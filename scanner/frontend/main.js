// Camera loop, motion tracking, overlay drawing and the Streamlit bridge.
/* global Tracker, Motion */
(function () {
  "use strict";

  // ---- Streamlit custom component protocol (v1), plain JS so there is no build step ----------
  const Streamlit = {
    send(type, data) {
      window.parent.postMessage({ isStreamlitMessage: true, type, ...data }, "*");
    },
    ready() { this.send("streamlit:componentReady", { apiVersion: 1 }); },
    setFrameHeight(height) { this.send("streamlit:setFrameHeight", { height }); },
    setValue(value) { this.send("streamlit:setComponentValue", { value, dataType: "json" }); },
  };

  const COLORS = { ok: "#22c55e", bad: "#ef4444", warn: "#f97316", role: "#3b82f6", raw: "rgba(255,255,255,.7)" };
  const ROLE_LABELS = { vendor: "VENDOR", date: "DATE", time: "TIME", total: "TOTAL", subtotal: "SUBTOTAL", tax: "TAX", count: "ITEMS" };
  const SPEED_LIMIT = 2.5; // thumbnail px per frame; above this frames are too blurry to read

  const $ = (id) => document.getElementById(id);
  const els = {
    stage: $("stage"), video: $("video"), canvas: $("overlay"), placeholder: $("placeholder"), status: $("status"),
    startBig: $("start-big"), hud: $("hud"), hint: $("hint"), pause: $("pause"), torch: $("torch"), reset: $("reset"),
  };
  const ctx = els.canvas.getContext("2d");

  const doc = new Tracker.ReceiptDoc();
  let settings = { captureMax: 1280, detLimit: 960, backend: "wasm", confThresh: 0.85, minSupport: 2 };
  let analysis = null;
  let resetNonce = null;
  let worker = null, workerBackend = null, workerReady = false, busy = false, threads = 1;
  let running = false, paused = false, track = null, torchOn = false;
  let pending = null, prevG = null, lastFrame = null, frameSeq = 0;
  let lastCaptureAt = 0;
  const timings = [];
  const lastSent = { version: -1, session: null, at: 0 };

  // ---- global motion (motion.js) on small grayscale thumbnails of the live video ------------------
  class Motion extends window.Motion.MotionTracker {
    constructor() {
      super(192);
      this.canvas = document.createElement("canvas");
      this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    }

    update(video) {
      const vw = video.videoWidth, vh = video.videoHeight;
      if (!vw || !vh) return;
      const h = Math.round((this.w * vh) / vw);
      if (this.canvas.width !== this.w || this.canvas.height !== h) {
        this.canvas.width = this.w;
        this.canvas.height = h;
      }
      this.ctx.drawImage(video, 0, 0, this.w, h);
      this.push(window.Motion.toGray(this.ctx.getImageData(0, 0, this.w, h).data, this.w * h), h);
    }
  }

  const motion = new Motion();
  const cap = document.createElement("canvas");
  const capCtx = cap.getContext("2d", { willReadFrequently: true });

  // ---- OCR worker ----------------------------------------------------------------------------
  function startWorker() {
    if (worker && workerBackend === settings.backend) return;
    if (worker) worker.terminate();
    workerReady = false;
    busy = false;
    pending = null;
    workerBackend = settings.backend;
    worker = new Worker("worker.js?v=14");
    worker.onmessage = onWorkerMessage;
    worker.onerror = (e) => setStatus(`OCR worker failed: ${e.message || e}`);
    worker.postMessage({ type: "init", backend: settings.backend });
    worker.postMessage({ type: "options", options: { detLimit: settings.detLimit } });
    setStatus("Loading OCR models...");
  }

  function onWorkerMessage(e) {
    const msg = e.data;
    if (msg.type === "ready") {
      workerReady = true;
      workerBackend = msg.backend;
      threads = msg.threads || 1;
      setStatus(running ? "" : "Ready. Tap Start camera.");
    } else if (msg.type === "result") {
      onResult(msg);
    } else if (msg.type === "error") {
      busy = false;
      pending = null;
      setStatus(`OCR error: ${msg.message}`);
    }
  }

  function captureFrame() {
    const v = els.video;
    const s = Math.min(1, settings.captureMax / Math.max(v.videoWidth, v.videoHeight));
    const w = Math.round(v.videoWidth * s), h = Math.round(v.videoHeight * s);
    if (cap.width !== w || cap.height !== h) {
      cap.width = w;
      cap.height = h;
    }
    capCtx.drawImage(v, 0, 0, w, h);
    const img = capCtx.getImageData(0, 0, w, h);
    pending = { id: ++frameSeq, G: { ...motion.G }, breaks: motion.breaks, w, h, t: performance.now() };
    busy = true;
    lastCaptureAt = pending.t;
    // predicted positions of confirmed lines in this frame, so the worker can skip re-reading them
    let hints = [];
    if (prevG && pending.breaks === prevG.breaks && doc.state === "tracking") {
      const k = w / motion.w;
      const T = doc.T;
      const pred = { s: T.s, tx: T.tx - T.s * (pending.G.x - prevG.x) * k, ty: T.ty - T.s * (pending.G.y - prevG.y) * k };
      hints = doc.hints(pred, { w, h });
    }
    worker.postMessage({ type: "frame", id: pending.id, buffer: img.data.buffer, width: w, height: h, hints }, [img.data.buffer]);
  }

  function onResult(msg) {
    busy = false;
    const p = pending;
    pending = null;
    if (!p || p.id !== msg.id) return;
    const k = p.w / motion.w;
    let shift = prevG ? { dx: (p.G.x - prevG.x) * k, dy: (p.G.y - prevG.y) * k } : { dx: 0, dy: 0 };
    if (prevG && p.breaks !== prevG.breaks) shift = null; // picture jumped: relocate from text alone
    const r = doc.ingest(msg.lines, shift, { w: p.w, h: p.h });
    if (r.state === "lost") {
      console.debug("[scanner] lost", JSON.stringify({ shift, reg: doc.lastRegistration, texts: msg.lines.filter((l) => !l.skip).map((l) => l.text) }));
    }
    prevG = { ...p.G, breaks: p.breaks };
    lastFrame = { G: p.G, breaks: p.breaks, w: p.w, h: p.h, lines: msg.lines, state: r.state };
    timings.push({ at: performance.now(), ms: msg.timing.total, det: msg.timing.det, rec: msg.timing.rec, n: msg.timing.read, skip: msg.timing.skipped });
    while (timings.length > 10) timings.shift();
    updateHud();
  }

  function updateHud() {
    if (!timings.length) return;
    const span = (timings[timings.length - 1].at - timings[0].at) / 1000;
    const fps = timings.length > 1 && span > 0 ? (timings.length - 1) / span : 0;
    const avg = (key) => timings.reduce((a, t) => a + t[key], 0) / timings.length;
    els.hud.textContent = `${workerBackend}${threads > 1 ? "×" + threads : ""} ${fps.toFixed(1)}/s ${avg("det").toFixed(0)}+${avg("rec").toFixed(0)}ms ${avg("n").toFixed(0)}r/${avg("skip").toFixed(0)}k`;
    els.hud.title = "backend, scans per second, detection+recognition ms, boxes read / skipped as already known";
  }

  // ---- camera ------------------------------------------------------------------------------
  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Camera blocked: open the app over https:// (see the setup notes).");
      return;
    }
    els.startBig.disabled = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      els.video.srcObject = stream;
      await els.video.play();
      track = stream.getVideoTracks()[0];
      try { await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (_) { /* optional */ }
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      els.torch.hidden = !caps.torch;
      running = true;
      els.placeholder.style.display = "none";
      els.pause.disabled = false;
      layout();
    } catch (err) {
      els.startBig.disabled = false;
      setStatus(`Camera error: ${err.name || ""} ${err.message || err}`);
    }
  }

  function setStatus(text) {
    els.status.textContent = text;
  }

  // ---- drawing -----------------------------------------------------------------------------
  function coverMapping(cw, ch, fw, fh) {
    const s = Math.max(cw / fw, ch / fh);
    return { s, ox: (cw - fw * s) / 2, oy: (ch - fh * s) / 2 };
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const cw = els.canvas.clientWidth, ch = els.canvas.clientHeight;
    if (els.canvas.width !== Math.round(cw * dpr) || els.canvas.height !== Math.round(ch * dpr)) {
      els.canvas.width = Math.round(cw * dpr);
      els.canvas.height = Math.round(ch * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    if (!lastFrame || !running) return;
    // positions are stale after a jump or while lost: show nothing rather than boxes in the wrong place
    const stale = motion.breaks !== lastFrame.breaks;

    const k = lastFrame.w / motion.w;
    const off = { x: (motion.G.x - lastFrame.G.x) * k, y: (motion.G.y - lastFrame.G.y) * k };
    const view = coverMapping(cw, ch, lastFrame.w, lastFrame.h);
    const T = doc.T;
    const sx = (x) => ((x - T.tx) / T.s + off.x) * view.s + view.ox;
    const sy = (y) => ((y - T.ty) / T.s + off.y) * view.s + view.oy;
    const roles = (analysis && analysis.roles) || {};
    const section = analysis && analysis.section;
    const productIds = new Set(section ? section.ids : []);
    const mismatch = section && section.status === "mismatch";

    const showDoc = !stale && lastFrame.state !== "lost";
    // products section frame
    if (showDoc && section && productIds.size && section.status !== "pending") {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const dl of doc.lines) {
        if (!productIds.has(dl.id)) continue;
        x0 = Math.min(x0, sx(dl.box[0])); y0 = Math.min(y0, sy(dl.box[1]));
        x1 = Math.max(x1, sx(dl.box[2])); y1 = Math.max(y1, sy(dl.box[3]));
      }
      if (x1 > x0) {
        const pad = 6;
        ctx.save();
        ctx.lineWidth = mismatch ? 3 : 2;
        ctx.strokeStyle = mismatch ? COLORS.warn : COLORS.ok;
        if (!mismatch) ctx.setLineDash([6, 5]);
        if (mismatch) {
          ctx.fillStyle = "rgba(249,115,22,0.13)";
          ctx.fillRect(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad);
        }
        ctx.strokeRect(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad);
        ctx.restore();
        tag(mismatch ? "ITEMS ≠ TOTAL" : "✓ ITEMS = TOTAL", x0 - pad, Math.max(14, y0 - pad), mismatch ? COLORS.warn : COLORS.ok);
      }
    }

    // word boxes
    const tagged = new Set();
    ctx.lineWidth = 2;
    for (const dl of showDoc ? doc.lines : []) {
      const top = sy(dl.box[1]), bot = sy(dl.box[3]);
      if (bot < 0 || top > ch) continue;
      const bw = dl.box[2] - dl.box[0];
      const inSection = productIds.has(dl.id);
      // words as rotated rectangles along the line's axis (receipt tilted in the camera)
      const co = Math.cos(dl.angle || 0), si = Math.sin(dl.angle || 0);
      const ht = (dl.box[3] - dl.box[1]) / 2;
      for (const w of doc.wordStates(dl)) {
        const X0 = dl.box[0] + w.f0 * bw, X1 = dl.box[0] + w.f1 * bw;
        if (sx(X1) < 0 || sx(X0) > cw) continue;
        const xc = (X0 + X1) / 2, yc = dl.cy + (xc - dl.cx) * (si / co), hl = (X1 - X0) / 2 / co;
        ctx.strokeStyle = !w.ok ? COLORS.bad : mismatch && inSection ? COLORS.warn : COLORS.ok;
        ctx.beginPath();
        for (const [u, v] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
          const x = xc + u * hl * co - v * ht * si, y = yc + u * hl * si + v * ht * co;
          ctx.lineTo(sx(x), sy(y));
        }
        ctx.closePath();
        ctx.stroke();
      }
      const role = roles[dl.id];
      if (role && ROLE_LABELS[role] && !tagged.has(role)) {
        tagged.add(role);
        tag(ROLE_LABELS[role], sx(dl.box[0]), top, COLORS.role);
      }
    }

    // when tracking is lost show what the camera currently sees, without committing it
    if (lastFrame.state === "lost" && !stale) {
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = COLORS.raw;
      for (const l of lastFrame.lines.filter((x) => !x.skip)) {
        const x0 = (l.box[0] + off.x) * view.s + view.ox, y0 = (l.box[1] + off.y) * view.s + view.oy;
        ctx.strokeRect(x0, y0, (l.box[2] - l.box[0]) * view.s, (l.box[3] - l.box[1]) * view.s);
      }
      ctx.restore();
    }
  }

  function tag(text, x, y, color) {
    ctx.save();
    ctx.font = "600 10px system-ui, sans-serif";
    const w = ctx.measureText(text).width + 8;
    ctx.fillStyle = color;
    ctx.fillRect(x, y - 13, w, 13);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + 4, y - 3);
    ctx.restore();
  }

  function updateHint() {
    let hint = "";
    if (running) {
      if (!workerReady) hint = "Loading OCR models...";
      else if (paused) hint = "Paused";
      else if (lastFrame && lastFrame.state === "lost") hint = "Lost track - move back over text already scanned";
      else if (motion.speed > SPEED_LIMIT) hint = "Hold steady";
      else if (!doc.lines.length) hint = "Fit the receipt width in view, start at the top";
      else if (analysis && analysis.section && analysis.section.status === "mismatch") hint = "Items don't add up - rescan the orange area";
      else if (analysis && analysis.section && analysis.section.status === "ok") hint = "✓ Items add up to the total";
    }
    if (els.hint.textContent !== hint) els.hint.textContent = hint;
  }

  // ---- main loop -----------------------------------------------------------------------------
  function tick() {
    if (running && els.video.readyState >= 2) {
      motion.update(els.video);
      const now = performance.now();
      const steady = motion.speed <= SPEED_LIMIT || now - lastCaptureAt > 1200;
      if (workerReady && !busy && !paused && !document.hidden && steady) captureFrame();
    }
    draw();
    updateHint();
    maybeSend();
    requestAnimationFrame(tick);
  }

  function maybeSend(force) {
    const now = performance.now();
    if (!force && (doc.version === lastSent.version && doc.session === lastSent.session)) return;
    if (!force && now - lastSent.at < 800) return;
    lastSent.version = doc.version;
    lastSent.session = doc.session;
    lastSent.at = now;
    Streamlit.setValue({
      session: doc.session,
      version: doc.version,
      lines: doc.exportLines(),
      stats: { backend: workerBackend, lines: doc.lines.length, state: doc.state },
    });
  }

  function resetDoc() {
    doc.reset();
    analysis = null;
    lastFrame = null;
    prevG = null;
    maybeSend(true);
  }

  // ---- layout & Streamlit render events --------------------------------------------------------
  function layout() {
    const width = els.stage.clientWidth || 360;
    const screenH = Math.min(window.screen.height || 800, window.outerHeight || 10000);
    const maxH = Math.max(320, screenH * 0.62);
    const v = els.video;
    const aspect = v.videoWidth ? v.videoHeight / v.videoWidth : 3 / 4;
    els.stage.style.height = `${Math.round(Math.min(width * aspect, maxH))}px`;
    Streamlit.setFrameHeight(contentHeight());
  }

  // the iframe's own height would feed back into documentElement.scrollHeight, so measure the content
  function contentHeight() {
    return Math.ceil(document.getElementById("app").getBoundingClientRect().height);
  }

  function onRender(args) {
    if (args.settings) {
      const prev = settings;
      settings = { ...settings, ...args.settings };
      doc.setOptions({ confThresh: settings.confThresh, minSupport: settings.minSupport });
      if (!worker || prev.backend !== settings.backend) startWorker();
      if (worker && prev.detLimit !== settings.detLimit) {
        worker.postMessage({ type: "options", options: { detLimit: settings.detLimit } });
      }
    }
    if (resetNonce !== null && args.reset_nonce !== resetNonce) resetDoc();
    resetNonce = args.reset_nonce;
    if (args.analysis && args.analysis.session === doc.session) analysis = args.analysis;
  }

  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "streamlit:render") onRender(e.data.args || {});
  });
  els.startBig.addEventListener("click", startCamera);
  els.pause.addEventListener("click", () => {
    paused = !paused;
    els.pause.textContent = paused ? "Resume" : "Pause";
  });
  els.reset.addEventListener("click", resetDoc);
  els.torch.addEventListener("click", async () => {
    torchOn = !torchOn;
    try { await track.applyConstraints({ advanced: [{ torch: torchOn }] }); } catch (_) { torchOn = false; }
    els.torch.textContent = torchOn ? "Light off" : "Light";
  });
  els.video.addEventListener("loadedmetadata", layout);
  window.addEventListener("resize", layout);
  new ResizeObserver(() => Streamlit.setFrameHeight(contentHeight())).observe(document.getElementById("app"));

  // read-only handle for debugging and the end-to-end test
  window.__scanner = { doc, motion, get lastFrame() { return lastFrame; }, get analysis() { return analysis; } };
  Streamlit.ready();
  layout();
  requestAnimationFrame(tick);
})();
