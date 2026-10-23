"""Version 2: guided photos. Each step asks for one picture (store name, date and time, total, item
count, then the items) and lets the user confirm, retake, type the value or skip. Run by app.py."""
import pandas as pd
import streamlit as st

import receipt_parser as rp
from scanner import receipt_scanner

settings = st.session_state["settings"]

STEPS = [
    {"id": "vendor", "title": "Store name", "capture": True,
     "prompt": "Fit the store's name in the frame and take a picture."},
    {"id": "datetime", "title": "Date and time", "capture": True,
     "prompt": "Now the date and time of the purchase."},
    {"id": "total", "title": "Total", "capture": True,
     "prompt": "Now the TOTAL line. Include the subtotal, discount, delivery or tax lines above it if you can."},
    {"id": "count", "title": "Number of items", "capture": True,
     "prompt": "Now the item count, e.g. 'ITEMS SOLD: 12'. Skip if the receipt doesn't print one."},
    {"id": "ask_items", "title": "Items", "capture": False, "prompt": "Scan the list of items too?"},
    {"id": "items", "title": "Items", "capture": True,
     "prompt": "Photograph the items from the top, a few lines per picture. Overlap is fine."},
    {"id": "summary", "title": "Summary", "capture": False, "prompt": ""},
]
STEP_INDEX = {s["id"]: i for i, s in enumerate(STEPS)}
EMPTY_FIELDS = {"vendor": None, "date": None, "time": None, "total": None, "subtotal": None, "adjustments": [],
                "item_count": None}
TYPE_IT = "✏️ Type it myself"

ss = st.session_state
ss.setdefault("step", 0)
ss.setdefault("fields", dict(EMPTY_FIELDS))
ss.setdefault("scanned_items", [])
ss.setdefault("added", [])        # how many items each "Add" contributed, for undo
ss.setdefault("photo", None)      # the picture under review: {"seq", "lines", "found"}
ss.setdefault("last_event", None)
ss.setdefault("receipt_no", 0)    # part of every widget key, so a new receipt starts with fresh widgets
ss.setdefault("note", None)

step = STEPS[ss.step]


def finder(step_id: str, lines: list[dict]) -> dict:
    """What a step's picture contains, always with every key the step's UI reads."""
    if step_id == "vendor":
        return {"vendor": rp.find_vendor(lines)}
    if step_id == "datetime":
        return {"date": [], "time": [], **rp.find_datetime(lines)}
    if step_id == "total":
        return {"total": [], "subtotal": None, "adjustments": [], **rp.find_total(lines)}
    if step_id == "count":
        return {"count": rp.find_item_count(lines)}
    return {"items": [], "subtotal": None, "adjustments": [], "total": None, **rp.find_items(lines)}


# --- what the camera sent: a new picture, or "retake" ------------------------------------------------
scan = ss.get("guided_scanner")
if scan and scan.get("event"):
    event = f"{scan['event']}:{scan['seq']}"
    if event != ss.last_event:
        ss.last_event = event
        if scan["event"] == "photo" and scan.get("step") == step["id"]:
            ss.photo = {"seq": scan["seq"], "lines": scan["lines"]}
        elif scan["event"] == "retake":
            ss.photo = None


if ss.photo is not None:
    ss.photo["found"] = finder(step["id"], ss.photo["lines"])


def key(name: str) -> str:
    seq = ss.photo["seq"] if ss.photo else 0
    return f"r{ss.receipt_no}-{step['id']}-{seq}-{name}"


def picked(cands: list[dict], name: str) -> dict | None:
    """The candidate currently chosen in the radio `name` (read before the radio is drawn)."""
    i = ss.get(key(name), 0)
    return cands[i] if cands and i < len(cands) else None


def go(step_id: str) -> None:
    ss.step = STEP_INDEX[step_id]
    ss.photo = None
    st.rerun()


def next_step() -> None:
    go(STEPS[ss.step + 1]["id"])


def to_float(text) -> float | None:
    try:
        return round(float(str(text).replace("$", "").replace(",", ".").strip()), 2)
    except ValueError:
        return None


