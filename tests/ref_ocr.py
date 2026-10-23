"""Python reference of the browser OCR pipeline (same algorithm as scanner/frontend/ocr-core.js).

Used to sanity-check model preprocessing/decoding:  python tests/ref_ocr.py tests/data/receipt.png
"""
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

MODELS = Path(__file__).resolve().parents[1] / "scanner" / "frontend" / "models"
DET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
DET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def detect(sess, rgb, limit=960, thresh=0.3, box_thresh=0.6, unclip=1.6):
    h, w = rgb.shape[:2]
    s = min(1.0, limit / max(h, w))
    nh, nw = max(32, round(h * s / 32) * 32), max(32, round(w * s / 32) * 32)
    img = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR), np.float32) / 255.0
    bgr = img[:, :, ::-1]
    x = ((bgr - DET_MEAN) / DET_STD).transpose(2, 0, 1)[None]
    prob = sess.run(None, {"x": x.astype(np.float32)})[0][0, 0]
    mask = prob > thresh
    # connected components (4-neighbour) via iterative labelling
    labels = np.zeros(mask.shape, np.int32)
    boxes, cur = [], 0
    for sy, sx in zip(*np.nonzero(mask)):
        if labels[sy, sx]:
            continue
        cur += 1
        stack = [(sy, sx)]
        labels[sy, sx] = cur
        ys, xs = [], []
        while stack:
            y, xx = stack.pop()
            ys.append(y); xs.append(xx)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, xx + dx
                if 0 <= ny < nh and 0 <= nx < nw and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = cur
                    stack.append((ny, nx))
        if len(ys) < 6:
            continue
        y0, y1, x0, x1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
        score = prob[ys, xs].mean()
        if score < box_thresh or (y1 - y0) < 3:
            continue
        bw, bh = x1 - x0, y1 - y0
        d = bw * bh * unclip / (2 * (bw + bh))
        boxes.append([(x0 - d) * w / nw, (y0 - d) * h / nh, (x1 + d) * w / nw, (y1 + d) * h / nh, float(score)])
    return boxes


def recognize(sess, chars, rgb, box):
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    crop = Image.fromarray(rgb[int(y0):int(y1), int(x0):int(x1)])
    tw = max(16, int(np.ceil(48 * crop.width / crop.height)))
    img = np.asarray(crop.resize((tw, 48), Image.BILINEAR), np.float32)[:, :, ::-1] / 255.0
    x = ((img - 0.5) / 0.5).transpose(2, 0, 1)[None]
    out = sess.run(None, {"x": x.astype(np.float32)})[0][0]
    idx, conf = out.argmax(1), out.max(1)
    text, confs, prev = "", [], 0
    for t, (i, c) in enumerate(zip(idx, conf)):
        if i != 0 and i != prev:
            text += chars[i]
            confs.append(float(c))
        prev = i
    return text, (min(confs) if confs else 0.0), (float(np.mean(confs)) if confs else 0.0)


def main(path):
    chars = ["<blank>"] + open(MODELS / "rec_en_dict.txt", encoding="utf-8").read().split("\n")[:-1] + [" "]
    det = ort.InferenceSession(str(MODELS / "det.onnx"))
    rec = ort.InferenceSession(str(MODELS / "rec_en.onnx"))
    rgb = np.asarray(Image.open(path).convert("RGB"))
    boxes = detect(det, rgb)
    boxes.sort(key=lambda b: (round(b[1] / 20), b[0]))
    for b in boxes:
        text, cmin, cmean = recognize(rec, chars, rgb, b)
        print(f"{[int(v) for v in b[:4]]} min={cmin:.2f} mean={cmean:.2f} | {text}")


if __name__ == "__main__":
    main(sys.argv[1])
