"""Version 1: live scan. Slide the phone down the receipt; the camera reads continuously, keeps every
line once, and the fields and checks below update as text is confirmed. Run by app.py."""
import pandas as pd
import streamlit as st

import receipt_parser as rp
from scanner import live_scanner

settings = st.session_state["settings"]
ss = st.session_state
ss.setdefault("live_reset", 0)

# The component's latest value is in session state before it renders, so the analysis sent back
# always matches the text the phone just sent.
scan = ss.get("live_scanner")
result = rp.read_receipt(scan["lines"]) if scan and scan.get("lines") else None
analysis = {"session": scan["session"], "roles": result["roles"], "section": result["section"]} if result else None

live_scanner(
    settings={"detLimit": settings["det_limit"], "captureMax": settings["capture_max"], "confThresh": settings["conf"],
              "minSupport": settings["min_support"], "backend": settings["backend"]},
    analysis=analysis,
    reset_nonce=ss.live_reset,
    key="live_scanner",
)

if not result:
    st.caption("Tap **Start camera**, fit the receipt's width in view and move slowly from top to bottom. "
               "Green = confirmed, red = uncertain, orange = the items don't add up. Results appear here as you scan.")
    st.stop()

v = result["validation"]
if v["status"] == "ok":
    st.success(f"✓ {v['message']}")
elif v["status"] == "mismatch":
    st.warning(f"⚠ {v['message']}. Rescan the items (orange area) or check the red words.")
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
    st.dataframe(
        pd.DataFrame({
            "Product": [it["name"] for it in items],
            "Code": [it["code"] or "" for it in items],
            "Qty": [it["qty"] for it in items],
            "Unit": [it["unit_price"] for it in items],
            "Total": [it["total"] for it in items],
            "Check": ["⚠ qty×unit" if it["qty_mismatch"] else "? unsure" if it["uncertain"] else "✓" for it in items],
        }),
        hide_index=True,
        column_config={
            "Qty": st.column_config.NumberColumn(format="%g"),
            "Unit": st.column_config.NumberColumn(format="%.2f"),
            "Total": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    sums = f"Items sum: **{v['items_sum']:.2f}**"
    if result["subtotal"] is not None:
        sums += f" · Subtotal: {result['subtotal']:.2f}"
    sums += "".join(f" · {a['kind'].title()}: {a['amount']:+.2f}" for a in result["adjustments"])
    st.markdown(sums)

with st.expander("Checks", expanded=v["status"] == "mismatch"):
    icons = {True: "✅", False: "❌", None: "⏳"}
    for chk in v["checks"]:
        st.markdown(f"{icons[chk['ok']]} **{chk['name']}**: {chk['detail']}")

with st.expander("Scanned text"):
    st.code("\n".join(result["rows"]) or "(nothing yet)", language=None)

b1, b2 = st.columns(2)
b1.button("Download JSON (doesn't work)", disabled=True, width="stretch",
          help="Turned off: the scanner isn't reliable enough yet to export results.")
if b2.button("New receipt", width="stretch"):
    ss.live_reset += 1
    ss.pop("live_scanner", None)
    st.rerun()