# --- which lines to outline on the picture ---------------------------------------------------------
review = None
if ss.photo and step["capture"]:
    found, ids, label = ss.photo["found"], [], ""
    if step["id"] == "vendor" and (c := picked(found["vendor"], "vendor")):
        ids, label = c["ids"], "STORE"
    elif step["id"] == "datetime":
        for name in ("date", "time"):
            if c := picked(found[name], name):
                ids += c["ids"]
        label = "DATE / TIME"
    elif step["id"] == "total" and (c := picked(found["total"], "total")):
        ids, label = c["ids"], "TOTAL"
    elif step["id"] == "count" and (c := picked(found["count"], "count")):
        ids, label = c["ids"], "ITEM COUNT"
    elif step["id"] == "items":
        ids = [i for item in found["items"] for i in item["line_ids"]]
        label = f"{len(found['items'])} ITEMS"
    review = {"seq": ss.photo["seq"], "ids": ids, "label": label}

# --- header ---------------------------------------------------------------------------------------
st.progress(ss.step / (len(STEPS) - 1), text=f"Step {ss.step + 1} of {len(STEPS)} · {step['title']}")
done = {k: v for k, v in ss.fields.items() if v is not None and k in ("vendor", "date", "time", "total", "item_count")}
# Always one element here, even when empty: anything appearing or disappearing above the camera
# shifts it, and Streamlit then rebuilds it (camera restart and OCR model reload on the phone).
with st.container():
    if done:
        st.caption("  ·  ".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in done.items()))

# Always rendered: on the summary it is hidden but stays mounted, so it can switch the light off and
# release the camera properly instead of being torn down mid-stream.
receipt_scanner(
    settings={"detLimit": settings["det_limit"], "captureMax": 1920, "confThresh": settings["conf"],
              "backend": settings["backend"]},
    step={**{k: step[k] for k in ("id", "title", "prompt", "capture")}, "show": step["id"] != "summary"},
    review=review,
    key="guided_scanner",
)


def nav(confirm_label: str | None = None, can_confirm: bool = True, skip: bool = True) -> str | None:
    """Confirm / Skip / Back buttons. Skip and Back act right away; returns "confirm" when pressed."""
    cols = st.columns([2, 1, 1])
    pressed = None
    if confirm_label and cols[0].button(confirm_label, type="primary", disabled=not can_confirm,
                                        key=key("confirm"), width="stretch"):
        pressed = "confirm"
    if skip and cols[1].button("Skip", key=key("skip"), width="stretch"):
        next_step()
    if ss.step > 0 and cols[2].button("Back", key=key("back"), width="stretch"):
        ss.step -= 1
        ss.photo = None
        st.rerun()
    return pressed


def choose(label: str, cands: list[dict], name: str, fmt=str):
    """Radio over the candidates plus "type it myself". Returns the chosen or typed text."""
    options = [fmt(c["value"]) for c in cands] + [TYPE_IT]
    i = st.radio(label, range(len(options)), format_func=lambda j: options[j], key=key(name))
    if i == len(cands):
        return st.text_input(f"Type the {name}", key=key(name + "-typed"))
    return cands[i]["value"]


def manual_entry(names: list[str]) -> dict | None:
    """Before any picture: let the user type the value(s) instead of scanning."""
    with st.expander("Can't scan it? Type it instead"):
        values = {n: st.text_input(n.replace("_", " ").capitalize(), key=key("manual-" + n)) for n in names}
        if st.button("Save", key=key("manual-save")):
            return values
    return None


# --- one block per step -----------------------------------------------------------------------------
sid = step["id"]

