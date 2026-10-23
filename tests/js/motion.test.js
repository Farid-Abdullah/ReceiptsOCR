// The motion tracker must follow slow sub-pixel drift without losing it to rounding, and flag jumps.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { PNG } = require("pngjs");
const { MotionTracker } = require("../../scanner/frontend/motion.js");

const png = PNG.sync.read(fs.readFileSync(path.join(__dirname, "../data/receipt.png")));
const SCALE = 1.55; // receipt px -> camera px, like the test videos
const CAM_W = 1280, CAM_H = 720, TW = 192, TH = Math.round((TW * CAM_H) / CAM_W);

function scene(x, y) {
  // camera px -> receipt px; dark table outside the receipt
  const rx = x / SCALE, ry = y / SCALE;
  if (rx < 0 || ry < 0 || rx >= png.width - 1 || ry >= png.height - 1) return 60;
  const x0 = rx | 0, y0 = ry | 0, fx = rx - x0, fy = ry - y0;
  const g = (xx, yy) => { const i = (yy * png.width + xx) * 4; return 0.299 * png.data[i] + 0.587 * png.data[i + 1] + 0.114 * png.data[i + 2]; };
  return (g(x0, y0) * (1 - fx) + g(x0 + 1, y0) * fx) * (1 - fy) + (g(x0, y0 + 1) * (1 - fx) + g(x0 + 1, y0 + 1) * fx) * fy;
}

// Thumbnail of the camera view whose top-left is at camera px (cx, cy) of the scene.
function thumb(cx, cy) {
  const s = CAM_W / TW, out = new Float32Array(TW * TH);
  for (let j = 0; j < TH; j++) {
    for (let i = 0; i < TW; i++) {
      let acc = 0;
      for (let k = 0; k < 3; k++) for (let l = 0; l < 3; l++) acc += scene(cx + (i + (k + 0.5) / 3) * s, cy + (j + (l + 0.5) / 3) * s);
      out[j * TW + i] = acc / 9;
    }
  }
  return out;
}

test("follows slow drift with sub-pixel accuracy", () => {
  const m = new MotionTracker(TW);
  const cx = 0;
  let cy = 150;
  m.push(thumb(cx, cy), TH);
  for (let k = 0; k < 120; k++) {
    cy += 1.4; // camera moves down 1.4 camera px per frame (content moves up)
    m.push(thumb(cx + Math.sin(k / 7) * 3, cy), TH);
  }
  const trueShift = -(cy - 150) * (TW / CAM_W); // content shift in thumbnail px
  console.log(`  true ${trueShift.toFixed(2)} thumb px, estimated ${m.G.y.toFixed(2)} (x ${m.G.x.toFixed(2)})`);
  assert.ok(Math.abs(m.G.y - trueShift) < 1.0, "accumulated vertical shift off by more than 1 thumbnail px");
  assert.strictEqual(m.breaks, 0);
});

for (const target of [400, 800, 1250, 1500]) {
  test(`flags a jump to another part of the receipt (y=${target})`, () => {
    const m = new MotionTracker(TW);
    m.push(thumb(0, 100), TH);
    m.push(thumb(0, 102), TH);
    assert.strictEqual(m.breaks, 0);
    m.push(thumb(0, target), TH);
    m.push(thumb(0, target + 1.5), TH); // a jump is confirmed by the next frame
    assert.strictEqual(m.breaks, 1);
  });
}

test("ignores a single glitched frame", () => {
  const m = new MotionTracker(TW);
  m.push(thumb(0, 100), TH);
  m.push(thumb(0, 101.4), TH);
  m.push(thumb(0, 900), TH); // e.g. a blurred or glare frame that matches nothing
  m.push(thumb(0, 102.8), TH);
  m.push(thumb(0, 104.2), TH);
  assert.strictEqual(m.breaks, 0);
  assert.ok(Math.abs(m.G.y - -(4.2 * TW) / CAM_W) < 0.5, `G.y = ${m.G.y}`);
});
