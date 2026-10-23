// Run the app's OCR code (scanner/frontend/ocr-core.js) on photos in Node, with the same models the
// phone downloads, to check what it reads from any photo.
//
//   node ocr_cli.js [options] photo.jpeg [...]        prints JSON: { "photo.jpeg": { lines, ... } }
//
//   --det-limit N   longest side the detector sees (default 1280, like the app); 0 = no shrinking
//   --crop T,B      keep only the band from T to B (fractions of the height), like a close-up photo
//   --scale X       enlarge the photo X times before OCR (helps small print)
//   --det PATH      detection model    (default scanner/frontend/models/det.onnx)
//   --rec PATH      recognition model  (default scanner/frontend/models/rec_en.onnx)
//   --dict PATH     recognition dictionary matching --rec
const fs = require("fs");
const path = require("path");
const jpeg = require("jpeg-js");
const ort = require("onnxruntime-node");
const core = require("../../scanner/frontend/ocr-core.js");

const MODELS = path.join(__dirname, "../../scanner/frontend/models");

function parseArgs(argv) {
  const opts = { detLimit: 1280, scale: 1, det: path.join(MODELS, "det.onnx"), rec: path.join(MODELS, "rec_en.onnx"),
    dict: path.join(MODELS, "rec_en_dict.txt"), images: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--det-limit") opts.detLimit = +argv[++i];
    else if (a === "--scale") opts.scale = +argv[++i];
    else if (a === "--crop") opts.crop = argv[++i].split(",").map(Number);
    else if (a === "--det") opts.det = argv[++i];
    else if (a === "--rec") opts.rec = argv[++i];
    else if (a === "--dict") opts.dict = argv[++i];
    else opts.images.push(a);
  }
  return opts;
}

// The band of rows from top to bottom (fractions of the height).
function crop(img, [top, bottom]) {
  const y0 = Math.round(img.height * top), y1 = Math.round(img.height * bottom);
  return { data: img.data.slice(y0 * img.width * 4, y1 * img.width * 4), width: img.width, height: y1 - y0 };
}

// Bilinear resize of an RGBA image.
function resize(img, scale) {
  if (scale === 1) return img;
  const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    const sy = Math.min(img.height - 1, Math.max(0, (y + 0.5) / scale - 0.5));
    const y0 = sy | 0, y1 = Math.min(y0 + 1, img.height - 1), wy = sy - y0;
    for (let x = 0; x < w; x++) {
      const sx = Math.min(img.width - 1, Math.max(0, (x + 0.5) / scale - 0.5));
      const x0 = sx | 0, x1 = Math.min(x0 + 1, img.width - 1), wx = sx - x0;
      for (let c = 0; c < 4; c++) {
        const a = img.data[(y0 * img.width + x0) * 4 + c], b = img.data[(y0 * img.width + x1) * 4 + c];
        const d = img.data[(y1 * img.width + x0) * 4 + c], e = img.data[(y1 * img.width + x1) * 4 + c];
        out[(y * w + x) * 4 + c] = (a + (b - a) * wx) * (1 - wy) + (d + (e - d) * wx) * wy;
      }
    }
  }
  return { data: out, width: w, height: h };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const sessions = {
    det: await ort.InferenceSession.create(opts.det),
    rec: await ort.InferenceSession.create(opts.rec),
  };
  const chars = core.parseDict(fs.readFileSync(opts.dict, "utf8"));
  const result = {};
  for (const file of opts.images) {
    const decoded = jpeg.decode(fs.readFileSync(file), { useTArray: true, formatAsRGBA: true });
    let img = { data: decoded.data, width: decoded.width, height: decoded.height };
    if (opts.crop) img = crop(img, opts.crop);
    img = resize(img, opts.scale);
    const detLimit = opts.detLimit > 0 ? opts.detLimit : Math.max(img.width, img.height);
    const res = await core.runOCR(ort, sessions, chars, img, { detLimit });
    result[path.basename(file)] = {
      width: img.width, height: img.height, scale: opts.scale, detLimit,
      timing: res.timing,
      lines: res.lines.map((l, i) => ({
        id: `l${i}`, text: l.text, conf: +l.conf.toFixed(4), minConf: +l.minConf.toFixed(4),
        box: l.box.map((v) => Math.round(v * 10) / 10), angle: +l.angle.toFixed(4),
      })),
    };
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