if sid in ("vendor", "datetime", "total", "count"):
    if ss.photo is None:
        st.info(f"📷 {step['prompt']}")
        names = {"vendor": ["vendor"], "datetime": ["date", "time"], "total": ["total"], "count": ["item_count"]}[sid]
        typed = manual_entry(names)
        if typed is not None:
            for n, v in typed.items():
                v = v.strip()
                if n == "total":
                    ss.fields[n] = to_float(v)
                elif n == "item_count":
                    ss.fields[n] = int(v) if v.isdigit() else None
                else:
                    ss.fields[n] = v or None
            next_step()
        nav()
    else:
        found = ss.photo["found"]
        if sid == "vendor":
            if not found["vendor"]:
                st.error("No store name found in this picture. Retake it closer, or type it below.")
            value = choose("Which one is the store name?", found["vendor"], "vendor")
            if nav("Confirm", bool(value and str(value).strip())) == "confirm":
                ss.fields["vendor"] = str(value).strip()
                next_step()
        elif sid == "datetime":
            if not found["date"] and not found["time"]:
                st.error("No date or time found in this picture. Retake it, or type them below.")
            date = choose("Date", found["date"], "date")
            time = choose("Time", found["time"], "time")
            if nav("Confirm", bool(date or time)) == "confirm":
                ss.fields["date"], ss.fields["time"] = (date or None), (time or None)
                next_step()
        elif sid == "total":
            if not found["total"]:
                st.error("No amount found in this picture. Retake it, or type the total below.")
            labels = {c["value"]: c["label"] for c in found["total"]}
            value = choose("Which is the total?", found["total"], "total",
                           fmt=lambda v: f"{v:.2f}   ({labels[v]})" if labels.get(v) else f"{v:.2f}")
            total = to_float(value) if value is not None else None
            extra = {"subtotal": found["subtotal"], "adjustments": found["adjustments"]}
            also = ([f"subtotal {found['subtotal']:.2f}"] if found["subtotal"] is not None else []) + [
                f"{a['kind']} {a['amount']:+.2f}" for a in found["adjustments"]]
            if also:
                st.caption("Also read: " + ", ".join(also))
            if value not in (None, "") and total is None:
                st.warning("That doesn't look like an amount.")
            if nav("Confirm", total is not None) == "confirm":
                ss.fields["total"] = total
                ss.fields["subtotal"] = extra["subtotal"] if extra["subtotal"] is not None else ss.fields["subtotal"]
                ss.fields["adjustments"] = extra["adjustments"] or ss.fields["adjustments"]
                next_step()
        elif sid == "count":
            if not found["count"]:
                st.error("No item count found in this picture. Retake it, type it, or skip.")
            value = choose("How many items does the receipt say?", found["count"], "count")
            count = int(value) if str(value).strip().isdigit() else None
            if nav("Confirm", count is not None) == "confirm":
                ss.fields["item_count"] = count
                next_step()

elif sid == "ask_items":
    st.info("Do you also want to scan the list of items? The app then checks that they add up to the total.")
    c1, c2, c3 = st.columns([2, 2, 1])
    if c1.button("Yes, scan items", type="primary", width="stretch"):
        go("items")
    if c2.button("No, finish", width="stretch"):
        go("summary")
    if c3.button("Back", width="stretch"):
        go("count")

elif sid == "items":
    if ss.note:
        st.success(ss.note)
        ss.note = None
    if ss.photo is None:
        st.info(f"📷 {step['prompt']}")
    else:
        found = ss.photo["found"]["items"]
        if not found:
            st.error("No items with prices found in this picture. Retake it closer.")
        else:
            merged, skipped = rp.merge_items(ss.scanned_items, found)
            new = len(merged) - len(ss.scanned_items)
            st.dataframe(pd.DataFrame([{"Product": i["name"], "Qty": i["qty"], "Total": i["total"]} for i in found]),
                         hide_index=True)
            if skipped:
                st.caption(f"{skipped} of these were already added from the previous picture and will be skipped.")
            if st.button(f"Add {new} item{'s' if new != 1 else ''}", type="primary", disabled=new == 0,
                         key=key("add"), width="stretch"):
                ss.scanned_items, ss.added = merged, ss.added + [new]
                # the last picture often reaches the subtotal, discount and tax lines
                if ss.fields.get("subtotal") is None:
                    ss.fields["subtotal"] = ss.photo["found"].get("subtotal")
                if not ss.fields.get("adjustments"):
                    ss.fields["adjustments"] = ss.photo["found"].get("adjustments") or []
                ss.note = f"Added {new} items" + (f", skipped {skipped} already scanned." if skipped else ".")
                ss.photo = None
                st.rerun()
    if ss.scanned_items:
        total = sum(i["total"] for i in ss.scanned_items)
        target = f" of {ss.fields['total']:.2f}" if ss.fields.get("total") is not None else ""
        st.markdown(f"**{len(ss.scanned_items)} items so far**, sum **{total:.2f}**{target}")
    c1, c2, c3 = st.columns([2, 1, 1])
    if c1.button("Done with items", type="primary" if ss.scanned_items else "secondary", width="stretch"):
        go("summary")
    if c2.button("Undo last", disabled=not ss.added, width="stretch"):
        ss.scanned_items = ss.scanned_items[: len(ss.scanned_items) - ss.added[-1]]
        ss.added = ss.added[:-1]
        st.rerun()
    if c3.button("Back", width="stretch"):
        go("ask_items")

