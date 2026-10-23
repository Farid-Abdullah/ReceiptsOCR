"""Live camera receipt scanner as a Streamlit custom component.

All OCR runs in the phone's browser (ONNX Runtime Web); Python only receives the de-duplicated
text lines and sends back the receipt analysis that colours the overlay.
"""
import hashlib
import json
from pathlib import Path

import streamlit.components.v1 as components

from . import assets

_FRONTEND = Path(__file__).parent / "frontend"
_component = components.declare_component("receipt_scanner", path=str(_FRONTEND))


def _write_build_version() -> None:
    """Fingerprint the frontend code into build.json. index.html loads the scripts with it as ?v=,
    so after a redeploy phones fetch the new code instead of a cached copy."""
    digest = hashlib.sha256()
    for path in sorted(_FRONTEND.glob("*.js")) + [_FRONTEND / "index.html"]:
        digest.update(path.read_bytes())
    content = json.dumps({"v": digest.hexdigest()[:12]})
    target = _FRONTEND / "build.json"
    if not target.exists() or target.read_text() != content:
        target.write_text(content)


_write_build_version()


def receipt_scanner(*, settings: dict, analysis: dict | None, reset_nonce: int, key: str):
    """Render the scanner. Returns {"session", "version", "lines": [...], "stats"} or None."""
    return _component(settings=settings, analysis=analysis, reset_nonce=reset_nonce, key=key, default=None)


def assets_present() -> bool:
    return assets.present()


def ensure_assets() -> None:
    assets.ensure()
