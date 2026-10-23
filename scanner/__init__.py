"""Live camera receipt scanner as a Streamlit custom component.

All OCR runs in the phone's browser (ONNX Runtime Web); Python only receives the de-duplicated
text lines and sends back the receipt analysis that colours the overlay.
"""
from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "frontend"
_component = components.declare_component("receipt_scanner", path=str(_FRONTEND))


def receipt_scanner(*, settings: dict, analysis: dict | None, reset_nonce: int, key: str):
    """Render the scanner. Returns {"session", "version", "lines": [...], "stats"} or None."""
    return _component(settings=settings, analysis=analysis, reset_nonce=reset_nonce, key=key, default=None)


def assets_present() -> bool:
    needed = ["models/det.onnx", "models/rec_en.onnx", "models/rec_en_dict.txt", "vendor/ort/ort.min.js"]
    return all((_FRONTEND / p).exists() for p in needed)
