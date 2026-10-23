# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows dev machine; the venv is `.venv` (use `.venv/Scripts/python`).

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
cd tests\js; npm install; npx playwright install chromium; cd ..\..   # Node test tooling

.venv\Scripts\python serve.py                       # local dev: localhost only, multi-threaded OCR
.venv\Scripts\python -m pytest tests                # all Python tests
.venv\Scripts\python -m pytest tests/test_layouts.py::test_lulu_style
cd tests\js; node e2e.js path	o\photo.jpg          # full app in Chromium; needs the app on :8501 (or APP_URL)
node tests/js/ocr_cli.js --crop 0.74,0.93 --scale 2 photo.jpg   # raw OCR JSON for a photo / close-up band
```

There is no build step and no linter configured. OCR models and ONNX Runtime Web are not in git:
`scanner/assets.py` downloads them into `scanner/frontend/{models,vendor}` on first app start (or
`python -m scanner.assets`).

## Architecture

**Split between browser and Python.** All camera handling and OCR run in the phone's browser inside
a Streamlit v1 custom component (`scanner/frontend/`, plain JS, no bundler). Python only receives
recognized text lines and parses them.

**Two versions in one app.** `app.py` is a router: page config, model download, the shared ⚙️
settings sidebar, which stores `st.session_state["settings"]`, and a segmented control that calls
`st.switch_page` between `st.navigation` pages:

- `views/live.py` is Version 1 (live scan), the default and first page, restored from commit `1221b5a`.
  The default page has an empty `url_path`, so the router compares page titles, not paths.
- `views/guided.py` is Version 2 (guided photos).

Both pages use the same component (`receipt_scanner`) with a different `mode` argument.
`index.html` waits for Streamlit's first render, picks a `<template>` and loads either `main.js`
(guided) or `live/{tracker,motion,main}.js` (live). It keeps that render in
`window.__firstRender`, which each `main.js` replays. Both share `worker.js`/`ocr-core.js` and one
model download. The live version passes `hints` (lines it has already confirmed) to the worker,
which skips re-reading them.

- `ocr-core.js`: the pure PP-OCRv5 pipeline. DBNet detection gives rotated boxes, which are cropped
  rotated for recognition; then SVTR/CTC decoding with word positions. Written UMD-style so the
  same file runs in `worker.js` (onnxruntime-web) and in Node tests (`onnxruntime-node`, via
  `tests/js/ocr_cli.js`). Python evaluation therefore scores the exact browser code.
- `main.js` (guided) and `live/main.js`: the camera, the overlay drawing, and the Streamlit
  protocol (postMessage, hand-written).
  - Both stop the camera by turning the torch off first and then stopping the tracks. Some
    Android phones otherwise keep the light on.
  - The camera is also stopped on `pagehide` and when the page is hidden.
  - Each exposes a read-only `window.__scanner` for tests.
- `index.html`: loads `main.js?v=<fingerprint>` from `build.json`. `scanner/__init__.py` rewrites
  `build.json` with a hash of the frontend files on import. This is the cache-busting mechanism
  for phones after a redeploy; don't add manual `?v=` numbers.

**Component contract** (`scanner/__init__.py` ↔ `main.js`):
- Guided, value sent to Python: `{event: "photo"|"retake", seq, step, size, lines: [{id, text, conf, ok, box, angle}]}`.
- Guided, args sent to the browser: `{mode: "guided", settings, step: {id, title, prompt, capture, show}, review: {seq, ids, label} | None}`.
  On the summary (`show: false`) the component stays mounted but collapses to height 0, so the
  camera can stop cleanly.
- Live, value sent to Python: `{session, version, lines: [{id, text, conf, ok, support, box, angle}], stats}`.
  These are lines in receipt ("document") coordinates, after tracking and voting.
- Live, args sent to the browser: `{mode: "live", settings, analysis: {session, roles, section}, reset_nonce}`.
  The analysis comes from `receipt_parser.read_receipt()`.
- `box` is a "deskewed" box: the projected x-extent by centre ± half thickness, with `angle`
  carried separately. The parser and the drawing code both rely on this.

**`views/guided.py`** is a step machine: vendor → datetime → total → count → ask_items → items →
summary. Component keys are `guided_scanner` and `live_scanner`. JSON export is deliberately
disabled in both versions ("Download JSON (doesn't work)"). Constraints that are easy to break:
- `ss.photo` stores only the raw OCR lines. `finder()` re-analyses them on every rerun, and
  `receipt_parser` is reloaded when its file mtime changes. Both guard against Streamlit Cloud
  rerunning a new `app.py` with stale state or an old module, which caused KeyErrors.
  `finder()` always fills every key the UI reads.
- Nothing may appear or disappear above `receipt_scanner(...)`. A shifting element index makes
  Streamlit remount the iframe, which restarts the camera and reloads the OCR models.
  Conditional content above it goes inside an always-present `st.container()`.
- The item list lives in `ss.scanned_items`, not `ss.items`, which collides with
  `SessionState.items()`.

**`receipt_parser.py`**:
- *Rows:* `_rows()` groups segments into printed rows following each segment's own slope
  (`_line_gap` uses the longer segment's angle), which handles curved paper.
- *Amounts:* `_amount()` handles currency integers (`66 QR`), self-consistent numbers-only rows
  (`1 8 8`) and dropped decimal points (`4 00`).
- *Labels and totals:* `_labelled_amounts()` attaches a label to every amount (same row,
  same-line neighbour, or a confident label-only row above). `_total_scores()` ranks totals by
  keywords (`TOTAL_PHRASE_RE` / `NEVER_TOTAL_RE` / `PAYMENT_RE`), repetition, subtotal +
  adjustments, and cash − change.
- *Summary:* `_summary()` finds where the item list ends, the subtotal and the adjustments
  (tax/discount/delivery/service, signed).
- *Items:* `find_items()` handles three layouts: name on the price row, name above it, and
  barcode-first with the name below (Al Meera).
- *Vendor:* `find_vendor()` checks, in order, a `Vendor:` label, `KNOWN_STORES` (only above the
  first price row), then the largest confident line. Delivery apps and low-confidence junk
  (Arabic read by the English model) rank last.
- *Dates and times:* dates are returned as ISO, read day-first (Qatar); times as 24-hour.
- *Checks:* `validate`/`summarize` check items + adjustments against the total.
- *Version 1:* `read_receipt()` runs every finder over the whole receipt and returns overlay
  `roles` and `section`.

**`serve.py`** wraps `app.py` in `st.App` (ASGI) with COOP/COEP headers. `python serve.py`
re-launches itself via `streamlit run serve.py --server.address localhost`. Local runs
deliberately must not be reachable from other devices; phone testing goes through the hosted
app. Don't put `server.address` in `.streamlit/config.toml`: that file also applies on Streamlit
Cloud. They make the page
cross-origin isolated, so the WASM OCR can use threads. Streamlit Cloud runs `app.py` directly
and is single-threaded. In ASGI mode, edits under `scanner/` need a server restart.

## Tests

The repo contains no receipt photos. The user had real ones once and removed them, along with
everything derived from them, from the code and the git history because they hold personal data.
Don't add receipt photos, OCR output of real receipts, or text copied from them to the repo.

- **`test_layouts.py`:** parser tests on common receipt wording and layouts (LuLu, Carrefour,
  "balance due", thousands separators, points), written as OCR text rows. The user doesn't want
  rendered fake receipt images.
- **`js/ocr_cli.js`:** runs the browser OCR code in Node on any photo (`--crop`, `--scale`,
  `--det-limit`, model overrides).
- **`js/e2e.js`:** takes photo paths as arguments and replaces `getUserMedia` with a canvas stream
  of the photo. Chrome's own fake camera crops frames to 1080 px high. For each photo it:
  - closes the settings sidebar (open on first visit);
  - runs Version 2 to its summary and checks the camera is released and JSON export is disabled;
  - live-scans with Version 1;
  - switches back, and progress must be kept.

## Deployment and status

Deployed on Streamlit Community Cloud from `main`: main file `app.py`, Python 3.13. Every push to
`main` redeploys; other branches are never deployed.

The settings defaults were chosen by measurement: detection at 1280 beat 1600 and 960.
Version 1 also uses capture 1920 and needs 3 frames to agree. The project is marked as an ended
experiment in the README: field extraction (especially the total) is still unreliable on real
receipts.
