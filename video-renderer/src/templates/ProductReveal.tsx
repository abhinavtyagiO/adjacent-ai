import {AbsoluteFill, Img, interpolate, useCurrentFrame} from "remotion";
import {BrandOverlay} from "../components/BrandOverlay";
import {Caption} from "../components/Caption";
import {compositionMedia} from "../media-url";
import type {RenderProps} from "../types";

/** Pan and zoom only the uploaded product photos. Crossfade into each new photo. */
export function ProductReveal(props: RenderProps) {
  const frame = useCurrentFrame();
  const scenes = props.product_files;
  const index = scenes.findIndex(scene => frame >= scene.start_frame && frame < scene.end_frame);
  const current = scenes[index];
  const fade = current ? Math.min(10, (current.end_frame - current.start_frame) / 4) : 0;
  const blending = index > 0 && frame < current.start_frame + fade;
  const visible = current ? (blending ? [scenes[index - 1], current] : [current]) : [];
  return <AbsoluteFill style={{backgroundColor: "#101010", overflow: "hidden"}}>
    {visible.map((scene) => {
      const progress = interpolate(frame, [scene.start_frame, scene.end_frame], [0, 1], {
        extrapolateLeft: "clamp", extrapolateRight: "clamp",
      });
      const opacity = blending && scene === current ? (frame - current.start_frame) / fade : 1;
      return <AbsoluteFill key={scene.file} style={{opacity}}>
        <Img src={compositionMedia(scene.file)} style={{
          width: "100%", height: "100%", objectFit: props.fit,
          transform: `translateY(${(progress - 0.5) * 16}px) scale(${1.02 + progress * 0.08})`,
        }}/>
      </AbsoluteFill>;
    })}
    {props.captions.map(caption =>
      <Caption key={caption.start_frame} caption={caption} style={props.style}/>
    )}
    <BrandOverlay label={props.brand_label} cta={props.cta_overlay} frames={props.duration_frames}/>
  </AbsoluteFill>;
}
