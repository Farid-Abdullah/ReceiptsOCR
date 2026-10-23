"""OCR models and the onnxruntime-web runtime, downloaded into the component folder.

They are not stored in git. The app fetches them on first start (Streamlit Community Cloud has
no build step); `python -m scanner.assets` does the same by hand.
"""
import hashlib
import io
import os
import sys
import tarfile
import threading
import urllib.request
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent / "frontend"
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

_lock = threading.Lock()  # two visitors on a fresh server must not download into the same files


def present() -> bool:
    return all((MODELS / name).exists() for name in MODEL_FILES) and all((ORT_DIR / f).exists() for f in ORT_KEEP)


def _fetch(url: str) -> bytes:
    print(f"  downloading {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "receipt-scanner-setup"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _write(dest: Path, data: bytes) -> None:
    # write then rename, so a half-written file is never served
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def ensure() -> None:
    """Download whatever is missing. Raises on network or checksum errors."""
    with _lock:
        MODELS.mkdir(parents=True, exist_ok=True)
        ORT_DIR.mkdir(parents=True, exist_ok=True)

        for name, (url, sha) in MODEL_FILES.items():
            dest = MODELS / name
            if dest.exists() and (sha is None or hashlib.sha256(dest.read_bytes()).hexdigest() == sha):
                continue
            data = _fetch(url)
            if sha and hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError(f"{name}: checksum mismatch")
            _write(dest, data)
            print(f"got  {name} ({len(data) / 1e6:.1f} MB)", flush=True)

        if not all((ORT_DIR / f).exists() for f in ORT_KEEP):
            with tarfile.open(fileobj=io.BytesIO(_fetch(ORT_TGZ)), mode="r:gz") as tar:
                for member in tar.getmembers():
                    fname = member.name.rsplit("/", 1)[-1]
                    if member.name.startswith("package/dist/") and fname in ORT_KEEP:
                        _write(ORT_DIR / fname, tar.extractfile(member).read())
            print(f"got  onnxruntime-web {ORT_VERSION}", flush=True)


if __name__ == "__main__":
    try:
        ensure()
    except Exception as err:  # noqa: BLE001 - report any failure as a CLI error
        print(f"FAIL {err}", file=sys.stderr)
        sys.exit(1)
    print("assets ready")
