"""Private object storage. The renderer receives local copies, never credentials or URLs."""

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol

from app.errors import DomainError


class StorageProvider(Protocol):
    def write(self, key: str, source: BinaryIO) -> None: ...
    def read(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def size(self, key: str) -> int: ...
    def stream(self, key: str, start: int = 0, length: int | None = None) -> Iterator[bytes]: ...


class LocalStorage:
    """Store objects atomically under a dedicated private directory."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", part) for part in parts
        ):
            raise DomainError("INVALID_OBJECT_KEY", "Invalid storage key")
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise DomainError("INVALID_OBJECT_KEY", "Storage key is outside its root")
        return path

    def write(self, key: str, source: BinaryIO) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            try:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
                handle.flush()
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)

    def read(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as error:
            raise DomainError(
                "MEDIA_FILE_MISSING", "The private object is unavailable", 404
            ) from error

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def size(self, key: str) -> int:
        with self.read(key) as handle:
            return os.fstat(handle.fileno()).st_size

    def stream(self, key: str, start: int = 0, length: int | None = None) -> Iterator[bytes]:
        with self.read(key) as handle:
            handle.seek(start)
            while length is None or length > 0:
                size = min(256 * 1024, length) if length is not None else 256 * 1024
                chunk = handle.read(size)
                if not chunk:
                    return
                yield chunk
                if length is not None:
                    length -= len(chunk)
