# Receipt scanner

A Streamlit app for your phone that reads a receipt step by step. Each step asks for one picture:
the store name, the date and time, the total, the item count, and then (optionally) the items, a
few lines per picture. After each picture the app outlines what it found and lists the candidates.
You confirm one, retake the picture, type the value yourself, or skip the step. The summary
shows everything as editable fields and an item table, and checks that the items add up to the
total.

All OCR runs **in the phone's browser**. Python only receives the recognized text and does the
receipt parsing and validation. Receipt images never leave the phone.

## Deploy (Streamlit Community Cloud)

The app is deployed from the `main` branch of the GitHub repo. Every push or merge to `main`
redeploys it automatically; other branches are never deployed.

One-time setup on [share.streamlit.io](https://share.streamlit.io):

1. Sign in with GitHub and choose **Create app → Deploy a public app from GitHub**.
2. Repository `Farid-Abdullah/ReceiptsOCR`, branch `main`, main file `app.py`.
3. Under **Advanced settings**, choose Python **3.13** (3.11 or newer is required).
4. Pick the app address (`something.streamlit.app`) and click **Deploy**.

The first start downloads the OCR models (about 45 MB) into the app, which takes a minute.
Streamlit Cloud provides the HTTPS that phones need for the camera.

## Run it on your PC (development)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run serve.py
```

Open `http://localhost:8501` in a desktop browser with a webcam. Browsers treat `localhost` as
secure, so the camera works without HTTPS there. The first start downloads the OCR models. To try
a change on the phone, deploy it.

`serve.py` runs the same `app.py` but adds cross-origin isolation headers, which let the OCR use
several CPU cores. Streamlit Cloud shows apps inside its own page, where that isolation isn't
possible, so the deployed app runs the OCR on one core. That's slower, but it works.

## Using it

1. Tap **Start camera** (once; the app remembers it for the session).
2. Each step says what to photograph at the top of the camera view. Fit that part of the receipt
   in the frame, hold still and tap **Take picture**.
3. The picture freezes. What the app found is outlined in **blue**. Other text is boxed **green**
   when read clearly and **red** when unsure. Below the camera, pick the right candidate and tap
   **Confirm**. You can also choose *Type it myself*, tap **Retake**, or **Skip** the step.
4. For the items, photograph the list from the top down, a few lines per picture. Each picture
   shows the items it found; tap **Add** to keep them. Overlapping pictures are fine: items already
   added from the previous picture are skipped, and the app says how many. **Undo last** removes
   the last batch.
5. The summary lets you edit any field or item and shows whether the items add up to the total.
   **Download JSON** exports the result; **Scan another receipt** starts over.

The sidebar (the `>` at the top left) has a few settings. *Detection resolution* trades small-print
reading for speed. *Inference backend* lets you try WebGPU on recent phones. *Green box
confidence* sets how strict "read clearly" is.

## How it works

```
phone browser (scanner/frontend)                          Streamlit (Python)
┌──────────────────────────────────────────────────┐      ┌───────────────────────────────┐
│ live camera ─► Take picture ─► worker.js:        │ text │ app.py: the steps             │
│   PP-OCRv5 detector + English recognizer         ├─────►│ receipt_parser.py: find_vendor│
│   (ONNX Runtime Web, WASM/WebGPU)                │      │  find_datetime, find_total,   │
│ frozen picture with the found text outlined      │◄─────┤  find_item_count, find_items, │
│                                                  │ ids  │  merge_items, summarize       │
└──────────────────────────────────────────────────┘      └───────────────────────────────┘
```

* **OCR:** PaddleOCR PP-OCRv5 mobile models in ONNX form. The detector is DBNet; it finds the text
  lines as rotated boxes, so a tilted receipt still reads. The recognizer is SVTR with CTC
  decoding; it gives per-character confidence and word positions.
* **Finding fields:** the text is grouped into printed rows (undoing any tilt). Each step has a
  finder. The store name is the biggest line of words, ranked ahead of addresses and phone
  numbers. Date and time come from patterns. The total is the row saying TOTAL, not SUBTOTAL; if
  no row says it, every amount is offered. The item count comes from text like `ITEMS SOLD: 15`.
  Items are rows with a price, including `qty @ price` lines.
* **Overlapping item pictures:** the longest run of items at the end of the list that repeats at
  the start of a new picture is skipped. Two genuinely identical items in different places on the
  receipt, such as two `BANANAS` lines, are both kept.

### Performance to expect

Measured on this PC in headless Chromium, for a picture with about 50 lines of text:

* About **3 s** per picture with `serve.py` locally (multi-threaded).
* About **4.5 s** per picture the way Streamlit Cloud runs it (single core).

Phones are slower; expect several seconds per picture. Pictures of a small area (just the total,
just the store name) read faster than the whole receipt. If it feels slow, lower *Detection
resolution* to 960 or try the WebGPU backend.

## Project layout

| Path | |
|---|---|
| `serve.py` | ASGI entry (Streamlit `st.App`) that adds the isolation headers |
| `app.py` | the Streamlit page: the scan steps, the summary, export |
| `receipt_parser.py` | the finders for each step, item merging and the validation checks |
| `scanner/` | the camera component (`frontend/` is plain JS, no build step) |
| `scanner/assets.py` | downloads the models and onnxruntime-web on first start, with checksums |

## Tests

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python tests\make_receipt.py --video     # synthetic receipt images and fake camera feeds
.venv\Scripts\python -m pytest tests                    # parser and step finders

cd tests\js
npm install
npm test                                                 # OCR core with the real models (Node)
npx playwright install chromium
npm run e2e                                              # whole guided scan with a fake camera (app must be running)
```

The end-to-end test takes a picture at every step and confirms what was found. It adds the items
twice from completely overlapping pictures, checking that nothing is added twice, and then checks
the summary. It runs for a correct receipt, one whose total doesn't match, and a tilted receipt.

## Notes and limits

* English only. Arabic or other scripts show up as red boxes and are ignored by the parser.
* The parser uses heuristics tuned to common receipt layouts: `qty @ price`, `QTY n`, codes before
  the name, tax flags after prices, and discount lines. Unusual layouts may need a tweak in
  `receipt_parser.py`.
* When running locally, restart `streamlit run serve.py` after editing `receipt_parser.py`. In ASGI mode, Streamlit only reloads
  `app.py` by itself.
