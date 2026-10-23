// Simulates a camera sliding down the receipt: real OCR on each overlapping frame, then checks the
// tracker kept every line exactly once (and kept the two genuinely separate BANANAS items).
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { PNG } = require("pngjs");
const ort = require("onnxruntime-node");
const core = require("../../scanner/frontend/ocr-core.js");
const { ReceiptDoc, norm } = require("../../scanner/frontend/tracker.js");

const FRONTEND = path.join(__dirname, "../../scanner/frontend");

// Crop + scale an RGBA image (nearest-ish bilinear is fine for this).
function view(img, x, y, w, h, scale) {
  const ow = Math.round(w * scale), oh = Math.round(h * scale);
  const out = new Uint8ClampedArray(ow * oh * 4);
  for (let j = 0; j < oh; j++) {
    for (let i = 0; i < ow; i++) {
      const sx = Math.min(img.width - 1, Math.max(0, Math.round(x + i / scale)));
      const sy = Math.min(img.height - 1, Math.max(0, Math.round(y + j / scale)));
      const si = (sy * img.width + sx) * 4, di = (j * ow + i) * 4;
      if (y + j / scale >= img.height || y + j / scale < 0) {
        out[di] = 70; out[di + 1] = 58; out[di + 2] = 50; out[di + 3] = 255; // table
      } else {
        out[di] = img.data[si]; out[di + 1] = img.data[si + 1]; out[di + 2] = img.data[si + 2]; out[di + 3] = 255;
      }
    }
  }
  return { data: out, width: ow, height: oh };
}

async function setup() {
  const png = PNG.sync.read(fs.readFileSync(path.join(__dirname, "../data/receipt.png")));
  const img = { data: png.data, width: png.width, height: png.height };
  const sessions = {
    det: await ort.InferenceSession.create(path.join(FRONTEND, "models/det.onnx")),
    rec: await ort.InferenceSession.create(path.join(FRONTEND, "models/rec_en.onnx")),
  };
  const chars = core.parseDict(fs.readFileSync(path.join(FRONTEND, "models/rec_en_dict.txt"), "utf8"));
  const rot = PNG.sync.read(fs.readFileSync(path.join(__dirname, "../data/receipt_rot4.png")));
  return { img, rot4: { data: rot.data, width: rot.width, height: rot.height }, sessions, chars };
}

async function pan(ctx, { motionKnown, step, scaleAt, path: ys, jumps = [], hints: useHints, image }) {
  const img = image ? ctx[image] : ctx.img;
  const doc = new ReceiptDoc();
  const winH = 330; // receipt pixels visible per frame (about 8 text rows)
  let prev = null;
  const states = [];
  let skippedTotal = 0, readTotal = 0;
  if (!ys) { ys = []; for (let y = -40; y < img.height - winH + 60; y += step) ys.push(y); }
  for (let k = 0; k < ys.length; k++) {
    const y = ys[k];
    const scale = scaleAt ? scaleAt(k) : 1.4;
    const x = 0 + ((k * 7) % 9) - 4;
    const frame = view(img, x, y, img.width, winH, scale);
    // content shift in frame px since previous frame (what the motion estimator reports)
    let motion = { dx: 0, dy: 0 };
    if (motionKnown && prev) motion = { dx: (prev.x - x) * scale, dy: (prev.y - y) * scale };
    if (jumps.includes(k)) motion = null; // motion tracker broke (swipe / camera covered)
    let hints = [];
    if (useHints && motion && doc.state === "tracking") {
      const T = doc.T;
      hints = doc.hints({ s: T.s, tx: T.tx - T.s * motion.dx, ty: T.ty - T.s * motion.dy }, { w: frame.width, h: frame.height });
    }
    const res = await core.runOCR(ort, ctx.sessions, ctx.chars, frame, { detLimit: 960, hints });
    skippedTotal += res.timing.skipped;
    readTotal += res.timing.read;
    const r = doc.ingest(res.lines, motion, { w: frame.width, h: frame.height });
    states.push(r.state);
    prev = { x, y };
  }
  if (useHints) console.log(`  recognized ${readTotal} boxes, skipped ${skippedTotal} known ones`);
  return { doc, states };
}

