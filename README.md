---
title: Receipts OCR
emoji: 🧾
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Live camera receipt scanner with in-browser OCR
---

# Receipt scanner

A Streamlit app for your phone: point the camera at a receipt and slide slowly from top to bottom.
Text is read live, without taking photos. Each word gets a box: **green** once several frames
agree on it, **red** while it is uncertain. Text already read is recognised again rather than
duplicated. The app pulls out the vendor, date, time, items (code, quantity, unit price, line
total), the printed item count and the total. When the items don't add up to the total, the whole
products section turns **orange** on the camera view.

All OCR runs **in the phone's browser**. Python only receives the de-duplicated text and does the
receipt parsing and validation. Receipt images never leave the phone.

## Deploy (Hugging Face Spaces)

The app runs as a Docker Space (`Dockerfile`; the Space settings are the header at the top of this
file). The GitHub Action `.github/workflows/sync-to-hf.yml` copies the `main` branch to the Space
on every push or merge, and the Space then rebuilds itself. Other branches are never deployed.

One-time setup:

1. On Hugging Face, create a Space: SDK **Docker**, template **Blank**, hardware **CPU basic**.
2. Create a Hugging Face access token of type **Write**.
3. In the GitHub repo, open **Settings → Secrets and variables → Actions** and add:
   * secret `HF_TOKEN`: the token
   * variable `HF_USERNAME`: your Hugging Face username
   * variable `HF_SPACE`: `your-username/space-name`
4. Push to `main`, or use **Actions → Deploy to Hugging Face → Run workflow**.

Open the app on the phone at its direct address, `https://<username>-<space-name>.hf.space`. You
can copy it from the Space's **⋮ → Embed this Space** menu. Don't use the `huggingface.co/spaces/...`
page: it shows the app inside a frame, where the camera may be blocked and multi-threading is off.

Phones only allow the camera on `https://` pages. Hugging Face provides the HTTPS, so no
certificates are involved.

## Run it on your PC (development)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\setup_assets.py
.venv\Scripts\python -m streamlit run serve.py
```

`setup_assets.py` downloads the OCR models (about 12 MB) and the browser runtime once. Open
`http://localhost:8501` in a desktop browser with a webcam. Browsers treat `localhost` as secure,
so the camera works without HTTPS there. To try a change on the phone, deploy it.

## Using it

1. Tap **Start camera** and let the receipt's width fill the view.
2. Move slowly downwards. Each frame must overlap the previous one, because that overlap is how
   the app knows where it is on the receipt.
3. **Hold steady** means the picture is too blurry to read. **Lost track** means it can't place
   the view on the receipt; move back over text it has already read.
4. When the total has been read, the items are checked against it. If they don't match, the
   products section turns orange and the results list which check failed. Rescan the red words or
   the orange area.
5. Tap **New receipt** to start over. **Download JSON** exports the result.

The sidebar (the `>` at the top left) has a few settings. *Detection resolution* trades small-print
reading for speed. *Inference backend* lets you try WebGPU on recent phones. *Green box
confidence* and *Frames that must agree* set how strict "green" is.

## How it works

```
phone browser (scanner/frontend)                                PC (Python)
┌────────────────────────────────────────────────────────┐      ┌──────────────────────┐
│ camera ─► motion.js (frame-to-frame shift, 30 fps)     │      │ app.py               │
│        └► worker.js: PP-OCRv5 detector + English       │ text │ receipt_parser.py    │
│           recognizer (ONNX Runtime Web, WASM/WebGPU)   ├─────►│  rows, fields,       │
│        ─► tracker.js: place each frame on the receipt, │      │  items, validation   │
│           merge repeat sightings, vote on the text     │◄─────┤                      │
│        ─► overlay: green / red / orange boxes          │roles │                      │
└────────────────────────────────────────────────────────┘      └──────────────────────┘
```

* **OCR:** PaddleOCR PP-OCRv5 mobile models in ONNX form. The detector is DBNet; it finds the text
  lines as rotated boxes, so a tilted receipt still reads. The recognizer is SVTR with CTC
  decoding; it gives per-character confidence and word positions.
* **No duplicates:** each OCR frame is placed on one receipt coordinate system. That uses the
  camera-motion estimate plus anchor lines whose text matches lines already read. Lines seen
  before get another vote and new lines are appended. Two genuinely identical items, such as two
  `BANANAS` lines, sit at different places on the receipt, so both are kept.
* **Speed:** lines already confirmed are not re-read every frame. The page is cross-origin
  isolated (`serve.py` adds COOP/COEP headers), so ONNX Runtime can use several CPU cores.

### Performance to expect

Measured on this PC in headless Chromium (WASM, 4 threads):

* Detection takes about 300 ms per frame.
* Recognition takes about 600 ms for a fresh view, dropping to about 250 ms once most visible
  lines are known.
* That works out to **1–2 scans per second**. The video and the boxes still move at full frame
  rate between scans.

Phones vary a lot. Expect roughly half that speed on a mid-range Android phone, and similar or
better on a recent iPhone or flagship. If scanning feels slow, try lowering *Detection
resolution* to 736, or try the WebGPU backend.

## Project layout

| Path | |
|---|---|
| `serve.py` | ASGI entry (Streamlit `st.App`) that adds the isolation headers |
| `Dockerfile`, `.github/workflows/` | Hugging Face Space build and deployment |
| `app.py` | the Streamlit page: settings, results, export |
| `receipt_parser.py` | text rows → vendor/date/time/items/total and the validation checks |
| `scanner/` | the camera component (`frontend/` is plain JS, no build step) |
| `scripts/setup_assets.py` | downloads the models and onnxruntime-web, with checksums |

## Tests

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python tests\make_receipt.py --video     # synthetic receipt images and camera-pan videos
.venv\Scripts\python -m pytest tests                    # parser

cd tests\js
npm install
npm test                                                 # OCR core, motion, tracker (real models, Node)
npx playwright install chromium
npm run e2e                                              # whole app with a fake camera (app must be running)
```

The tracker tests pan a virtual camera over the receipt, including a tilted receipt, zoom drift,
fast scans, jumps and scanning back and forth. They check that every printed row comes out exactly
once and that every green word is correct.

## Notes and limits

* English only. Arabic or other scripts show up as red boxes and are ignored by the parser.
* The parser uses heuristics tuned to common receipt layouts: `qty @ price`, `QTY n`, codes before
  the name, tax flags after prices, and discount lines. Unusual layouts may need a tweak in
  `receipt_parser.py`.
* When running locally, restart `streamlit run serve.py` after editing `receipt_parser.py`. In ASGI mode, Streamlit only reloads
  `app.py` by itself.
* For debugging, the overlay's top-right readout shows the backend, scans per second, milliseconds
  for detection + recognition, and boxes read / skipped.
