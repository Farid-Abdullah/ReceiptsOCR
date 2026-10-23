"""Receipt scanner: point the phone camera at a receipt and slide down it.

Locally:  streamlit run serve.py      Streamlit Community Cloud: main file app.py (see README)
"""
import json

import pandas as pd
import streamlit as st

from receipt_parser import parse
from scanner import assets_present, ensure_assets, receipt_scanner

st.set_page_config(page_title="Receipt Scanner", page_icon="🧾", layout="centered", initial_sidebar_state="collapsed")
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; padding-bottom: 2rem; padding-left: .8rem; padding-right: .8rem; }
      [data-testid="stMetricValue"] { font-size: 1.15rem; }
      footer { visibility: hidden; }
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

with st.sidebar:
    st.header("Scanner settings")
    det_limit = st.select_slider(
        "Detection resolution", options=[640, 736, 832, 960, 1088, 1280], value=960,
        help="Higher reads smaller print but is slower on the phone.",
    )
    capture_max = st.select_slider("Camera frame size", options=[960, 1280, 1600, 1920], value=1280)
    conf = st.slider("Green box confidence", 0.5, 0.99, 0.85, 0.01,
                     help="Minimum per-character confidence for a word to be shown as confirmed.")
    support = st.slider("Frames that must agree", 1, 5, 2,
                        help="A word turns green only after this many frames read it the same way.")
    backend = st.radio("Inference backend", ["wasm", "webgpu"], horizontal=True,
                       help="WebGPU is faster on recent phones; falls back to WASM if unavailable.")

if "reset_nonce" not in st.session_state:
    st.session_state.reset_nonce = 0

# The component's latest value is in session state before it renders, so the analysis we send
# back always matches the text the phone just sent.
scan = st.session_state.get("scanner")
result = parse(scan["lines"]) if scan and scan.get("lines") else None
analysis = None
if result:
    analysis = {"session": scan["session"], "roles": result["roles"], "section": result["section"]}

st.subheader("🧾 Receipt scanner")
receipt_scanner(
    settings={"detLimit": det_limit, "captureMax": capture_max, "confThresh": conf,
              "minSupport": support, "backend": backend},
    analysis=analysis,
    reset_nonce=st.session_state.reset_nonce,
    key="scanner",
)

if not result:
    st.caption("Tap **Start camera**, fit the receipt's width in view and move slowly from top to bottom. "
               "Green = confirmed, red = uncertain. Results appear here as you scan.")
    st.stop()

v = result["validation"]
if v["status"] == "ok":
    st.success(f"✓ {v['message']}")
elif v["status"] == "mismatch":
    st.warning(f"⚠ {v['message']} — rescan the items (orange area) or check the red words.")
else:
    st.info(v["message"])

c1, c2 = st.columns(2)
c1.metric("Vendor", result["vendor"] or "—")
c2.metric("Total", f"{result['total']:.2f}" if result["total"] is not None else "—")
c3, c4, c5 = st.columns(3)
c3.metric("Date", result["date"] or "—")
c4.metric("Time", result["time"] or "—")
count = f"{v['line_count']}" if v["line_count"] == v["unit_count"] else f"{v['line_count']} ({v['unit_count']} units)"
stated = result["stated_item_count"]
if stated is None:
    c5.metric("Items", count, help="Item lines scanned (units counts quantities)")
elif stated in (v["line_count"], v["unit_count"]):
    c5.metric("Items", count, delta="matches receipt", help=f"The receipt says {stated} items")
else:
    c5.metric("Items", count, delta=f"-receipt says {stated}", help="Count printed on the receipt differs")

items = result["items"]
if items:
    df = pd.DataFrame(
        {
            "Product": [it["name"] for it in items],
            "Code": [it["code"] or "" for it in items],
            "Qty": [it["qty"] for it in items],
            "Unit": [it["unit_price"] for it in items],
            "Total": [it["total"] for it in items],
            "Check": [
                "⚠ qty×unit" if it["qty_mismatch"] else "? unsure" if it["uncertain"] else "✓"
                for it in items
            ],
        }
    )
    st.dataframe(
        df, hide_index=True,
        column_config={
            "Qty": st.column_config.NumberColumn(format="%g"),
            "Unit": st.column_config.NumberColumn(format="%.2f"),
            "Total": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    sums = f"Items sum: **{v['items_sum']:.2f}**"
    if result["subtotal"] is not None:
        sums += f" · Subtotal: {result['subtotal']:.2f}"
    if result["tax"] is not None:
        sums += f" · Tax: {result['tax']:.2f}"
    st.markdown(sums)

with st.expander("Checks", expanded=v["status"] == "mismatch"):
    for chk in v["checks"]:
        icon = {True: "✅", False: "❌", None: "⏳"}[chk["ok"]]
        st.markdown(f"{icon} **{chk['name']}** — {chk['detail']}")

with st.expander("Scanned text"):
    st.code("\n".join(result["rows"]) or "(nothing yet)", language=None)

export = {k: result[k] for k in ("vendor", "date", "time", "total", "subtotal", "tax", "stated_item_count", "items")}
export["validation"] = {k: v[k] for k in ("status", "message", "items_sum")}
b1, b2 = st.columns(2)
b1.download_button("Download JSON", json.dumps(export, indent=2), file_name="receipt.json",
                   mime="application/json", width="stretch")
if b2.button("New receipt", width="stretch"):
    st.session_state.reset_nonce += 1
    st.session_state.pop("scanner", None)
    st.rerun()
