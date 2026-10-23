"""The camera receipt scanner as a Streamlit custom component, in two versions.

All OCR runs in the phone's browser (ONNX Runtime Web); Python only receives text lines and sends
back what to outline. One component serves both versions: the "mode" argument tells
frontend/index.html whether to start Version 2 (guided photos, main.js) or Version 1 (live scan,
live/*.js). They share the OCR worker and the downloaded models.
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
    for path in sorted(_FRONTEND.glob("*.js")) + sorted(_FRONTEND.glob("live/*.js")) + [_FRONTEND / "index.html"]:
        digest.update(path.read_bytes())
    content = json.dumps({"v": digest.hexdigest()[:12]})
    target = _FRONTEND / "build.json"
    if not target.exists() or target.read_text() != content:
        target.write_text(content)


_write_build_version()


def receipt_scanner(*, settings: dict, step: dict, review: dict | None, key: str):
    """Version 2: the camera for one step ({"id", "title", "prompt", "capture", "show"}). `review`
    outlines what was found in the current picture ({"seq", "ids", "label"}). Returns the last
    camera event: {"event": "photo", "seq", "step", "size", "lines": [...]} or
    {"event": "retake", "seq", "step"}."""
    return _component(mode="guided", settings=settings, step=step, review=review, key=key, default=None)


def live_scanner(*, settings: dict, analysis: dict | None, reset_nonce: int, key: str):
    """Version 1: continuous scanning. `analysis` ({"session", "roles", "section"}) colours the
    overlay; changing `reset_nonce` starts a new receipt. Returns the de-duplicated receipt text:
    {"session", "version", "lines": [...], "stats"} or None."""
    return _component(mode="live", settings=settings, analysis=analysis, reset_nonce=reset_nonce, key=key,
                      default=None)


def assets_present() -> bool:
    return assets.present()


def ensure_assets() -> None:
    assets.ensure()
