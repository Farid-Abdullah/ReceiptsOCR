// Global image motion between camera frames, on small grayscale thumbnails.
// Keeps the overlay glued to the paper between OCR results and seeds frame registration.
//
// Each frame is compared with a keyframe (not the previous frame) and refined to sub-pixel
// precision, so slow steady motion is not lost to per-frame rounding.
(function (root) {
  "use strict";

  const R = 8; // coarse search radius at half resolution (= +-16 thumbnail px)
  const REKEY = 6; // thumbnail px of drift before the current frame becomes the new keyframe

  function meanAbsDiff(a, b, w, h, dx, dy, margin, step) {
    let sum = 0, n = 0;
    for (let y = margin; y < h - margin; y += step) {
      const ra = y * w, rb = (y + dy) * w + dx;
      for (let x = margin; x < w - margin; x += step) {
        sum += Math.abs(a[ra + x] - b[rb + x]);
        n++;
      }
    }
    return n ? sum / n : Infinity;
  }

  function halve(src, w, h) {
    const w2 = w >> 1, h2 = h >> 1, out = new Float32Array(w2 * h2);
    for (let y = 0; y < h2; y++) {
      for (let x = 0; x < w2; x++) {
        const i = 2 * y * w + 2 * x;
        out[y * w2 + x] = (src[i] + src[i + 1] + src[i + w] + src[i + w + 1]) / 4;
      }
    }
    return out;
  }

  // Vertex of the parabola through (-1, cm), (0, c0), (1, cp): sub-pixel offset in [-0.5, 0.5].
  function subpixel(cm, c0, cp) {
    const den = cm - 2 * c0 + cp;
    if (!(den > 1e-6)) return 0;
    return Math.max(-0.5, Math.min(0.5, (0.5 * (cm - cp)) / den));
  }

  // Content shift d such that b(p + d) ~= a(p). Returns {dx, dy, broken}.
  // Receipts are periodic (a text line every ~9 thumbnail px), so a wide search can lock onto the
  // neighbouring line. Motion is smooth, so first look right around the predicted shift and only
  // search wide when that fails.
  function estimateShift(a, b, w, h, guess = { dx: 0, dy: 0 }) {
    const gx = Math.round(guess.dx), gy = Math.round(guess.dy);
    const M = 2 * (R + Math.ceil(Math.max(Math.abs(guess.dx), Math.abs(guess.dy)) / 2)) + 3;
    // How different the picture is from itself nudged by two pixels: a true match beats this by far
    // (ratio <= ~0.25), a jump to other text that merely looks alike does not (>= ~0.6).
    const texture = meanAbsDiff(a, a, w, h, 2, 2, M, 2);
    if (texture <= 2) return { dx: guess.dx, dy: guess.dy, flat: true }; // blank paper: keep the guess

    const local = refine(a, b, w, h, gx, gy, 3, M);
    if (local.residual <= 0.3 * texture) return { dx: local.dx, dy: local.dy };

    const a2 = halve(a, w, h), b2 = halve(b, w, h), w2 = w >> 1, h2 = h >> 1;
    const hx = Math.round(guess.dx / 2), hy = Math.round(guess.dy / 2);
    const M2 = (M - 3) / 2;
    let best = { dx: 0, dy: 0, cost: Infinity };
    for (let dy = hy - R; dy <= hy + R; dy++) {
      for (let dx = hx - R; dx <= hx + R; dx++) {
        const cost = meanAbsDiff(a2, b2, w2, h2, dx, dy, M2, 1) + 0.03 * Math.hypot(dx - hx, dy - hy);
        if (cost < best.cost) best = { dx, dy, cost };
      }
    }
    const wide = refine(a, b, w, h, 2 * best.dx, 2 * best.dy, 2, M);
    const m = wide.residual < local.residual ? wide : local;
    // judged at the sub-pixel position: true matches score <= ~0.2, jumps >= ~0.6
    if (m.residual > 0.4 * texture) return { dx: 0, dy: 0, broken: true, residual: m.residual, texture, at: { dx: m.dx, dy: m.dy } };
    return { dx: m.dx, dy: m.dy };
  }

  // Best integer shift within +-r of (cx, cy) at full resolution, refined to sub-pixel.
  function refine(a, b, w, h, cx, cy, r, M) {
    let fx = cx, fy = cy, fc = Infinity;
    for (let dy = cy - r; dy <= cy + r; dy++) {
      for (let dx = cx - r; dx <= cx + r; dx++) {
        const c = meanAbsDiff(a, b, w, h, dx, dy, M, 2);
        if (c < fc) { fc = c; fx = dx; fy = dy; }
      }
    }
    const cost = (dx, dy) => meanAbsDiff(a, b, w, h, dx, dy, M, 1);
    const c0 = cost(fx, fy);
    const dx = fx + subpixel(cost(fx - 1, fy), c0, cost(fx + 1, fy));
    const dy = fy + subpixel(cost(fx, fy - 1), c0, cost(fx, fy + 1));
    return { dx, dy, residual: subpixelCost(a, b, w, h, dx, dy, M) };
  }

  // meanAbsDiff with b sampled bilinearly at a fractional shift (every other pixel).
  function subpixelCost(a, b, w, h, dx, dy, margin) {
    const ix = Math.floor(dx), iy = Math.floor(dy), fx = dx - ix, fy = dy - iy;
    const w00 = (1 - fx) * (1 - fy), w01 = fx * (1 - fy), w10 = (1 - fx) * fy, w11 = fx * fy;
    let sum = 0, n = 0;
    for (let y = margin; y < h - margin - 1; y += 2) {
      const rb = (y + iy) * w + ix;
      for (let x = margin; x < w - margin - 1; x += 2) {
        const i = rb + x;
        const v = b[i] * w00 + b[i + 1] * w01 + b[i + w] * w10 + b[i + w + 1] * w11;
        sum += Math.abs(a[y * w + x] - v);
        n++;
      }
    }
    return n ? sum / n : Infinity;
  }

  function toGray(rgba, n) {
    const g = new Float32Array(n);
    for (let i = 0, j = 0; i < n; i++, j += 4) g[i] = 0.299 * rgba[j] + 0.587 * rgba[j + 1] + 0.114 * rgba[j + 2];
    return g;
  }

  class MotionTracker {
    constructor(width = 192) {
      this.w = width;
      this.h = 0;
      this.key = null; // keyframe thumbnail
      this.keyG = { x: 0, y: 0 }; // accumulated shift at the keyframe
      this.last = { dx: 0, dy: 0 }; // current frame's shift relative to the keyframe
      this.G = { x: 0, y: 0 }; // accumulated content shift, thumbnail px
      this.speed = 0; // smoothed thumbnail px per frame
      this.breaks = 0; // times the picture changed too much to follow
    }

    // gray: Float32Array thumbnail of size w x h
    push(gray, h) {
      if (h !== this.h || !this.key) {
        this.h = h;
        this.key = gray;
        this.keyG = { ...this.G };
        this.last = { dx: 0, dy: 0 };
        return;
      }
      const m = estimateShift(this.key, gray, this.w, h, this.last);
      if (m.broken) {
        this.events = (this.events || []).slice(-9).concat([{ residual: m.residual, texture: m.texture, at: m.at, guess: { ...this.last }, suspect: !!this.suspect }]);
        // One odd frame (blur, glare, compression) is not a jump: only when the next frame also fails
        // to match the keyframe is the picture considered to have changed.
        if (!this.suspect) {
          this.suspect = true;
          return;
        }
        this.suspect = false;
        this.breaks++;
        this.key = gray;
        this.keyG = { ...this.G };
        this.last = { dx: 0, dy: 0 };
        this.speed = 99;
        return;
      }
      this.suspect = false;
      const prevG = this.G;
      this.G = { x: this.keyG.x + m.dx, y: this.keyG.y + m.dy };
      this.speed = 0.6 * this.speed + 0.4 * Math.hypot(this.G.x - prevG.x, this.G.y - prevG.y);
      this.last = { dx: m.dx, dy: m.dy };
      if (Math.hypot(m.dx, m.dy) > REKEY) {
        this.key = gray;
        this.keyG = { ...this.G };
        this.last = { dx: 0, dy: 0 };
      }
    }
  }

  const api = { MotionTracker, estimateShift, toGray, meanAbsDiff, halve };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Motion = api;
})(typeof self !== "undefined" ? self : this);
