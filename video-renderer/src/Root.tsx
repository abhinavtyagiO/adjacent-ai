import {Composition, registerRoot} from "remotion";
import {ProductReveal} from "./templates/ProductReveal";
import {TextOverBroll} from "./templates/TextOverBroll";
import type {RenderProps} from "./types";

const defaults: RenderProps = {
  schema_version: 1, template_version: 1, template: "TEXT_OVER_BROLL",
  width: 1080, height: 1920, fps: 30, duration_frames: 450, fit: "cover",
  audio: "SILENT", style: {
    position: "BOTTOM", font_size: 66, color: "#FFFFFF",
    backdrop: "#101010", safe_margin: 96,
  },
  captions: [], brand_label: null, cta_overlay: null, background_file: "background.mp4",
  product_files: [],
};

function Root() {
  const metadata = ({props}: {props: RenderProps}) => ({durationInFrames: props.duration_frames});
  return <>
    <Composition id="TextOverBroll" component={TextOverBroll}
      width={1080} height={1920} fps={30} durationInFrames={450}
      defaultProps={defaults} calculateMetadata={metadata}/>
    <Composition id="ProductReveal" component={ProductReveal}
      width={1080} height={1920} fps={30} durationInFrames={450}
      defaultProps={{...defaults, template: "PRODUCT_REVEAL", background_file: null}}
      calculateMetadata={metadata}/>
  </>;
}

registerRoot(Root);
