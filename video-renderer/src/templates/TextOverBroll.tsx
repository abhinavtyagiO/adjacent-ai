import {AbsoluteFill, OffthreadVideo, staticFile} from "remotion";
import {BrandOverlay} from "../components/BrandOverlay";
import {Caption} from "../components/Caption";
import type {RenderProps} from "../types";

/** Play one continuous background video, preserving the requested cover or contain fit. */
export function TextOverBroll(props: RenderProps) {
  return <AbsoluteFill style={{backgroundColor: "#101010"}}>
    <OffthreadVideo
      src={staticFile(props.background_file ?? "background.mp4")}
      muted={props.audio === "SILENT"}
      style={{width: "100%", height: "100%", objectFit: props.fit}}
    />
    {props.captions.map(caption =>
      <Caption key={caption.start_frame} caption={caption} style={props.style}/>
    )}
    <BrandOverlay label={props.brand_label} cta={props.cta_overlay} frames={props.duration_frames}/>
  </AbsoluteFill>;
}
