import {useCurrentFrame} from "remotion";

/** Brand label and approved CTA are separate from the timed caption. */
export function BrandOverlay({label, cta, frames}: {
  label: string | null; cta: string | null; frames: number;
}) {
  const frame = useCurrentFrame();
  const style = {
    position: "absolute" as const, left: 96, right: 96, textAlign: "center" as const,
    color: "white", font: "bold 38px Arial, sans-serif", textShadow: "0 1px 8px black",
  };
  return <>
    {label && <div style={{...style, top: 80}}>{label}</div>}
    {cta && frame >= frames - 90 && <div style={{...style, bottom: 80}}>{cta}</div>}
  </>;
}