elif sid == "summary":
    f = ss.fields
    status_box = st.container()  # filled at the end, once the edits below are known
    st.markdown("#### Receipt")
    c1, c2 = st.columns(2)
    f["vendor"] = c1.text_input("Store", f["vendor"] or "", key=key("vendor")) or None
    f["date"] = c2.text_input("Date", f["date"] or "", key=key("date")) or None
    c1, c2 = st.columns(2)
    f["time"] = c1.text_input("Time", f["time"] or "", key=key("time")) or None
    f["total"] = to_float(c2.text_input("Total", "" if f["total"] is None else f"{f['total']:.2f}", key=key("total")))
    c1, c2 = st.columns(2)
    f["subtotal"] = to_float(c1.text_input("Subtotal", "" if f["subtotal"] is None else f"{f['subtotal']:.2f}",
                                           key=key("subtotal")))
    count = c2.text_input("Item count", "" if f["item_count"] is None else str(f["item_count"]), key=key("count"))
    f["item_count"] = int(count) if count.strip().isdigit() else None
    st.caption("Between subtotal and total (discounts negative):")
    adj = st.data_editor(
        pd.DataFrame(f["adjustments"] or [], columns=["kind", "amount"]),
        num_rows="dynamic", hide_index=True, key=key("adjustments"),
        column_config={"kind": st.column_config.TextColumn("Line", help="e.g. tax, discount, delivery"),
                       "amount": st.column_config.NumberColumn("Amount", format="%.2f")},
    )
    f["adjustments"] = [{"kind": str(r["kind"] or "adjustment"), "label": str(r["kind"] or ""), "amount": round(float(r["amount"]), 2)}
                        for r in adj.to_dict("records") if not pd.isna(r["amount"])]

    items = ss.scanned_items
    if items:
        st.markdown("#### Items")
        edited = st.data_editor(
            pd.DataFrame([{"Product": i["name"], "Code": i["code"] or "", "Qty": i["qty"],
                           "Unit": i["unit_price"], "Total": i["total"]} for i in items]),
            num_rows="dynamic", hide_index=True, key=key("items"),
            column_config={
                "Qty": st.column_config.NumberColumn(format="%g"),
                "Unit": st.column_config.NumberColumn(format="%.2f"),
                "Total": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        items = []
        for row in edited.to_dict("records"):
            if pd.isna(row["Total"]) or not str(row["Product"] or "").strip():
                continue
            qty = 1.0 if pd.isna(row["Qty"]) else float(row["Qty"])
            unit = float(row["Total"]) / qty if pd.isna(row["Unit"]) and qty else float(row["Unit"] or 0)
            total = round(float(row["Total"]), 2)
            items.append({"name": str(row["Product"]).strip(), "code": str(row["Code"] or "") or None,
                          "qty": qty, "unit_price": round(unit, 2), "total": total, "discount": total < 0,
                          "uncertain": False, "qty_mismatch": total >= 0 and abs(qty * unit - total) > 0.011 + 0.005 * qty,
                          "line_ids": []})

    v = rp.summarize(f, items)
    with status_box:
        if not items:
            st.info("Items were not scanned, so they can't be checked against the total.")
        elif v["status"] == "ok":
            st.success(f"✓ {v['message']}")
        elif v["status"] == "mismatch":
            st.warning(f"⚠ {v['message']}. Fix the items below, or check the total and the lines above it.")
        with st.expander("Checks", expanded=v["status"] == "mismatch"):
            icons = {True: "✅", False: "❌", None: "⏳"}
            for chk in v["checks"]:
                st.markdown(f"{icons[chk['ok']]} **{chk['name']}**: {chk['detail']}")

    c1, c2, c3 = st.columns([2, 2, 1])
    c1.button("Download JSON (doesn't work)", disabled=True, width="stretch",
              help="Turned off: the scanner isn't reliable enough yet to export results.")
    if c2.button("Scan another receipt", type="primary", width="stretch"):
        for k in ("step", "fields", "scanned_items", "added", "photo", "note"):
            ss.pop(k, None)
        ss.receipt_no += 1
        st.rerun()
    if c3.button("Back", width="stretch"):
        go("items" if ss.scanned_items else "ask_items")
