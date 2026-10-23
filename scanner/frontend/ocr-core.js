// OCR pipeline core: PP-OCR detection (DBNet) + recognition (SVTR/CTC) pre/post-processing.
// Pure functions over RGBA pixel buffers so the same file runs in the Web Worker and in Node tests.
// Mirrors tests/ref_ocr.py.
(function (root) {
  "use strict";

  const DET_MEAN = [0.485, 0.456, 0.406];
  const DET_STD = [0.229, 0.224, 0.225];
  const REC_H = 48;
  const REC_MAX_W = 1280;

  // Bilinear sample of an RGBA image into a CHW float tensor (BGR channel order like PaddleOCR).
  // (sx, sy, sw, sh) is the source rectangle; output is outW x outH written at width `stride`.
  function sampleToCHW(img, sx, sy, sw, sh, outW, outH, stride, out, mean, std) {
    const { data, width, height } = img;
    const plane = stride * outH;
    const fx = sw / outW, fy = sh / outH;
    for (let y = 0; y < outH; y++) {
      let syf = sy + (y + 0.5) * fy - 0.5;
      if (syf < 0) syf = 0;
      if (syf > height - 1) syf = height - 1;
      const y0 = syf | 0, y1 = Math.min(y0 + 1, height - 1), wy = syf - y0;
      for (let x = 0; x < outW; x++) {
        let sxf = sx + (x + 0.5) * fx - 0.5;
        if (sxf < 0) sxf = 0;
        if (sxf > width - 1) sxf = width - 1;
        const x0 = sxf | 0, x1 = Math.min(x0 + 1, width - 1), wx = sxf - x0;
        const i00 = (y0 * width + x0) * 4, i01 = (y0 * width + x1) * 4;
        const i10 = (y1 * width + x0) * 4, i11 = (y1 * width + x1) * 4;
        const o = y * stride + x;
        for (let c = 0; c < 3; c++) {
          const src = 2 - c; // BGR
          const top = data[i00 + src] + (data[i01 + src] - data[i00 + src]) * wx;
          const bot = data[i10 + src] + (data[i11 + src] - data[i10 + src]) * wx;
          const v = (top + (bot - top) * wy) / 255;
          out[c * plane + o] = (v - mean[c]) / std[c];
        }
      }
    }
  }

  function detInput(img, limit) {
    const s = Math.min(1, limit / Math.max(img.width, img.height));
    const w = Math.max(32, Math.round((img.width * s) / 32) * 32);
    const h = Math.max(32, Math.round((img.height * s) / 32) * 32);
    const data = new Float32Array(3 * w * h);
    sampleToCHW(img, 0, 0, img.width, img.height, w, h, w, data, DET_MEAN, DET_STD);
    return { data, w, h };
  }

  // DB post-processing: threshold, 4-connected components, rotated boxes, unclip.
  // Each box: rot {cx, cy, len, thick, angle} (the text line as a rotated rectangle, image px) plus a
  // "deskewed" axis box x0..x1 (projected length) by y0..y1 (centre +- thick/2) used for tracking.
  function detBoxes(prob, w, h, imgW, imgH, opts) {
    const thresh = opts.thresh ?? 0.3, boxThresh = opts.boxThresh ?? 0.6, unclip = opts.unclip ?? 1.6;
    const n = w * h;
    const seen = new Uint8Array(n);
    const stack = new Int32Array(n);
    const pix = new Int32Array(n); // pixels of all accepted components, back to back
    const sxr = imgW / w, syr = imgH / h;
    const comps = [];
    let pc = 0;
    for (let start = 0; start < n; start++) {
      if (seen[start] || prob[start] <= thresh) continue;
      const first = pc;
      let sp = 0, count = 0, sum = 0, minY = h, maxY = -1;
      let mx = 0, my = 0, mxx = 0, myy = 0, mxy = 0;
      stack[sp++] = start;
      seen[start] = 1;
      while (sp) {
        const p = stack[--sp];
        const py = (p / w) | 0, px = p - py * w;
        pix[pc++] = p;
        count++;
        sum += prob[p];
        if (py < minY) minY = py;
        if (py > maxY) maxY = py;
        const x = (px + 0.5) * sxr, y = (py + 0.5) * syr;
        mx += x; my += y; mxx += x * x; myy += y * y; mxy += x * y;
        if (px > 0 && !seen[p - 1] && prob[p - 1] > thresh) { seen[p - 1] = 1; stack[sp++] = p - 1; }
        if (px < w - 1 && !seen[p + 1] && prob[p + 1] > thresh) { seen[p + 1] = 1; stack[sp++] = p + 1; }
        if (py > 0 && !seen[p - w] && prob[p - w] > thresh) { seen[p - w] = 1; stack[sp++] = p - w; }
        if (py < h - 1 && !seen[p + w] && prob[p + w] > thresh) { seen[p + w] = 1; stack[sp++] = p + w; }
      }
      if (count < 6 || maxY - minY + 1 < 3 || sum / count < boxThresh) {
        pc = first;
        continue;
      }
      mx /= count; my /= count;
      const cxx = mxx / count - mx * mx, cyy = myy / count - my * my, cxy = mxy / count - mx * my;
      // principal axis of the blob; elongation = ratio of the two eigenvalues
      const tr = cxx + cyy, det = cxx * cyy - cxy * cxy;
      const disc = Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
      const l1 = tr / 2 + disc, l2 = Math.max(1e-6, tr / 2 - disc);
      comps.push({ first, last: pc, mx, my, angle: 0.5 * Math.atan2(2 * cxy, cxx - cyy), elong: l1 / l2, score: sum / count });
    }

    // Short blobs (single characters) have unreliable orientation: use the frame's dominant angle.
    const reliable = (c) => c.elong >= 9 && Math.abs(c.angle) < 0.6;
    const angles = comps.filter(reliable).map((c) => c.angle).sort((a, b) => a - b);
    const frameAngle = angles.length ? angles[angles.length >> 1] : 0;
    const boxes = [];
    for (const c of comps) {
      const angle = reliable(c) ? c.angle : frameAngle;
      const co = Math.cos(angle), si = Math.sin(angle);
      let umin = Infinity, umax = -Infinity, vmin = Infinity, vmax = -Infinity;
      for (let k = c.first; k < c.last; k++) {
        const p = pix[k], py = (p / w) | 0, px = p - py * w;
        const dx = (px + 0.5) * sxr - c.mx, dy = (py + 0.5) * syr - c.my;
        const u = dx * co + dy * si, v = -dx * si + dy * co;
        if (u < umin) umin = u;
        if (u > umax) umax = u;
        if (v < vmin) vmin = v;
        if (v > vmax) vmax = v;
      }
      let len = umax - umin + sxr, thick = vmax - vmin + syr;
      const uc = (umin + umax) / 2, vc = (vmin + vmax) / 2;
      const cx = c.mx + uc * co - vc * si, cy = c.my + uc * si + vc * co;
      const d = (len * thick * unclip) / (2 * (len + thick));
      len += 2 * d;
      thick += 2 * d;
      boxes.push({
        rot: { cx, cy, len, thick, angle },
        x0: Math.max(0, cx - (len / 2) * co),
        x1: Math.min(imgW, cx + (len / 2) * co),
        y0: Math.max(0, cy - thick / 2),
        y1: Math.min(imgH, cy + thick / 2),
        score: c.score,
      });
    }
    return boxes;
  }

  // Bilinear sample of a rotated rectangle of an RGBA image into a CHW tensor (BGR), like sampleToCHW.
  function sampleRotToCHW(img, rot, outW, outH, stride, out, mean, std) {
    const { data, width, height } = img;
    const plane = stride * outH;
    const co = Math.cos(rot.angle), si = Math.sin(rot.angle);
    for (let y = 0; y < outH; y++) {
      const v = ((y + 0.5) / outH - 0.5) * rot.thick;
      for (let x = 0; x < outW; x++) {
        const u = ((x + 0.5) / outW - 0.5) * rot.len;
        let sxf = rot.cx + u * co - v * si - 0.5, syf = rot.cy + u * si + v * co - 0.5;
        if (sxf < 0) sxf = 0;
        if (sxf > width - 1) sxf = width - 1;
        if (syf < 0) syf = 0;
        if (syf > height - 1) syf = height - 1;
        const x0 = sxf | 0, x1 = Math.min(x0 + 1, width - 1), wx = sxf - x0;
        const y0 = syf | 0, y1 = Math.min(y0 + 1, height - 1), wy = syf - y0;
        const i00 = (y0 * width + x0) * 4, i01 = (y0 * width + x1) * 4;
        const i10 = (y1 * width + x0) * 4, i11 = (y1 * width + x1) * 4;
        const o = y * stride + x;
        for (let c = 0; c < 3; c++) {
          const src = 2 - c; // BGR
          const top = data[i00 + src] + (data[i01 + src] - data[i00 + src]) * wx;
          const bot = data[i10 + src] + (data[i11 + src] - data[i10 + src]) * wx;
          out[c * plane + o] = ((top + (bot - top) * wy) / 255 - mean[c]) / std[c];
        }
      }
    }
  }

  // Resize a batch of (rotated) boxes to height 48 and pad to the widest one.
  function recBatchInput(img, boxes) {
    const widths = boxes.map((b) => Math.min(REC_MAX_W, Math.max(16, Math.ceil((REC_H * b.rot.len) / b.rot.thick))));
    const W = Math.ceil(Math.max(...widths) / 64) * 64; // few distinct shapes -> GPU kernels get reused
    const data = new Float32Array(boxes.length * 3 * REC_H * W); // zero == normalized mid-gray padding
    const mean = [0.5, 0.5, 0.5], std = [0.5, 0.5, 0.5];
    boxes.forEach((b, i) => {
      const view = data.subarray(i * 3 * REC_H * W, (i + 1) * 3 * REC_H * W);
      sampleRotToCHW(img, b.rot, widths[i], REC_H, W, view, mean, std);
    });
    return { data, W, widths };
  }

  // Greedy CTC decode of one sequence, with word boxes from character timesteps.
  // probs: Float32Array [T * C] (already softmaxed). Returns text, confidences and words.
  function ctcDecode(probs, T, C, chars, box, resizedW, paddedW) {
    const spaceIdx = C - 1;
    const step = paddedW / T; // input pixels per timestep
    // timestep -> position along the line -> x (words are reported by their x extent)
    const r = box.rot;
    const toX = (t) => r.cx + ((t * step) / resizedW - 0.5) * r.len * Math.cos(r.angle);
    let text = "", prev = 0;
    const confs = [];
    const words = [];
    let cur = null;
    for (let t = 0; t < T; t++) {
      let best = 0, bestP = -1;
      const off = t * C;
      for (let c = 0; c < C; c++) {
        const p = probs[off + c];
        if (p > bestP) { bestP = p; best = c; }
      }
      if (best !== 0 && best !== prev) {
        const ch = best === spaceIdx ? " " : chars[best - 1] ?? "";
        text += ch;
        confs.push(bestP);
        if (ch === " ") {
          if (cur) { words.push(cur); cur = null; }
        } else {
          if (!cur) cur = { text: "", confs: [], t0: t, t1: t };
          cur.text += ch;
          cur.confs.push(bestP);
          cur.t1 = t;
        }
      }
      prev = best;
    }
    if (cur) words.push(cur);
    const out = words.map((w) => ({
      text: w.text,
      conf: Math.min(...w.confs),
      x0: Math.max(box.x0, toX(w.t0 - 0.6)),
      x1: Math.min(box.x1, toX(w.t1 + 1.6)),
    }));
    return {
      text: text.trim(),
      conf: confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : 0,
      minConf: confs.length ? Math.min(...confs) : 0,
      words: out,
    };
  }

  function parseDict(txt) {
    const lines = txt.split("\n").map((l) => l.replace(/\r$/, ""));
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines;
  }

  // Full pipeline. `ort` is the onnxruntime module (web or node), sessions created by the caller.
  async function runOCR(ort, sessions, chars, img, opts = {}) {
    const t0 = now();
    const det = detInput(img, opts.detLimit ?? 960);
    const detOut = await sessions.det.run({ x: new ort.Tensor("float32", det.data, [1, 3, det.h, det.w]) });
    const prob = Object.values(detOut)[0].data;
    let boxes = detBoxes(prob, det.w, det.h, img.width, img.height, opts);
    // receipts are roughly upright: drop vertical/degenerate boxes
    boxes = boxes.filter((b) => b.rot.len >= 0.6 * b.rot.thick && b.rot.thick >= 6);
    const t1 = now();

    // Boxes that land on text already confirmed in earlier frames do not need reading again,
    // except a few long ones kept as registration anchors.
    const skipped = [];
    if (opts.hints && opts.hints.length) {
      const matched = [];
      const rest = [];
      for (const b of boxes) {
        // same box, or a piece of a confirmed line (the detector splits lines differently per frame)
        const h = opts.hints.find((hint) => {
          const c = covers(b, hint.box);
          return c.ofBox >= 0.8 && c.ofHint >= 0.25;
        });
        if (h) matched.push({ b, id: h.id });
        else rest.push(b);
      }
      matched.sort((p, q) => (q.b.x1 - q.b.x0) - (p.b.x1 - p.b.x0));
      const keep = opts.anchors ?? 3;
      for (let i = 0; i < matched.length; i++) {
        if (i < keep) rest.push(matched[i].b);
        else skipped.push({ box: [matched[i].b.x0, matched[i].b.y0, matched[i].b.x1, matched[i].b.y1], skip: matched[i].id });
      }
      boxes = rest;
    }

    const aspect = (b) => b.rot.len / b.rot.thick;
    const order = boxes.map((b, i) => i).sort((a, b) => aspect(boxes[a]) - aspect(boxes[b]));
    const batchSize = opts.recBatch ?? 6;
    const lines = new Array(boxes.length);
    for (let s = 0; s < order.length; s += batchSize) {
      const idx = order.slice(s, s + batchSize);
      const batch = idx.map((i) => boxes[i]);
      const rec = recBatchInput(img, batch);
      const recOut = await sessions.rec.run({
        x: new ort.Tensor("float32", rec.data, [batch.length, 3, REC_H, rec.W]),
      });
      const out = Object.values(recOut)[0];
      const [, T, C] = out.dims;
      idx.forEach((boxIdx, k) => {
        const probs = out.data.subarray(k * T * C, (k + 1) * T * C);
        const dec = ctcDecode(probs, T, C, chars, boxes[boxIdx], rec.widths[k], rec.W);
        const b = boxes[boxIdx];
        lines[boxIdx] = { box: [b.x0, b.y0, b.x1, b.y1], angle: b.rot.angle, ...dec };
      });
    }
    return {
      lines: lines.filter((l) => l && l.text.length > 0).concat(skipped),
      timing: { det: t1 - t0, rec: now() - t1, total: now() - t0, read: boxes.length, skipped: skipped.length },
      detSize: [det.w, det.h],
    };
  }

  function covers(b, h) {
    const ix = Math.min(b.x1, h[2]) - Math.max(b.x0, h[0]);
    const iy = Math.min(b.y1, h[3]) - Math.max(b.y0, h[1]);
    if (ix <= 0 || iy <= 0) return { ofBox: 0, ofHint: 0 };
    const inter = ix * iy;
    return { ofBox: inter / ((b.x1 - b.x0) * (b.y1 - b.y0)), ofHint: inter / ((h[2] - h[0]) * (h[3] - h[1])) };
  }

  function now() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  const api = { detInput, detBoxes, recBatchInput, ctcDecode, parseDict, runOCR, sampleToCHW, sampleRotToCHW };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.OCRCore = api;
})(typeof self !== "undefined" ? self : this);
