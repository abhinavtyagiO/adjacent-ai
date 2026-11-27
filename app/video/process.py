"""Bounded local FFmpeg/FFprobe operations for original files and derivatives."""

import json
import logging
import math
import os
import signal
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from app.errors import DomainError

logger = logging.getLogger(__name__)
INPUT = [
    "-protocol_whitelist",
    "file,pipe",
    "-format_whitelist",
    "mov,matroska,webm,png_pipe,jpeg_pipe,webp_pipe",
]
MAX_PIXELS = 4096 * 4096


def run_local(args, timeout, error_code, message, *, cwd=None, env=None):
    """Run an argument vector and stop the entire subprocess group on timeout."""
    try:
        with (
            tempfile.TemporaryFile() as output,
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=cwd,
                env=env,
            ) as process,
        ):
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise DomainError(error_code + "_TIMEOUT", message, 422) from exc
            if status or output.tell() > 1024 * 1024:
                raise DomainError(error_code, message, 422)
            output.seek(0)
            return output.read()
    except FileNotFoundError as exc:
        raise DomainError(
            "MEDIA_TOOL_MISSING", "A required media tool is unavailable", 503
        ) from exc


class MediaProcessor:
    """Inspect, normalize and validate media with bounded local tools."""

    def __init__(self, timeout: int):
        self.timeout = timeout

    def probe(self, path: Path) -> dict:
        raw = run_local(
            [
                "ffprobe",
                "-v",
                "error",
                *INPUT,
                "-show_streams",
                "-show_format",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
                "sample_aspect_ratio,duration:stream_side_data=rotation:format=format_name,duration:"
                "stream_disposition=attached_pic:format_tags=major_brand",
                "-of",
                "json",
                str(path),
            ],
            self.timeout,
            "INVALID_MEDIA",
            "The file is not a supported, readable video or image",
        )
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise DomainError("INVALID_MEDIA", "Media metadata could not be read", 422) from exc

    def ffmpeg(self, path, *args, code="INVALID_MEDIA"):
        run_local(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-threads",
                "2",
                "-filter_threads",
                "2",
                *INPUT,
                "-i",
                str(path),
                *map(str, args),
            ],
            self.timeout,
            code,
            "Media could not be decoded or processed",
        )

    @staticmethod
    def describe(raw: dict, size: int) -> dict:
        streams = raw.get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video"]
        if len(video) != 1 or video[0].get("disposition", {}).get("attached_pic"):
            raise DomainError("INVALID_MEDIA", "Supply one video or image stream", 422)
        stream = video[0]
        details = raw.get("format", {})
        formats = details.get("format_name", "").split(",")
        image = any(f.endswith("_pipe") for f in formats)
        angle = next(
            (x["rotation"] for x in stream.get("side_data_list", []) if "rotation" in x), 0
        )
        try:
            width, height = int(stream["width"]), int(stream["height"])
            duration = None if image else float(stream.get("duration") or details["duration"])
            fps = None if image else float(Fraction(stream["avg_frame_rate"]))
            if not (16 <= width <= 4096 and 16 <= height <= 4096 and width * height <= MAX_PIXELS):
                raise ValueError
            if not image and (
                not math.isfinite(duration) or not 0 < duration <= 180 or not 0 < fps <= 120
            ):
                raise ValueError
        except (KeyError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise DomainError(
                "MEDIA_LIMITS", "Media dimensions, duration or frame rate are unsupported", 422
            ) from exc
        if angle % 180:
            width, height = height, width
        codec = stream.get("codec_name")
        extension = (
            {"mjpeg": "jpg", "png": "png", "webp": "webp"}.get(codec)
            if image
            else ("mov" if details.get("tags", {}).get("major_brand") == "qt  " else "mp4")
            if "mov" in formats
            else "webm"
        )
        if extension is None:
            raise DomainError("INVALID_MEDIA", "Supply a JPEG, PNG, WebP, MP4, MOV or WebM", 422)
        return {
            "media_type": "IMAGE" if image else "VIDEO",
            "width": width,
            "height": height,
            "rotation": angle,
            "orientation": "PORTRAIT"
            if height > width
            else "LANDSCAPE"
            if width > height
            else "SQUARE",
            "duration_seconds": duration,
            "frame_rate": fps,
            "codec": codec,
            "pixel_format": stream.get("pix_fmt"),
            "file_bytes": size,
            "extension": extension,
            "audio_streams": sum(s.get("codec_type") == "audio" for s in streams),
            "audio_codecs": [
                s.get("codec_name") for s in streams if s.get("codec_type") == "audio"
            ],
            "sample_aspect_ratio": stream.get("sample_aspect_ratio"),
        }

    def thumbnail(self, path: Path, output: Path):
        self.ffmpeg(
            path,
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            "scale=320:320:force_original_aspect_ratio=decrease",
            "-q:v",
            "3",
            "-update",
            "1",
            output,
            code="THUMBNAIL_FAILED",
        )

    def process(self, path: Path, directory: Path):
        """Validate the whole video and normalize only when compatibility requires it."""
        started = time.monotonic()
        details = self.describe(self.probe(path), path.stat().st_size)
        derivative = None
        if details["media_type"] == "VIDEO":
            compatible = (
                details["codec"] == "h264"
                and details["pixel_format"] == "yuv420p"
                and details["rotation"] == 0
                and details["sample_aspect_ratio"] in (None, "N/A", "1:1")
                and set(details["audio_codecs"]) <= {"aac"}
                and details["audio_streams"] <= 1
            )
            if compatible and details["extension"] == "mp4":
                self.ffmpeg(path, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-")
            elif compatible and details["extension"] == "mov":
                derivative = directory / "normalized.mp4"
                self.ffmpeg(
                    path,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    derivative,
                    code="NORMALIZATION_FAILED",
                )
                self.ffmpeg(derivative, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-")
            else:
                derivative = directory / "normalized.mp4"
                self.ffmpeg(
                    path,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-map_metadata",
                    "-1",
                    "-vf",
                    "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "fast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    derivative,
                    code="NORMALIZATION_FAILED",
                )
            if derivative:
                details["normalized"] = self.describe(
                    self.probe(derivative), derivative.stat().st_size
                )
                logger.info("media_normalized codec=%s", details["codec"])
        thumb = directory / "thumbnail.jpg"
        self.thumbnail(derivative or path, thumb)
        details["processing_seconds"] = round(time.monotonic() - started, 3)
        logger.info(
            "media_probed type=%s bytes=%s seconds=%s",
            details["media_type"],
            details["file_bytes"],
            details["processing_seconds"],
        )
        return details, derivative, thumb

    def validate_render(self, path: Path, duration_frames: int, audio: bool) -> dict:
        if not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024 * 1024:
            raise DomainError(
                "INVALID_OUTPUT", "The rendered file is absent or exceeds its limit", 422
            )
        raw = self.probe(path)
        details = self.describe(raw, path.stat().st_size)
        if (
            "mp4" not in raw["format"]["format_name"].split(",")
            or (details["width"], details["height"], details["frame_rate"]) != (1080, 1920, 30)
            or details["rotation"]
            or details["codec"] != "h264"
            or details["pixel_format"] != "yuv420p"
            or details["audio_streams"] != int(audio)
            or (audio and details["audio_codecs"] != ["aac"])
            or abs(details["duration_seconds"] * 30 - duration_frames) > 1.01
        ):
            raise DomainError(
                "INVALID_OUTPUT",
                "Rendered video does not match the VideoSpec",
                422,
                details={
                    "expected_duration_frames": duration_frames,
                    "expected_audio": audio,
                    "actual": details,
                },
            )
        self.ffmpeg(
            path, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-", code="OUTPUT_DECODE_FAILED"
        )
        return {"validated": True, "decoded": True, "media": details, "ffprobe": raw}
