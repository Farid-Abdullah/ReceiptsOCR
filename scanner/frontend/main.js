// Version 2 (guided photos): camera with a "Take picture" button. The photo is read by the OCR worker, its text lines go to
// Streamlit, and Streamlit sends back which lines to outline as "found". Plus the Streamlit bridge.
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

  const COLORS = { ok: "#22c55e", bad: "#ef4444", found: "#3b82f6" };
  const CAMERA_FLAG = "receipt-scanner-camera"; // remembers that the camera was allowed, to restart it

  const $ = (id) => document.getElementById(id);
  const els = {
    stage: $("stage"), video: $("video"), canvas: $("overlay"), placeholder: $("placeholder"), status: $("status"),
    startBig: $("start-big"), banner: $("banner"), hint: $("hint"), shoot: $("shoot"), retake: $("retake"), torch: $("torch"),
  };
  const ctx = els.canvas.getContext("2d");

  let settings = { detLimit: 1280, captureMax: 1920, backend: "wasm", confThresh: 0.85 };
  let worker = null, workerBackend = null, workerReady = false;
  let stream = null, track = null, torchOn = false, starting = false;
  let step = { id: null, title: "", prompt: "", capture: false };
  let mode = "camera"; // camera | reading | review
  let photo = null; // { seq, canvas, w, h, lines, acked }
  let review = null; // what Streamlit found in the current photo: { seq, ids, label }
  let seq = 0;

  // ---- OCR worker ----------------------------------------------------------------------------
  function startWorker() {
    if (worker && workerBackend === settings.backend) return;
    if (worker) worker.terminate();
    workerReady = false;
    workerBackend = settings.backend;
    worker = new Worker(`worker.js?v=${encodeURIComponent(window.SCANNER_VERSION || "")}`);
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
      setStatus(stream ? "" : "Ready. Tap Start camera.");
      updateUi();
    } else if (msg.type === "result") {
      onResult(msg);
    } else if (msg.type === "error") {
      setStatus(`OCR error: ${msg.message}`);
      toCamera();
    }
  }

  // ---- camera ------------------------------------------------------------------------------
  async function startCamera() {
    if (stream || starting) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Camera blocked: the app must be opened over https://");
      return;
    }
    starting = true;
    els.startBig.disabled = true;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      els.video.srcObject = stream;
      await els.video.play();
      track = stream.getVideoTracks()[0];
      try { await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (_) { /* optional */ }
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      els.torch.hidden = !caps.torch;
      try { sessionStorage.setItem(CAMERA_FLAG, "1"); } catch (_) { /* storage may be blocked */ }
      els.placeholder.style.display = "none";
      layout();
    } catch (err) {
      stream = null;
      els.startBig.disabled = false;
      setStatus(`Camera error: ${err.name || ""} ${err.message || err}`);
    } finally {
      starting = false;
      updateUi();
    }
  }

  // Turn the light off before letting go of the camera: some Android phones keep the torch on
  // otherwise, even after scanning has finished.
  async function stopCamera() {
    if (!stream) return;
    const s = stream, t = track;
    stream = null;
    track = null;
    if (torchOn && t) {
      try { await t.applyConstraints({ advanced: [{ torch: false }] }); } catch (_) { /* no torch control */ }
    }
    torchOn = false;
    els.torch.textContent = "Light";
    s.getTracks().forEach((x) => x.stop());
    els.video.srcObject = null;
    updateUi();
  }

  // Leaving the page (switching versions, closing the tab): release the camera right away.
  function stopNow() {
    if (!stream) return;
    stream.getTracks().forEach((x) => x.stop());
    stream = null;
    track = null;
  }

  function cameraWasAllowed() {
    try { return sessionStorage.getItem(CAMERA_FLAG) === "1"; } catch (_) { return false; }
  }

  // ---- photo -> OCR -> Streamlit -------------------------------------------------------------
  function takePicture() {
    const v = els.video;
    if (!stream || !workerReady || mode !== "camera" || !v.videoWidth) return;
    const s = Math.min(1, settings.captureMax / Math.max(v.videoWidth, v.videoHeight));
    const w = Math.round(v.videoWidth * s), h = Math.round(v.videoHeight * s);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const c = canvas.getContext("2d", { willReadFrequently: true });
    c.drawImage(v, 0, 0, w, h);
    const img = c.getImageData(0, 0, w, h);
    photo = { seq: ++seq, canvas, w, h, lines: [], acked: false, started: performance.now() };
    review = null;
    mode = "reading";
    worker.postMessage({ type: "frame", id: photo.seq, buffer: img.data.buffer, width: w, height: h }, [img.data.buffer]);
    updateUi();
  }

  function onResult(msg) {
    if (!photo || msg.id !== photo.seq) return;
    photo.lines = msg.lines.map((l, i) => {
      const words = l.words.map((w) => ({ ...w, ok: w.conf >= settings.confThresh }));
      return { ...l, id: `p${photo.seq}-${i}`, words, ok: words.length > 0 && words.every((w) => w.ok) };
    });
    photo.ms = performance.now() - photo.started;
    mode = "review";
    Streamlit.setValue({
      event: "photo",
      seq: photo.seq,
      step: step.id,
      size: [photo.w, photo.h],
      lines: photo.lines.map((l) => ({
        id: l.id, text: l.text, conf: Math.round(l.conf * 1000) / 1000, ok: l.ok,
        box: l.box.map((v) => Math.round(v)), angle: Math.round(l.angle * 1e4) / 1e4,
      })),
    });
    updateUi();
  }

  function toCamera() {
    mode = "camera";
    photo = null;
    review = null;
    updateUi();
  }

  function retake() {
    const old = photo ? photo.seq : seq;
    toCamera();
    Streamlit.setValue({ event: "retake", seq: old, step: step.id });
  }

  // ---- drawing -----------------------------------------------------------------------------
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const cw = els.canvas.clientWidth, ch = els.canvas.clientHeight;
    if (els.canvas.width !== Math.round(cw * dpr) || els.canvas.height !== Math.round(ch * dpr)) {
      els.canvas.width = Math.round(cw * dpr);
      els.canvas.height = Math.round(ch * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    if (!photo || mode === "camera") return;

    // the whole photo, letterboxed, so nothing that was read is hidden
    const s = Math.min(cw / photo.w, ch / photo.h);
    const ox = (cw - photo.w * s) / 2, oy = (ch - photo.h * s) / 2;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cw, ch);
    ctx.drawImage(photo.canvas, ox, oy, photo.w * s, photo.h * s);
    const px = (x) => ox + x * s, py = (y) => oy + y * s;

    const found = new Set(review && review.seq === photo.seq ? review.ids : []);
    let labelAt = null;
    for (const l of photo.lines) {
      const [x0, y0, x1, y1] = l.box;
      const co = Math.cos(l.angle || 0), si = Math.sin(l.angle || 0);
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, ht = (y1 - y0) / 2;
      const quad = (X0, X1, pad) => {
        // a rectangle along the (possibly tilted) text line covering x range X0..X1
        const xc = (X0 + X1) / 2, yc = cy + (xc - cx) * (si / co), hl = (X1 - X0) / 2 / co + pad;
        ctx.beginPath();
        for (const [u, v] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
          ctx.lineTo(px(xc + u * hl * co - v * (ht + pad) * si), py(yc + u * hl * si + v * (ht + pad) * co));
        }
        ctx.closePath();
      };
      if (found.has(l.id)) {
        quad(x0, x1, 3 / s);
        ctx.fillStyle = "rgba(59,130,246,0.22)";
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = COLORS.found;
        ctx.stroke();
        if (!labelAt || py(y0) < labelAt.y) labelAt = { x: px(x0), y: py(y0) - 4 };
        continue;
      }
      ctx.lineWidth = 1.5;
      for (const w of l.words) {
        ctx.strokeStyle = w.ok ? COLORS.ok : COLORS.bad;
        quad(w.x0, w.x1, 0);
        ctx.stroke();
      }
    }
    if (labelAt && review.label) tag(review.label, Math.max(2, labelAt.x), Math.max(16, labelAt.y), COLORS.found);
  }

  function tag(text, x, y, color) {
    ctx.save();
    ctx.font = "600 12px system-ui, sans-serif";
    const w = ctx.measureText(text).width + 10;
    ctx.fillStyle = color;
    ctx.fillRect(x, y - 16, w, 16);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + 5, y - 4);
    ctx.restore();
  }

  // ---- UI state ------------------------------------------------------------------------------
  function updateUi() {
    els.banner.innerHTML = "";
    if (step.title || step.prompt) {
      const b = document.createElement("b");
      b.textContent = step.title;
      const sp = document.createElement("span");
      sp.textContent = step.prompt;
      els.banner.append(b, sp);
    }
    els.shoot.hidden = mode !== "camera";
    els.shoot.disabled = !(stream && workerReady && step.capture);
    els.shoot.textContent = workerReady ? "Take picture" : "Loading OCR...";
    els.retake.hidden = mode === "camera";
    els.retake.disabled = mode === "reading";
    let hint = "";
    if (mode === "reading") hint = "Reading the text...";
    else if (mode === "review" && photo) {
      hint = photo.lines.length ? `${photo.lines.length} lines read in ${(photo.ms / 1000).toFixed(1)} s` : "No text found - retake closer";
    } else if (stream && !step.capture) hint = "Camera paused";
    els.hint.textContent = hint;
    draw();
  }

  function setStatus(text) {
    els.status.textContent = text;
  }

  function layout() {
    const width = els.stage.clientWidth || 360;
    const screenH = Math.min(window.screen.height || 800, window.outerHeight || 10000);
    const maxH = Math.max(300, screenH * 0.55);
    const v = els.video;
    const aspect = v.videoWidth ? v.videoHeight / v.videoWidth : 3 / 4;
    els.stage.style.height = `${Math.round(Math.min(width * aspect, maxH))}px`;
    Streamlit.setFrameHeight(contentHeight());
    draw();
  }

  // the iframe's own height would feed back into documentElement.scrollHeight, so measure the content
  function contentHeight() {
    const app = document.getElementById("app");
    return app.hidden ? 0 : Math.ceil(app.getBoundingClientRect().height);
  }

  // ---- Streamlit render events -----------------------------------------------------------------
  function onRender(args) {
    if (args.settings) {
      const prev = settings;
      settings = { ...settings, ...args.settings };
      if (!worker || prev.backend !== settings.backend) startWorker();
      else if (prev.detLimit !== settings.detLimit) worker.postMessage({ type: "options", options: { detLimit: settings.detLimit } });
    }
    const next = args.step || {};
    if (next.id !== step.id) {
      step = { ...next };
      toCamera(); // a new step always starts from the live camera
    } else {
      step = { ...next };
    }
    if (photo && args.review && args.review.seq === photo.seq) {
      review = args.review;
      photo.acked = true;
    } else if (photo && photo.acked && !args.review) {
      toCamera(); // Streamlit is done with this photo (confirmed or cleared)
    }
    if (step.capture && !stream && cameraWasAllowed() && !document.hidden) startCamera();
    if (!step.capture) stopCamera();
    const hidden = step.show === false;
    if (document.getElementById("app").hidden !== hidden) {
      document.getElementById("app").hidden = hidden;
      Streamlit.setFrameHeight(hidden ? 0 : contentHeight());
    }
    updateUi();
  }

  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "streamlit:render") onRender(e.data.args || {});
  });
  window.addEventListener("pagehide", stopNow);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopCamera();
    else if (step.capture && cameraWasAllowed()) startCamera();
  });
  els.startBig.addEventListener("click", startCamera);
  els.shoot.addEventListener("click", takePicture);
  els.retake.addEventListener("click", retake);
  els.torch.addEventListener("click", async () => {
    torchOn = !torchOn;
    try { await track.applyConstraints({ advanced: [{ torch: torchOn }] }); } catch (_) { torchOn = false; }
    els.torch.textContent = torchOn ? "Light off" : "Light";
  });
  els.video.addEventListener("loadedmetadata", layout);
  window.addEventListener("resize", layout);
  new ResizeObserver(() => Streamlit.setFrameHeight(contentHeight())).observe(document.getElementById("app"));

  // read-only handle for debugging and the end-to-end test
  window.__scanner = { get mode() { return mode; }, get photo() { return photo; }, get review() { return review; }, get step() { return step; }, get cameraOn() { return !!stream; } };
  Streamlit.ready();
  // the loader (index.html) already received Streamlit's first render, which chose this version
  if (window.__firstRender) onRender(window.__firstRender.args || {});
  layout();
  updateUi();
})();
