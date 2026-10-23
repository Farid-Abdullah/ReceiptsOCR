// OCR worker: owns the ONNX sessions so inference never blocks the camera/overlay thread.
/* global ort, OCRCore */
importScripts("vendor/ort/ort.min.js?v=14", "ocr-core.js?v=14");

let sessions = null;
let chars = null;
let opts = { detLimit: 960 };

async function createSessions(backend) {
  const providers = backend === "webgpu" ? ["webgpu", "wasm"] : ["wasm"];
  const so = { executionProviders: providers, graphOptimizationLevel: "all" };
  const [det, rec] = await Promise.all([
    ort.InferenceSession.create("models/det.onnx", so),
    ort.InferenceSession.create("models/rec_en.onnx", so),
  ]);
  return { det, rec };
}

async function init(msg) {
  ort.env.wasm.wasmPaths = new URL("vendor/ort/", self.location.href).href;
  // Threads need SharedArrayBuffer, i.e. a cross-origin isolated page (serve.py sends COOP/COEP).
  const threads = self.crossOriginIsolated ? Math.max(1, Math.min(4, (self.navigator.hardwareConcurrency || 2) - 1)) : 1;
  ort.env.wasm.numThreads = threads;
  ort.env.logLevel = "error";
  let backend = msg.backend === "webgpu" && self.navigator.gpu ? "webgpu" : "wasm";
  try {
    sessions = await createSessions(backend);
  } catch (err) {
    if (backend === "wasm") throw err;
    backend = "wasm";
    sessions = await createSessions(backend);
  }
  chars = OCRCore.parseDict(await (await fetch("models/rec_en_dict.txt")).text());
  // warm-up so the first real frame is not slow
  const blank = { data: new Uint8ClampedArray(320 * 96 * 4).fill(255), width: 320, height: 96 };
  await OCRCore.runOCR(ort, sessions, chars, blank, { detLimit: 320 });
  postMessage({ type: "ready", backend, threads });
}

self.onmessage = async (e) => {
  const msg = e.data;
  try {
    if (msg.type === "init") {
      await init(msg);
    } else if (msg.type === "options") {
      Object.assign(opts, msg.options);
    } else if (msg.type === "frame") {
      const img = { data: new Uint8ClampedArray(msg.buffer), width: msg.width, height: msg.height };
      const res = await OCRCore.runOCR(ort, sessions, chars, img, { ...opts, hints: msg.hints });
      postMessage({ type: "result", id: msg.id, lines: res.lines, timing: res.timing, buffer: msg.buffer }, [msg.buffer]);
    }
  } catch (err) {
    postMessage({ type: "error", id: msg.id, message: String((err && err.message) || err) });
  }
};
