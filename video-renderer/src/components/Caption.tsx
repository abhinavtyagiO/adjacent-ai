import {AbsoluteFill, interpolate, useCurrentFrame} from "remotion";
import type {FrameText, RenderProps} from "../types";

/** Render one approved caption with small entrance and exit fades. */
export function Caption({caption, style}: {caption: FrameText; style: RenderProps["style"]}) {
  const frame = useCurrentFrame();
  if (frame < caption.start_frame || frame >= caption.end_frame) return null;
  const fade = Math.min(6, (caption.end_frame - caption.start_frame) / 4);
  const opacity = interpolate(
    frame, [caption.start_frame, caption.start_frame + fade,
      caption.end_frame - fade, caption.end_frame], [0, 1, 1, 0],
    {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
  );
  return <AbsoluteFill style={{
    padding: `${style.safe_margin + 120}px ${style.safe_margin}px`,
    alignItems: "center",
    justifyContent: {TOP: "flex-start", CENTER: "center", BOTTOM: "flex-end"}[style.position],
    opacity,
  }}>
    <div style={{
      maxWidth: "100%", padding: "26px 38px", borderRadius: 24,
      background: style.backdrop, color: style.color, fontSize: style.font_size,
      fontFamily: "Arial, sans-serif", fontWeight: 700, lineHeight: 1.24,
      textAlign: "center", whiteSpace: "pre-wrap", overflowWrap: "anywhere",
      textShadow: "0 2px 6px #0005",
    }}>{caption.text}</div>
  </AbsoluteFill>;
}