// Group segments into printed rows (same idea as receipt_parser.group_rows) and join their text.
function rows(lines, keepSegments = false) {
  const out = [];
  // undo receipt tilt (like receipt_parser._deskew) so labels and prices share a row
  const angles = lines.filter((l) => l.box[2] - l.box[0] > 3 * (l.box[3] - l.box[1])).map((l) => l.angle || 0).sort((a, b) => a - b);
  const tilt = Math.tan(angles.length ? angles[angles.length >> 1] : 0);
  lines = lines.map((l) => {
    const shift = -((l.box[0] + l.box[2]) / 2) * tilt;
    return { ...l, box: [l.box[0], l.box[1] + shift, l.box[2], l.box[3] + shift] };
  });
  for (const l of [...lines].sort((a, b) => (a.box[1] + a.box[3]) - (b.box[1] + b.box[3]))) {
    const h = l.box[3] - l.box[1];
    const row = out.slice(-3).find((r) => Math.min(r.y1, l.box[3]) - Math.max(r.y0, l.box[1]) >= 0.5 * Math.min(h, r.y1 - r.y0));
    if (row) { row.segs.push(l); row.y0 = Math.min(row.y0, l.box[1]); row.y1 = Math.max(row.y1, l.box[3]); }
    else out.push({ y0: l.box[1], y1: l.box[3], segs: [l] });
  }
  const sorted = out.map((r) => r.segs.sort((a, b) => a.box[0] - b.box[0]).map((x) => norm(x.text)));
  return keepSegments ? sorted : sorted.map((segs) => segs.join(""));
}

const EXPECTED_ROWS = ["FRESH MART", "123 MAIN STREET", "SPRINGFIELD TEL 555-0142", "DATE: 10/21/2026 TIME: 14:35",
  "4011 BANANAS", "2 @ 0.59 1.18", "0712 WHOLE MILK 1GAL 3.49", "2231 BREAD WHEAT 2.99", "4011 BANANAS",
  "2 @ 0.59 1.18", "9921 EGGS LARGE 12CT 4.29", "5520 CHEDDAR CHEESE 5.79", "1180 APPLE JUICE", "3 @ 1.99 5.97",
  "3307 PASTA PENNE", "2 @ 1.25 2.50", "8812 TOMATO SAUCE 2.15", "6604 COFFEE BEANS 9.99", "SUBTOTAL 39.53",
  "TAX 5% 1.98", "TOTAL 41.51", "VISA 41.51", "ITEMS SOLD: 15", "THANK YOU FOR SHOPPING"].map(norm);

function range(a, b, step) {
  const out = [];
  for (let v = a; step > 0 ? v < b : v > b; v += step) out.push(v);
  return out;
}

let ctx;
test.before(async () => { ctx = await setup(); });

for (const [name, cfg] of [
  ["with motion estimate", { motionKnown: true, step: 60 }],
  ["text-only registration (no motion estimate)", { motionKnown: false, step: 45 }],
  ["with zoom drift", { motionKnown: true, step: 55, scaleAt: (k) => 1.3 + 0.012 * k }],
  ["tilted receipt (4 degrees)", { motionKnown: true, step: 45, image: "rot4", hints: true }],
  ["with skip hints", { motionKnown: true, step: 45, hints: true }],
  ["fast scan", { motionKnown: true, step: 110, lenient: true }],
  ["jump back to top without motion", { motionKnown: true, path: [...range(-40, 640, 60), -40, 20, 80, ...range(500, 980, 60)], jumps: [12, 15] }],
  ["back and forth", { motionKnown: true, path: [...range(-40, 600, 70), ...range(600, 100, -70), ...range(100, 980, 70)] }],
]) {
  test(`pan: ${name}`, async () => {
    const { doc, states } = await pan(ctx, cfg);
    const lines = doc.exportLines();
    const got = rows(lines);
    const lost = states.filter((st) => st === "lost").length;
    console.log(`  ${lines.length} segments in ${got.length} rows, lost frames ${lost}/${states.length}, green ${lines.filter((l) => l.ok).length}`);
    // green must mean correct: every confirmed segment is part of a real printed row
    for (const l of lines.filter((x) => x.ok)) {
      assert.ok(EXPECTED_ROWS.some((row) => row.includes(norm(l.text))), `green but wrong: "${l.text}"`);
    }
    if (cfg.lenient) {
      // rows seen in only one frame may be missing or red, but confirmed text may not be duplicated,
      // invented or reordered
      let k = 0;
      const fits = (segs, row) => {
        let pos = 0; // segments appear in order inside the printed row (some may be missing)
        for (const seg of segs) {
          const at = row.indexOf(seg, pos);
          if (at < 0) return false;
          pos = at + seg.length;
        }
        return true;
      };
      for (const segs of rows(lines.filter((x) => x.ok), true)) {
        const row = segs.join(" ");
        while (k < EXPECTED_ROWS.length && !fits(segs, EXPECTED_ROWS[k])) k++;
        assert.ok(k < EXPECTED_ROWS.length, `unexpected or out-of-order row "${row}"`);
        k++;
      }
      assert.ok(got.length >= EXPECTED_ROWS.length - 2, `only ${got.length} rows`);
      return;
    }
    // every printed row recovered once, in order, nothing extra (the two BANANAS rows are both real)
    assert.deepStrictEqual(got, EXPECTED_ROWS);
  });
}
