/** The worker validates these fields with VideoPlan before passing them to Remotion. */
export type FrameText = {text: string; start_frame: number; end_frame: number};
export type RenderProps = {
  schema_version: 1;
  template_version: 1;
  template: "TEXT_OVER_BROLL" | "PRODUCT_REVEAL";
  width: 1080;
  height: 1920;
  fps: 30;
  duration_frames: number;
  fit: "cover" | "contain";
  audio: "SILENT" | "BACKGROUND";
  style: {
    position: "TOP" | "CENTER" | "BOTTOM";
    font_size: number;
    color: string;
    backdrop: string;
    safe_margin: number;
  };
  captions: FrameText[];
  brand_label: string | null;
  cta_overlay: string | null;
  background_file: string | null;
  product_files: {file: string; start_frame: number; end_frame: number}[];
};
