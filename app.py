"""Receipt scanner: Streamlit entry point. Shared page setup, the scanner settings and the switch
between the two versions; the versions themselves are pages in views/:

  views/live.py     Version 1, live scan (slide the phone down the receipt), the default
  views/guided.py   Version 2, guided photos (one picture per field)

Locally:  python serve.py (this computer only)      Streamlit Community Cloud: main file app.py
"""
import importlib
from pathlib import Path

import streamlit as st

import receipt_parser as rp
from scanner import assets_present, ensure_assets

# After a redeploy, Streamlit can rerun new page code while an older receipt_parser is still loaded
# (that showed up as KeyErrors). Reload it whenever its file is newer than the loaded copy.
if getattr(rp, "_loaded_mtime", None) != Path(rp.__file__).stat().st_mtime:
    rp = importlib.reload(rp)
    rp._loaded_mtime = Path(rp.__file__).stat().st_mtime

# the sidebar holds the scanner settings: open on the first visit so they can be set before scanning
st.set_page_config(page_title="Receipt Scanner", page_icon="🧾", layout="centered", initial_sidebar_state="expanded")
st.markdown(
    """
    <style>
      .block-container { padding-top: 3rem; padding-bottom: 2rem; padding-left: .8rem; padding-right: .8rem; }
      [data-testid="stMetricValue"] { font-size: 1.15rem; }
      footer { visibility: hidden; }
      /* the button that opens the settings sidebar: a gear instead of ">>" */
      [data-testid="stExpandSidebarButton"] > * { display: none !important; }
      [data-testid="stExpandSidebarButton"]::after { content: "⚙️"; font-size: 1.6rem; line-height: 1; }
    </style>
    """,
    unsafe_allow_html=True,
)

# The OCR models are not in git: the first visit to a fresh server downloads them (~45 MB, once).
if not assets_present():
    try:
        with st.spinner("Downloading the OCR models (first start only, takes a minute)..."):
            ensure_assets()
    except Exception as err:  # noqa: BLE001 - show any download problem in the page
        st.error(f"Could not download the OCR models: {err}. Reload the page to try again.")
        st.stop()

live = st.Page("views/live.py", title="Version 1 · Live scan", icon="🎥", url_path="live", default=True)
guided = st.Page("views/guided.py", title="Version 2 · Guided photos", icon="📷", url_path="guided")
page = st.navigation([live, guided], position="hidden")
is_live = page.title == live.title  # the default page has an empty url_path

# Defaults are the settings that read best in testing: detection at 1280 px beat 1600 px and the
# original 960 px; the full camera frame; 3 frames to confirm a word.
with st.sidebar:
    st.header("⚙️ Scanner settings")
    st.caption("The defaults gave the best results in testing. Close this panel to start scanning.")
    st.select_slider("Detection resolution", options=[640, 960, 1280, 1600], value=1280, key="det_limit",
                     help="Size the text detector works at. Higher finds smaller print but is slower; "
                          "1280 read the test receipts best.")
    st.slider("Green box confidence", 0.5, 0.99, 0.85, 0.01, key="conf",
              help="Minimum per-character confidence for a word to count as read clearly.")
    st.radio("Inference backend", ["wasm", "webgpu"], horizontal=True, key="backend",
             help="WebGPU is faster on recent phones; it falls back to WASM where unavailable.")
    if is_live:
        st.markdown("**Version 1 (live scan)**")
        st.select_slider("Camera frame size", options=[960, 1280, 1600, 1920], value=1920, key="capture_max",
                         help="How much of the camera's resolution each scan uses. Full size reads best.")
        st.slider("Frames that must agree", 1, 5, 3, key="min_support",
                  help="A word turns green only after this many frames read it the same way.")
ss = st.session_state
ss["settings"] = {"det_limit": ss["det_limit"], "conf": ss["conf"], "backend": ss["backend"],
                  "capture_max": ss.get("capture_max", 1920), "min_support": ss.get("min_support", 3)}

st.subheader("🧾 Receipt scanner")
labels = {live.title: live, guided.title: guided}
choice = st.segmented_control("Version", list(labels), default=page.title, label_visibility="collapsed")
if choice and choice != page.title:
    st.switch_page(labels[choice])

page.run()
