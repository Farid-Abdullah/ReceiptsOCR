# Receipt Scanner

A streamlit app for reading shop receipts with the camera. It extracts the store name, date, time,
total, item count and the list of items (name, quantity, unit price, amount). It then checks that
the items add up to the total.

It's a **[Streamlit](https://streamlit.io) application**. The camera and all of the text
recognition run **on the phone, inside the browser**, as a custom Streamlit component written in
plain JavaScript. The models are PaddleOCR PP-OCRv5, run with ONNX Runtime Web. Python only
receives the recognized text and turns it into fields. So:

* Speed depends on the phone, no server side logic.
* Currently everything in the app is static and memoryless.

**Try it on your phone: [receiptsocr.streamlit.app](https://receiptsocr.streamlit.app)**

## Two versions

Both versions are in the app; switch between them at the top of the page. Each keeps its own
progress. Scanner settings are in the ⚙️ side panel, which opens on the first visit.

### Version 1: live scanning (default)

The first version needed no pictures: you slid the phone down the receipt and the camera read it
continuously.

* **Tracking:** frame-to-frame motion tracking and text matching placed every video frame on one
  receipt, so text already seen was recognized again instead of being added twice.
* **Voting:** each word was voted on across frames. Its box turned green once several frames
  agreed, and stayed red while unsure.
* **Live checks:** the items were checked against the total as you scanned. The products section
  turned **orange** on the camera view when they didn't add up.

It was the first approach. Scanning a moving receipt turned out slow and error-prone on a phone,
which led to Version 2.

### Version 2: guided photos

Instead of scanning continuously, the app walks through the receipt one part at a time and asks for a picture of each. Steps:

1. Store name
2. Date and time
3. Total
4. Number of items
5. Optionally the items, in several overlapping pictures

After each picture, the text found is boxed on the photo: **blue** for the value it was looking
for, **green** for words read clearly and **red** for unsure ones. You confirm a candidate, pick
another, type the value yourself, retake the picture, or skip the step. The summary shows every
field and item as editable values, and the checks (do the items add up to the total, does the
item count match). Exporting to JSON is turned off while the results aren't reliable.

## Status

This was a failed experiment. I needed to reliably read and extract all the info from receipts seemlessly. The OCR itself reads printed receipt text
well. Turning that text into fields is the hard part, and it is extremely unreliable on real fresh photos that might have blurrs, artifacts, wrinkled receipts etc. The parsing is also not good enough, there needs to be more business logic that validates the fields that are read, a way to deal with duplicate reads(in case of multiple photos per receipt).

* Layouts differ a lot between shops, so items can be misread.

The fields can always be corrected by hand in the app, but the whole point of this is finding a cheap way to scan receipt and extract all info accurately.

## How to run

### Run it locally (not recommended, try hosting, streamlit steps below)

On first start the app downloads the OCR models (about 45 MB).

* This might be useful only to check the UI, but then again its just a standard streamlit UI. 
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv\Scripts\python serve.py
```

Open `http://localhost:8501`; the app is served to this computer only.


### Very easy to host on streamlit

Phone browsers only allow the camera on `https://` pages, so to use a phone the app has to be
hosted. Streamlit Community Cloud does this for free:

1. Push the repo to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), choose **Create app**: your repo, branch
   `main`, main file `app.py`, and Python 3.13 under *Advanced settings*.
3. Open the app's `https://….streamlit.app` address on the phone.

Every push to `main` redeploys the app. If it behaves oddly after an update, use **⋮ → Reboot app**.

## How it works

```
phone browser (scanner/frontend)                          Streamlit (Python)
┌──────────────────────────────────────────────────┐      ┌───────────────────────────────┐
│ camera ─► picture / live ─► worker.js:           │ text │ views/: the two versions      │
│   PP-OCRv5 text detector + English recognizer    ├─────►│ receipt_parser.py: finds the  │
│   (ONNX Runtime Web, WASM or WebGPU)             │      │  store, date, time, total,    │
│ the picture with what was found outlined         │◄─────┤  item count and items, and    │
│                                                  │ ids  │  checks they add up           │
└──────────────────────────────────────────────────┘      └───────────────────────────────┘
```

* **OCR:** the PP-OCRv5 mobile text detector finds text lines as rotated boxes, so a tilted
  receipt still reads. The English recognizer `en_PP-OCRv5_rec_mobile` gives per-character
  confidence and word positions.
* **Rows:** text pieces are grouped into printed rows by following each line's own slope, which
  handles curved paper.
* **Amounts:** decimals, whole numbers with a currency (`66 QR`), rows of numbers that agree
  (`1 8 8`), thousands separators, and prices whose decimal point the OCR dropped (`4 00`).
* **Items:** the parser handles three layouts: the name on the same row as the price, the name on
  the row above, and barcode-first receipts with the name below the price row.
* **Total:** every amount is scored by its label and by whether the receipt confirms it. Words
  like TOTAL, NET AMOUNT or AMOUNT DUE count for it; cash, change, points, rounding and savings
  count against it. A value printed more than once, or equal to subtotal + adjustments or to
  cash − change, also counts for it.

## Project layout

| Path | |
|---|---|
| `app.py` | entry point: settings sidebar, version switch |
| `views/guided.py`, `views/live.py` | Version 2 (guided photos) and Version 1 (live scan) |
| `serve.py` | local run (`python serve.py`): this computer only, with multi-threaded OCR |
| `receipt_parser.py` | the finder for each step, merging overlapping item pictures, the checks |
| `scanner/` | the camera component (`frontend/` is plain JavaScript, no build step; `frontend/live/` is Version 1) |
| `scanner/assets.py` | downloads the models and ONNX Runtime Web on first start, with checksums |
| `tests/` | parser tests on common receipt layouts; `js/` has an OCR command-line tool and an end-to-end test |

## Tests

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests          # parser tests on common receipt layouts

cd tests\js; npm install; npx playwright install chromium
node ocr_cli.js photo.jpg                     # what the app's OCR reads from a photo (JSON)
node e2e.js photo.jpg                         # both versions in Chromium, the photo as the camera
```

The end-to-end test needs the app running (`python serve.py`). No receipt photos are included;
use your own.

## Notes

* English text only. Arabic shows up as red boxes and is ignored.
* Numeric dates are read day-first (18/10/2026), as printed in Qatar.
