// Runs the browser OCR core against the real models with onnxruntime-node.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { PNG } = require("pngjs");
const ort = require("onnxruntime-node");
const core = require("../../scanner/frontend/ocr-core.js");

const FRONTEND = path.join(__dirname, "../../scanner/frontend");

test("reads the synthetic receipt", async () => {
  const png = PNG.sync.read(fs.readFileSync(path.join(__dirname, "../data/receipt.png")));
  const img = { data: png.data, width: png.width, height: png.height };
  const sessions = {
    det: await ort.InferenceSession.create(path.join(FRONTEND, "models/det.onnx")),
    rec: await ort.InferenceSession.create(path.join(FRONTEND, "models/rec_en.onnx")),
  };
  const chars = core.parseDict(fs.readFileSync(path.join(FRONTEND, "models/rec_en_dict.txt"), "utf8"));
  assert.strictEqual(chars.length, 436);
  const res = await core.runOCR(ort, sessions, chars, img, { detLimit: 960 });
  const texts = res.lines.map((l) => l.text);
  console.log(`  ${res.lines.length} lines, det ${res.timing.det.toFixed(0)}ms rec ${res.timing.rec.toFixed(0)}ms`);
  for (const want of ["FRESH MART", "DATE: 10/21/2026", "TOTAL", "41.51", "6604 COFFEE BEANS", "ITEMS SOLD: 15"]) {
    assert.ok(texts.includes(want), `missing "${want}" in ${JSON.stringify(texts)}`);
  }
  const milk = res.lines.find((l) => l.text === "0712 WHOLE MILK 1GAL");
  assert.deepStrictEqual(milk.words.map((w) => w.text), ["0712", "WHOLE", "MILK", "1GAL"]);
  // word boxes are ordered, inside the line box and non-overlapping-ish
  for (let i = 0; i < milk.words.length; i++) {
    const w = milk.words[i];
    assert.ok(w.x0 >= milk.box[0] - 0.5 && w.x1 <= milk.box[2] + 0.5 && w.x1 > w.x0);
    if (i) assert.ok(w.x0 >= milk.words[i - 1].x1 - 6, "words overlap");
  }
  console.log("  word boxes:", milk.words.map((w) => `${w.text}[${w.x0.toFixed(0)}-${w.x1.toFixed(0)}]`).join(" "));
});

for (const angle of [4, -6]) {
  test(`reads a receipt tilted ${angle} degrees`, async () => {
    const png = PNG.sync.read(fs.readFileSync(path.join(__dirname, `../data/receipt_rot${angle}.png`)));
    const img = { data: png.data, width: png.width, height: png.height };
    const sessions = {
      det: await ort.InferenceSession.create(path.join(FRONTEND, "models/det.onnx")),
      rec: await ort.InferenceSession.create(path.join(FRONTEND, "models/rec_en.onnx")),
    };
    const chars = core.parseDict(fs.readFileSync(path.join(FRONTEND, "models/rec_en_dict.txt"), "utf8"));
    const res = await core.runOCR(ort, sessions, chars, img, { detLimit: 1088 });
    // the detector may box words or whole lines depending on resolution, so compare words
    const words = new Set(res.lines.flatMap((l) => l.text.split(" ")));
    const want = "FRESH MART 123 MAIN STREET 0712 WHOLE MILK 1GAL 6604 COFFEE BEANS SUBTOTAL 39.53 41.51 ITEMS SOLD: 15 THANK YOU FOR SHOPPING 10/21/2026 14:35 5.97 2.50".split(" ");
    const missing = want.filter((w) => !words.has(w));
    const angles = res.lines.filter((l) => l.text.length >= 6).map((l) => (l.angle * 180) / Math.PI).sort((x, y) => x - y);
    const median = angles[angles.length >> 1];
    assert.ok(Math.abs(median + angle) < 1, `estimated tilt ${median.toFixed(1)} for an image rotated ${angle}`);
    console.log(`  ${angle}deg: ${res.lines.length} lines, missing ${JSON.stringify(missing)}`);
    assert.deepStrictEqual(missing, []);
  });
}
