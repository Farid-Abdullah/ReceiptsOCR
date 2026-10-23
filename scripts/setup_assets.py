"""Download the OCR models and the onnxruntime-web runtime into the component folder.

Run once:  python scripts/setup_assets.py
Everything ends up under scanner/frontend/ so the phone loads it from the PC, not the internet.
"""
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "scanner" / "frontend"
MODELS = FRONTEND / "models"
ORT_DIR = FRONTEND / "vendor" / "ort"

RAPID = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0"
MODEL_FILES = {
    "det.onnx": (
        f"{RAPID}/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    "rec_en.onnx": (
        f"{RAPID}/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx",
        "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
    ),
    "rec_en_dict.txt": (
        f"{RAPID}/paddle/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile/ppocrv5_en_dict.txt",
        None,
    ),
}

ORT_VERSION = "1.22.0"
ORT_TGZ = f"https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-{ORT_VERSION}.tgz"
ORT_KEEP = {
    "ort.min.js",
    "ort-wasm-simd-threaded.mjs",
    "ort-wasm-simd-threaded.wasm",
    "ort-wasm-simd-threaded.jsep.mjs",
    "ort-wasm-simd-threaded.jsep.wasm",
}


def fetch(url: str) -> bytes:
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "receipt-scanner-setup"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    ORT_DIR.mkdir(parents=True, exist_ok=True)

    for name, (url, sha) in MODEL_FILES.items():
        dest = MODELS / name
        if dest.exists() and (sha is None or hashlib.sha256(dest.read_bytes()).hexdigest() == sha):
            print(f"ok   {name}")
            continue
        data = fetch(url)
        if sha and hashlib.sha256(data).hexdigest() != sha:
            print(f"FAIL {name}: checksum mismatch", file=sys.stderr)
            return 1
        dest.write_bytes(data)
        print(f"got  {name} ({len(data) / 1e6:.1f} MB)")

    if all((ORT_DIR / f).exists() for f in ORT_KEEP):
        print(f"ok   onnxruntime-web {ORT_VERSION}")
    else:
        with tarfile.open(fileobj=io.BytesIO(fetch(ORT_TGZ)), mode="r:gz") as tar:
            for member in tar.getmembers():
                fname = member.name.rsplit("/", 1)[-1]
                if member.name.startswith("package/dist/") and fname in ORT_KEEP:
                    (ORT_DIR / fname).write_bytes(tar.extractfile(member).read())
        print(f"got  onnxruntime-web {ORT_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
