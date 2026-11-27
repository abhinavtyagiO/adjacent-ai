/** Render a validated plan using staged files and a browser provisioned during setup. */
import {bundle} from "@remotion/bundler";
import {renderMedia, selectComposition} from "@remotion/renderer";
import {readFile, stat} from "node:fs/promises";
import {join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {RenderProps} from "./types";

const [input, output, publicDir, workDir, browser, concurrency] = process.argv.slice(2);
if (!input || !output || !publicDir || !workDir || !browser || !concurrency) {
  throw new Error("Expected plan, output, assets, work directory, browser and concurrency");
}
const props = JSON.parse(await readFile(input, "utf8")) as RenderProps;
if (props.schema_version !== 1 || props.template_version !== 1) {
  throw new Error("Unsupported video plan");
}
const compositionId = {TEXT_OVER_BROLL: "TextOverBroll", PRODUCT_REVEAL: "ProductReveal"}[props.template];
if (!compositionId) throw new Error("Unsupported template");
const files = props.template === "TEXT_OVER_BROLL"
  ? [props.background_file]
  : props.product_files.map(scene => scene.file);
if (files.length === 0 || files.length > 5) throw new Error("Missing local media");
for (const file of files) {
  if (!file || !/^(background\.mp4|product-[0-4]\.(jpg|png|webp))$/.test(file)
      || !(await stat(join(publicDir, file))).isFile()) {
    throw new Error("Media must be staged locally");
  }
}
const location = await bundle({
  entryPoint: fileURLToPath(new URL("./Root.tsx", import.meta.url)),
  publicDir: resolve(publicDir),
  outDir: join(workDir, "bundle"),
  enableCaching: false,
});
const browserOptions = {browserExecutable: browser, logLevel: "error" as const};
const composition = await selectComposition({
  ...browserOptions, serveUrl: location, id: compositionId, inputProps: props,
});
await renderMedia({
  ...browserOptions, serveUrl: location, composition, inputProps: props,
  outputLocation: output, codec: "h264", pixelFormat: "yuv420p", colorSpace: "bt709",
  muted: props.audio === "SILENT", audioCodec: "aac",
  concurrency: Number(concurrency), x264Preset: "veryfast", crf: 20,
  overwrite: false, disallowParallelEncoding: true,
});
