// Receipt document model: registers each OCR frame onto one receipt coordinate system,
// merges repeated sightings of the same text line (voting) and only appends genuinely new lines.
//
// Transform convention: doc = s * frame + (tx, ty)   (similarity without rotation).
(function (root) {
  "use strict";

  function norm(text) {
    return text.toUpperCase().replace(/\s+/g, "");
  }

  function levenshtein(a, b) {
    if (a === b) return 0;
    if (!a.length) return b.length;
    if (!b.length) return a.length;
    let prev = new Array(b.length + 1);
    let cur = new Array(b.length + 1);
    for (let j = 0; j <= b.length; j++) prev[j] = j;
    for (let i = 1; i <= a.length; i++) {
      cur[0] = i;
      for (let j = 1; j <= b.length; j++) {
        const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      }
      [prev, cur] = [cur, prev];
    }
    return prev[b.length];
  }

  function similarity(a, b) {
    const m = Math.max(a.length, b.length);
    return m ? 1 - levenshtein(a, b) / m : 1;
  }

  function mapBox(b, T) {
    return [T.s * b[0] + T.tx, T.s * b[1] + T.ty, T.s * b[2] + T.tx, T.s * b[3] + T.ty];
  }

  function iou(a, b) {
    const ix = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
    const iy = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
    if (ix <= 0 || iy <= 0) return { iou: 0, cover: 0 };
    const inter = ix * iy;
    const aa = (a[2] - a[0]) * (a[3] - a[1]), ab = (b[2] - b[0]) * (b[3] - b[1]);
    return { iou: inter / (aa + ab - inter), cover: inter / Math.min(aa, ab) };
  }

  // Build a line observation from a subset of a line's words (frame coordinates).
  function fromWords(l, words) {
    const confs = words.map((w) => w.conf);
    const x0 = words[0].x0, x1 = words[words.length - 1].x1;
    // on a tilted line the sub-line's centre sits higher or lower than the line's centre
    const dy = ((x0 + x1) / 2 - (l.box[0] + l.box[2]) / 2) * Math.tan(l.angle || 0);
    return {
      text: words.map((w) => w.text).join(" "),
      conf: confs.reduce((a, b) => a + b, 0) / confs.length,
      minConf: Math.min(...confs),
      box: [x0, l.box[1] + dy, x1, l.box[3] + dy],
      angle: l.angle || 0,
      words,
    };
  }

  // Join detector pieces (sorted left to right) into one line observation.
  function joinPieces(pieces) {
    const words = pieces.flatMap((p) => p.words);
    const total = pieces.reduce((a, p) => a + p.text.length, 0) || 1;
    const avg = (f) => pieces.reduce((a, p) => a + f(p), 0) / pieces.length;
    const cy = avg((p) => (p.box[1] + p.box[3]) / 2), half = Math.max(...pieces.map((p) => p.box[3] - p.box[1])) / 2;
    return {
      text: pieces.map((p) => p.text).join(" "),
      conf: pieces.reduce((a, p) => a + p.conf * p.text.length, 0) / total,
      minConf: Math.min(...pieces.map((p) => p.minConf)),
      box: [pieces[0].box[0], cy - half, pieces[pieces.length - 1].box[2], cy + half],
      angle: avg((p) => p.angle || 0),
      words,
    };
  }

  function median(arr) {
    if (!arr.length) return 0;
    const s = [...arr].sort((x, y) => x - y);
    return s[s.length >> 1];
  }

  const DEFAULTS = {
    minLineConf: 0.3, // drop recognitions below this mean confidence entirely
    minNewConf: 0.6, // a line must be at least this confident to start a new document line
    edgeMargin: 3, // px: lines touching the top/bottom frame edge are cut off, ignore them
    anchorSim: 0.75, // text similarity needed for a line to anchor registration
    anchorMinLen: 3,
    matchCover: 0.75, // both boxes must cover this much of each other to count as the same line
    newMaxCover: 0.2, // a line is new only if existing lines cover less than this share of it
    confThresh: 0.85, // per-word min char confidence for green
    minSupport: 2, // frames that must agree before a word turns green
    maxGroupCount: 50,
  };

  class ReceiptDoc {
    constructor(opts = {}) {
      this.opts = { ...DEFAULTS, ...opts };
      this.reset();
    }

    reset() {
      this.lines = [];
      this.nextId = 1;
      this.T = { s: 1, tx: 0, ty: 0 };
      this.state = "empty";
      this.lostStreak = 0;
      this.version = 0;
      this.session = Math.random().toString(36).slice(2, 10);
    }

    setOptions(opts) {
      Object.assign(this.opts, opts);
      this.version++;
    }

    medianHeight() {
      return median(this.lines.map((l) => l.box[3] - l.box[1])) || 20;
    }

    // frameLines: OCR output in frame pixels. motion: content shift {dx, dy} in frame pixels since the
    // previous ingested frame (from the optical motion estimator), or null when the motion tracker lost
    // the picture (fast swipe, camera covered) and the frame must be located on the receipt from scratch.
    ingest(frameLines, motion, frame) {
      const o = this.opts;
      // boxes the OCR skipped because they sit on already-confirmed lines: presence only, no text
      const skipped = frameLines.filter((l) => l.skip);
      const lines = frameLines.filter(
        (l) => !l.skip && l.conf >= o.minLineConf && l.text.trim().length > 0 &&
          l.box[1] > o.edgeMargin && l.box[3] < frame.h - o.edgeMargin
      );
      const global = !motion || this.lostStreak > 0;
      const m = motion || { dx: 0, dy: 0 };
      const pred = { s: this.T.s, tx: this.T.tx - this.T.s * m.dx, ty: this.T.ty - this.T.s * m.dy };
      const result = { state: this.state, anchors: 0, matched: 0, added: 0, removed: 0 };

      if (!lines.length) {
        this.T = pred;
        this.state = this.lines.length ? "idle" : "empty";
        result.state = this.state;
        return result;
      }

      if (!this.lines.length) {
        this.T = pred;
        const fresh = lines.filter((l) => l.conf >= o.minNewConf);
        for (const l of fresh) this._addLine(l, mapBox(l.box, pred));
        this.state = fresh.length ? "tracking" : "empty";
        this.version++;
        return { ...result, state: this.state, added: fresh.length };
      }

      const T = this._register(lines, pred, frame, result, global, skipped);
      if (!T) {
        this.T = pred;
        this.lostStreak++;
        this.state = "lost";
        result.state = "lost";
        return result;
      }
      this.T = T;
      this.lostStreak = 0;
      this.state = "tracking";
      this._merge(lines, T, frame, result, skipped);
      if (result.added || result.matched || result.removed) this.version++;
      result.state = this.state;
      return result;
    }

    // Find the frame -> receipt transform from text seen again. Each (frame line, known line) pair proposes
    // an offset; the largest consistent group wins. "yOnly" pairs (a detector piece of a known line, or a
    // box the OCR skipped because it sat on a known line) only say something about the vertical offset.
    _register(lines, pred, frame, result, global, skipped = []) {
      const o = this.opts;
      const hDoc = this.medianHeight();
      const sigma = 0.3 * frame.h * pred.s;
      const proposals = [];
      const prior = (dx, dy) => (global ? 1 : Math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma)));
      for (let i = 0; i < lines.length; i++) {
        const ni = norm(lines[i].text);
        if (ni.length < o.anchorMinLen) continue;
        const b = mapBox(lines[i].box, pred);
        const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
        for (const dl of this.lines) {
          const nj = dl.consensus.norm;
          if (nj.length < o.anchorMinLen) continue;
          const dy = dl.cy - cy;
          if (!global && Math.abs(dy) > frame.h * pred.s * 0.8) continue;
          let sim = Math.abs(nj.length - ni.length) <= Math.max(2, 0.3 * ni.length) ? similarity(ni, nj) : 0;
          let yOnly = false;
          if (sim < o.anchorSim) {
            const [short, long] = ni.length < nj.length ? [ni, nj] : [nj, ni];
            if (short.length < 4 || !long.includes(short)) continue;
            sim = 0.85;
            yOnly = true;
          }
          const dx = yOnly ? 0 : dl.cx - cx;
          proposals.push({ key: i, label: lines[i].text, dl, dx, dy, yOnly, sim, p: [cx, cy],
            w: sim * Math.min(ni.length, 12) * (0.25 + prior(dx, dy)) });
        }
      }
      skipped.forEach((l, k) => {
        const dl = this.lines.find((d) => d.id === l.skip);
        if (!dl) return;
        const b = mapBox(l.box, pred);
        const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2, dy = dl.cy - cy;
        proposals.push({ key: "s" + k, label: "[known " + dl.consensus.text + "]", dl, dx: 0, dy, yOnly: true, sim: 1, p: [cx, cy],
          w: 4 * (0.25 + prior(0, dy)) });
      });
      this.lastRegistration = { proposals: proposals.length, global };
      if (!proposals.length) return null;

      const tolY = 0.6 * hDoc, tolX = 1.5 * hDoc;
      let best = null;
      for (const p of proposals) {
        const perLine = new Map();
        for (const q of proposals) {
          if (Math.abs(q.dy - p.dy) <= tolY && (q.yOnly || p.yOnly || Math.abs(q.dx - p.dx) <= tolX)) {
            if (!perLine.has(q.key) || perLine.get(q.key).w < q.w) perLine.set(q.key, q);
          }
        }
        let weight = 0;
        for (const q of perLine.values()) weight += q.w;
        if (!best || weight > best.weight) best = { weight, inliers: [...perLine.values()] };
      }
      const inl = best.inliers;
      this.lastRegistration.inliers = inl.map((q) => q.label);
      const full = inl.filter((q) => !q.yOnly);
      const strongSingle = inl.length === 1 && full.length === 1 && inl[0].sim >= 0.9 && norm(inl[0].label).length >= 6;
      // relocating without a motion hint needs more evidence: receipts repeat short strings
      if (global ? inl.length < Math.min(3, lines.length) || inl.length < 2 || !full.length : inl.length < 2 && !strongSingle) return null;
      result.anchors = inl.length;

      // Refine: scale from the vertical spread of the anchors (receipts are scanned top to bottom),
      // vertical offset from all anchors, horizontal offset only from anchors that carry x information.
      const ys = inl.map((q) => [q.p[1], q.dl.cy]);
      const py = ys.reduce((acc, v) => acc + v[0], 0) / ys.length;
      const qy = ys.reduce((acc, v) => acc + v[1], 0) / ys.length;
      let num = 0, den = 0;
      for (const [p, q] of ys) {
        num += (p - py) * (q - qy);
        den += (p - py) ** 2;
      }
      const spreadY = Math.sqrt(den / ys.length);
      let a = spreadY > 2 * hDoc && den > 0 ? num / den : 1;
      a = Math.min(1.15, Math.max(0.87, a));
      const bx = full.length
        ? full.reduce((acc, q) => acc + q.dl.cx - a * q.p[0], 0) / full.length
        : (1 - a) * (inl.reduce((acc, q) => acc + q.p[0], 0) / inl.length);
      return { s: a * pred.s, tx: a * pred.tx + bx, ty: a * pred.ty + (qy - a * py) };
    }

    _merge(lines, T, frame, result, skipped = []) {
      const o = this.opts;
      const taken = new Set(); // doc lines that already got a text vote this frame
      const touched = new Set(skipped.map((l) => l.skip)); // doc lines seen in any segmentation this frame
      const seenShare = new Map(); // doc line id -> share of it covered by this frame's boxes
      for (const l of skipped) {
        const dl = this.lines.find((d) => d.id === l.skip);
        if (dl) dl.seen++;
      }
      const buckets = new Map(); // doc line id -> smaller pieces lying inside it
      const order = lines.map((l, i) => i).sort((x, y) => lines[y].conf - lines[x].conf);
      for (const i of order) {
        const l = lines[i];
        const box = mapBox(l.box, T);
        const n = norm(l.text);
        const overlaps = [];
        let best = null, covered = 0;
        for (const dl of this.lines) {
          const ov = iou(box, dl.box);
          if (ov.cover <= 0) continue;
          const area = (b) => (b[2] - b[0]) * (b[3] - b[1]);
          const inter = ov.cover * Math.min(area(box), area(dl.box));
          const rec = { dl, iou: ov.iou, inNew: inter / area(dl.box), inDoc: inter / area(box) };
          overlaps.push(rec);
          covered += rec.inDoc;
          seenShare.set(dl.id, (seenShare.get(dl.id) || 0) + rec.inNew);
          if (rec.inNew < o.matchCover || rec.inDoc < o.matchCover || taken.has(dl.id)) continue;
          // a piece of the line (e.g. "WHOLE MILK" of "0712 WHOLE MILK") must not vote for the whole line
          const nj = dl.consensus.norm;
          if (n !== nj && (nj.includes(n) || n.includes(nj))) continue;
          const sim = similarity(n, nj);
          const wr = (box[2] - box[0]) / (dl.box[2] - dl.box[0]);
          if (sim < 0.8 && (wr < 0.85 || wr > 1 / 0.85)) continue;
          const score = ov.iou + 0.5 * sim;
          if (!best || score > best.score) best = { dl, score };
        }
        if (best) {
          taken.add(best.dl.id);
          this._observe(best.dl, l, box);
          result.matched++;
          continue;
        }
        // Detector split a known line into pieces: collect the pieces, vote once they cover the line.
        const container = overlaps.find((r) => r.inDoc >= 0.6 && r.inDoc >= r.inNew);
        if (container) {
          if (!buckets.has(container.dl.id)) buckets.set(container.dl.id, { dl: container.dl, pieces: [] });
          buckets.get(container.dl.id).pieces.push(l);
          continue;
        }
        // Detector merged several known lines: hand each its own words, keep leftovers as new lines.
        const parts = overlaps.filter((r) => r.inNew >= 0.6 && r.inNew > r.inDoc);
        if (parts.length && l.words.length) {
          this._distributeWords(l, T, parts, taken, result);
          continue;
        }
        // 1-2 character fragments next to known text are usually detector noise ("(", a stray "S")
        const minConf = n.length <= 2 ? Math.max(o.minNewConf, 0.9) : o.minNewConf;
        if (covered < o.newMaxCover && l.conf >= minConf) {
          const dl = this._addLine(l, box);
          taken.add(dl.id);
          touched.add(dl.id);
          result.added++;
        }
      }
      for (const { dl, pieces } of buckets.values()) {
        if (taken.has(dl.id)) continue;
        pieces.sort((a, b) => a.box[0] - b.box[0]);
        const width = pieces.reduce((a, p) => a + (p.box[2] - p.box[0]) * T.s, 0);
        const span = (pieces[pieces.length - 1].box[2] - pieces[0].box[0]) * T.s;
        if (span < 0.9 * (dl.box[2] - dl.box[0]) || width < 0.7 * span) continue; // partial view: no vote
        const joined = joinPieces(pieces);
        taken.add(dl.id);
        this._observe(dl, joined, mapBox(joined.box, T));
        result.matched++;
      }

      // Lines that sit well inside the current view but were not read at all are probably ghosts.
      const view = mapBox([0, 0, frame.w, frame.h], T);
      const mx = 0.06 * (view[2] - view[0]), my = 0.06 * (view[3] - view[1]);
      const keep = [];
      for (const dl of this.lines) {
        const inside = dl.box[0] > view[0] + mx && dl.box[2] < view[2] - mx && dl.box[1] > view[1] + my && dl.box[3] < view[3] - my;
        const reread = touched.has(dl.id) || taken.has(dl.id) || (seenShare.get(dl.id) || 0) >= 0.5;
        if (inside && !reread) dl.missed++;
        if (dl.missed >= 4 && dl.missed > 2 * dl.seen) {
          result.removed++;
          continue;
        }
        keep.push(dl);
      }
      this.lines = keep;
    }

    _distributeWords(l, T, parts, taken, result) {
      const perLine = new Map();
      const leftovers = [];
      for (const w of l.words) {
        const cx = T.s * (w.x0 + w.x1) / 2 + T.tx;
        const part = parts.find((r) => cx >= r.dl.box[0] && cx <= r.dl.box[2]);
        if (part) {
          if (!perLine.has(part.dl.id)) perLine.set(part.dl.id, { dl: part.dl, words: [] });
          perLine.get(part.dl.id).words.push(w);
        } else {
          leftovers.push(w);
        }
      }
      for (const { dl, words } of perLine.values()) {
        if (taken.has(dl.id)) continue;
        const sub = fromWords(l, words);
        taken.add(dl.id);
        this._observe(dl, sub, mapBox(sub.box, T));
        result.matched++;
      }
      // contiguous runs of words that land on empty paper become new lines
      let run = [];
      const flush = () => {
        if (!run.length) return;
        const sub = fromWords(l, run);
        const box = mapBox(sub.box, T);
        if (sub.conf >= this.opts.minNewConf && this._coveredShare(box) < this.opts.newMaxCover) {
          this._addLine(sub, box);
          result.added++;
        }
        run = [];
      };
      for (const w of l.words) {
        if (leftovers.includes(w)) run.push(w);
        else flush();
      }
      flush();
    }

    // Share of `box` already covered by known lines.
    _coveredShare(box) {
      const area = (box[2] - box[0]) * (box[3] - box[1]);
      let inter = 0;
      for (const dl of this.lines) {
        const ix = Math.min(box[2], dl.box[2]) - Math.max(box[0], dl.box[0]);
        const iy = Math.min(box[3], dl.box[3]) - Math.max(box[1], dl.box[1]);
        if (ix > 0 && iy > 0) inter += ix * iy;
      }
      return area > 0 ? inter / area : 1;
    }

    _addLine(l, box) {
      const dl = { id: "L" + this.nextId++, box: [...box], groups: new Map(), seen: 0, missed: 0, total: 0 };
      this._observe(dl, l, box, true);
      this.lines.push(dl);
      return dl;
    }

    _observe(dl, l, box, fresh) {
      if (!fresh) {
        const k = 0.2;
        for (let c = 0; c < 4; c++) dl.box[c] = dl.box[c] * (1 - k) + box[c] * k;
        dl.angle = dl.angle * (1 - k) + (l.angle || 0) * k;
      } else {
        dl.angle = l.angle || 0;
      }
      dl.cx = (dl.box[0] + dl.box[2]) / 2;
      dl.cy = (dl.box[1] + dl.box[3]) / 2;
      dl.seen++;
      dl.total++;
      const n = norm(l.text);
      const bw = l.box[2] - l.box[0] || 1;
      const words = l.words.map((w) => ({
        text: w.text, conf: w.conf, f0: (w.x0 - l.box[0]) / bw, f1: (w.x1 - l.box[0]) / bw,
      }));
      let g = dl.groups.get(n);
      if (!g) {
        g = { norm: n, text: l.text, count: 0, score: 0, bestConf: -1, words };
        dl.groups.set(n, g);
      }
      g.count = Math.min(this.opts.maxGroupCount, g.count + 1);
      g.score += l.conf;
      if (l.minConf > g.bestConf) {
        g.bestConf = l.minConf;
        g.text = l.text;
        g.conf = l.conf;
        g.words = words;
      }
      let best = null;
      for (const cand of dl.groups.values()) if (!best || cand.score > best.score) best = cand;
      dl.consensus = best;
    }

    // Per-word display state for a line: ok == green.
    wordStates(dl) {
      const o = this.opts;
      const g = dl.consensus;
      const agree = g.count / Math.max(1, Math.min(dl.total, this.opts.maxGroupCount));
      const stable = g.count >= o.minSupport && agree >= 0.5;
      return g.words.map((w) => ({ ...w, ok: stable && w.conf >= o.confThresh }));
    }

    // Confirmed, well-supported lines in frame coordinates for transform T: the OCR can skip re-reading them.
    hints(T, frame, minSupport = 3) {
      const out = [];
      const view = mapBox([0, 0, frame.w, frame.h], T);
      for (const dl of this.lines) {
        if (dl.box[3] < view[1] || dl.box[1] > view[3]) continue;
        if (dl.consensus.count < Math.max(minSupport, this.opts.minSupport + 1)) continue;
        if (!this.wordStates(dl).every((w) => w.ok)) continue;
        out.push({ id: dl.id, box: dl.box.map((v, i) => (v - (i % 2 ? T.ty : T.tx)) / T.s) });
      }
      return out;
    }

    exportLines() {
      return [...this.lines]
        .sort((a, b) => a.box[1] - b.box[1] || a.box[0] - b.box[0])
        .map((dl) => {
          const words = this.wordStates(dl);
          return {
            id: dl.id,
            text: dl.consensus.text,
            conf: Math.round((dl.consensus.conf ?? 0) * 1000) / 1000,
            ok: words.length > 0 && words.every((w) => w.ok),
            support: dl.consensus.count,
            box: dl.box.map((v) => Math.round(v)),
            angle: Math.round(dl.angle * 1e4) / 1e4,
          };
        });
    }
  }

  const api = { ReceiptDoc, similarity, norm, mapBox, iou };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Tracker = api;
})(typeof self !== "undefined" ? self : this);
