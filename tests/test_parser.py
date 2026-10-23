import pytest

from receipt_parser import parse, parse_money, row_amount

# Segments as the OCR pipeline actually produced them for tests/data/receipt.png (see tests/ref_ocr.py)
OCR_SEGMENTS = [
    ([157, 50, 402, 88], "FRESH MART"), ([180, 108, 382, 136], "123 MAIN STREET"),
    ([115, 146, 446, 175], "SPRINGFIELD TEL 555-0142"), ([27, 222, 244, 251], "DATE: 10/21/2026"),
    ([270, 222, 423, 251], "TIME: 14:35"), ([26, 299, 189, 327], "4011 BANANAS"),
    ([62, 333, 179, 370], "2 @ 0.59"), ([472, 335, 535, 368], "1.18"),
    ([26, 375, 292, 403], "0712 WHOLE MILK 1GAL"), ([472, 373, 535, 406], "3.49"),
    ([26, 413, 242, 442], "2231 BREAD WHEAT"), ([472, 411, 535, 445], "2.99"),
    ([24, 449, 190, 480], "4011 BANANAS"), ([59, 486, 179, 522], "2 @ 0.59"), ([472, 487, 535, 521], "1.18"),
    ([24, 525, 294, 557], "9921 EGGS LARGE 12CT"), ([471, 526, 534, 557], "4.29"),
    ([26, 564, 279, 593], "5520 CHEDDAR CHEESE"), ([472, 563, 535, 597], "5.79"),
    ([25, 601, 241, 633], "1180 APPLE JUICE"), ([62, 639, 178, 673], "3 @ 1.99"), ([472, 640, 534, 671], "5.97"),
    ([26, 678, 241, 707], "3307 PASTA PENNE"), ([62, 713, 179, 750], "2 @ 1.25"), ([472, 715, 536, 748], "2.50"),
    ([25, 752, 255, 784], "8812 TOMATO SAUCE"), ([472, 753, 534, 785], "2.15"),
    ([26, 791, 254, 820], "6604 COFFEE BEANS"), ([472, 791, 535, 825], "9.99"),
    ([26, 867, 136, 898], "SUBTOTAL"), ([461, 867, 535, 899], "39.53"),
    ([25, 906, 114, 936], "TAX 5%"), ([472, 906, 535, 937], "1.98"),
    ([25, 945, 109, 977], "TOTAL"), ([450, 944, 536, 979], "41.51"),
    ([24, 1021, 86, 1050], "VISA"), ([460, 1021, 534, 1050], "41.51"),
    ([25, 1058, 216, 1089], "ITEMS SOLD: 15"), ([136, 1097, 425, 1123], "THANK YOU FOR SHOPPING"),
]


def lines_from(segments, replace=None):
    replace = replace or {}
    return [{"id": f"L{i}", "text": replace.get(t, t), "ok": True, "box": box} for i, (box, t) in enumerate(segments)]


def rows_to_lines(rows, line_h=30, gap=8, right_x=470):
    """Build segments from simple (left, right) row tuples."""
    out, y = [], 50
    for i, (left, right) in enumerate(rows):
        if left:
            out.append({"id": f"a{i}", "text": left, "ok": True, "box": [20, y, 20 + 12 * len(left), y + line_h]})
        if right:
            out.append({"id": f"b{i}", "text": right, "ok": True, "box": [right_x, y, right_x + 60, y + line_h]})
        y += line_h + gap
    return out


def test_synthetic_receipt_fields():
    r = parse(lines_from(OCR_SEGMENTS))
    assert r["vendor"] == "FRESH MART"
    assert r["date"] == "10/21/2026"
    assert r["time"] == "14:35"
    assert r["total"] == 41.51
    assert r["subtotal"] == 39.53
    assert r["tax"] == 1.98
    assert r["stated_item_count"] == 15
    items = r["items"]
    assert [it["name"] for it in items] == [
        "BANANAS", "WHOLE MILK 1GAL", "BREAD WHEAT", "BANANAS", "EGGS LARGE 12CT",
        "CHEDDAR CHEESE", "APPLE JUICE", "PASTA PENNE", "TOMATO SAUCE", "COFFEE BEANS",
    ]
    bananas = items[0]
    assert (bananas["code"], bananas["qty"], bananas["unit_price"], bananas["total"]) == ("4011", 2, 0.59, 1.18)
    assert items[6]["qty"] == 3 and items[6]["unit_price"] == 1.99
    assert not any(it["qty_mismatch"] for it in items)
    v = r["validation"]
    assert v["status"] == "ok", v["message"]
    assert v["unit_count"] == 15
    assert r["section"]["status"] == "ok"
    assert r["roles"]["L33"] == "total" and r["roles"]["L34"] == "total"
    assert r["roles"]["L0"] == "vendor"
    assert set(r["section"]["ids"]) >= {"L5", "L6", "L7", "L28"}


def test_wrong_total_is_mismatch():
    r = parse(lines_from(OCR_SEGMENTS, {"41.51": "45.51"}))
    assert r["total"] == 45.51
    assert r["validation"]["status"] == "mismatch"
    assert r["section"]["status"] == "mismatch"


def test_missing_item_is_mismatch():
    segs = [s for s in OCR_SEGMENTS if s[1] not in ("6604 COFFEE BEANS", "9.99")]
    r = parse(lines_from(segs))
    assert len(r["items"]) == 9
    assert r["validation"]["status"] == "mismatch"


def test_pending_until_total_seen():
    r = parse(lines_from(OCR_SEGMENTS[:20]))
    assert r["total"] is None
    assert r["validation"]["status"] == "pending"
    assert len(r["items"]) == 6


def test_inline_qty_tax_flags_and_discount():
    rows = [
        ("CORNER CAFE", None),
        ("2026-03-04 09:15 AM", None),
        ("LATTE 2 x 3.50", "7.00 T"),
        ("MUFFIN", "2.25 A"),
        ("COUPON", "1.00-"),
        ("TOTAL", "8.25"),
        ("CASH", "10.00"),
        ("CHANGE", "1.75"),
    ]
    r = parse(rows_to_lines(rows))
    assert r["vendor"] == "CORNER CAFE"
    assert r["date"] == "2026-03-04"
    assert r["time"] == "09:15 AM"
    names = [(it["name"], it["qty"], it["unit_price"], it["total"], it["discount"]) for it in r["items"]]
    assert names == [("LATTE", 2, 3.5, 7.0, False), ("MUFFIN", 1, 2.25, 2.25, False), ("COUPON", 1, -1.0, -1.0, True)]
    assert r["total"] == 8.25
    assert r["validation"]["status"] == "ok"


def test_qty_line_under_priced_item():
    rows = [("SHOP", None), ("APPLES", "3.00"), ("  3 @ 1.00", None), ("TOTAL", "3.00")]
    r = parse(rows_to_lines(rows))
    assert len(r["items"]) == 1
    assert r["items"][0]["qty"] == 3 and r["items"][0]["unit_price"] == 1.0
    assert r["validation"]["status"] == "ok"


def test_grand_total_preferred_and_total_tax_ignored():
    rows = [("SHOP", None), ("THING", "10.00"), ("TOTAL TAX", "0.50"), ("GRAND TOTAL", "10.50")]
    r = parse(rows_to_lines(rows))
    assert r["total"] == 10.50


@pytest.mark.parametrize("tok,val", [("3.49", 3.49), ("$12,50", 12.5), ("1.00-", -1.0), ("-2.00", -2.0), ("4.O9", 4.09), ("abc", None)])
def test_parse_money(tok, val):
    assert parse_money(tok) == val


def test_row_amount_flags():
    assert row_amount("MILK 3.49 A") == (3.49, "MILK")
    assert row_amount("MILK 3.49A") == (3.49, "MILK")
    assert row_amount("MILK") == (None, "MILK")


@pytest.mark.parametrize("degrees", [-6, 5])
def test_tilted_receipt_rows_are_deskewed(degrees):
    """On a tilted receipt the price at the right edge sits higher/lower than its label."""
    import math

    a = math.radians(degrees)
    tilted = []
    for i, (box, text) in enumerate(OCR_SEGMENTS):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        ncx, ncy = cx * math.cos(a) - cy * math.sin(a) + 100, cx * math.sin(a) + cy * math.cos(a) + 100
        hw, hh = (box[2] - box[0]) / 2 * math.cos(a), (box[3] - box[1]) / 2
        tilted.append({"id": f"L{i}", "text": text, "ok": True, "angle": a, "box": [ncx - hw, ncy - hh, ncx + hw, ncy + hh]})
    r = parse(tilted)
    assert r["total"] == 41.51
    assert len(r["items"]) == 10
    assert r["validation"]["status"] == "ok", r["validation"]["message"]


# --- guided scan: one photo per step -------------------------------------------------------------
from receipt_parser import find_datetime, find_item_count, find_items, find_total, find_vendor, merge_items, summarize


def photo(*texts):
    """The OCR segments of a photo showing only the given printed rows (texts must be unique)."""
    return [l for l in lines_from(OCR_SEGMENTS) if l["text"] in texts]


def photo_of(start, stop):
    """The OCR segments of a photo showing OCR_SEGMENTS[start:stop] (keeps repeated rows apart)."""
    return lines_from(OCR_SEGMENTS)[start:stop]


def test_find_vendor_prefers_big_name_over_address():
    lines = photo("FRESH MART", "123 MAIN STREET", "SPRINGFIELD TEL 555-0142")
    cands = find_vendor(lines)
    assert cands[0]["value"] == "FRESH MART"
    assert cands[0]["ids"] == ["L0"]
    assert {c["value"] for c in cands[1:]} == {"123 MAIN STREET", "SPRINGFIELD TEL 555-0142"}


def test_find_vendor_nothing_readable():
    assert find_vendor(photo("41.51", "TIME: 14:35")) == []


def test_find_datetime():
    found = find_datetime(photo("DATE: 10/21/2026", "TIME: 14:35", "4011 BANANAS"))
    assert [c["value"] for c in found["date"]] == ["10/21/2026"]
    assert [c["value"] for c in found["time"]] == ["14:35"]
    assert found["date"][0]["ids"] == ["L3"] and found["time"][0]["ids"] == ["L4"]


def test_find_total_with_subtotal_and_tax():
    found = find_total(photo("SUBTOTAL", "39.53", "TAX 5%", "1.98", "TOTAL", "41.51", "VISA"))
    assert found["total"][0]["value"] == 41.51
    assert set(found["total"][0]["ids"]) == {"L33", "L34"}
    assert found["subtotal"] == 39.53 and found["tax"] == 1.98


def test_find_total_without_keyword_offers_amounts():
    found = find_total(photo("2.15", "9.99"))
    assert [c["value"] for c in found["total"]] == [9.99, 2.15]


def test_find_item_count():
    assert [c["value"] for c in find_item_count(photo("ITEMS SOLD: 15", "THANK YOU FOR SHOPPING"))] == [15]
    assert find_item_count(photo("TOTAL", "41.51")) == []


def test_items_from_overlapping_photos_are_merged_once():
    first = find_items(photo_of(5, 17))["items"]    # 4011 BANANAS .. 9921 EGGS 4.29 (both bananas)
    second = find_items(photo_of(12, 29))["items"]  # 2nd BANANAS .. COFFEE BEANS: repeats 2 items
    assert [i["name"] for i in first] == ["BANANAS", "WHOLE MILK 1GAL", "BREAD WHEAT", "BANANAS", "EGGS LARGE 12CT"]
    merged, skipped = merge_items(first, second)
    assert skipped == 2
    assert len(merged) == 10
    assert [i["name"] for i in merged].count("BANANAS") == 2  # the two real banana lines both stay


def test_merge_without_overlap_keeps_everything():
    a = [{"name": "MILK", "total": 3.49}]
    b = [{"name": "BREAD", "total": 2.99}]
    assert merge_items(a, b) == (a + b, 0)


def test_summarize_checks_items_against_total():
    items = find_items(photo_of(5, 29))["items"]
    fields = {"vendor": "FRESH MART", "date": "10/21/2026", "time": "14:35", "total": 41.51,
              "subtotal": 39.53, "tax": 1.98, "item_count": 15}
    assert summarize(fields, items)["status"] == "ok"
    assert summarize({**fields, "total": 45.51}, items)["status"] == "mismatch"
    assert summarize({**fields, "tax": None, "subtotal": None}, items)["status"] == "mismatch"


def test_low_confidence_slivers_are_ignored():
    """Thin pieces of big bold letters come back as '2' with low confidence (seen on a real run)."""
    lines = [
        {"id": "a", "text": "FRESH", "conf": 1.0, "ok": True, "box": [240, 132, 341, 163]},
        {"id": "b", "text": "2", "conf": 0.255, "ok": False, "box": [334, 137, 345, 151]},
        {"id": "c", "text": "2", "conf": 0.264, "ok": False, "box": [340, 137, 351, 151]},
        {"id": "d", "text": "MART", "conf": 1.0, "ok": True, "box": [340, 125, 423, 156]},
        {"id": "e", "text": "123 MAIN STREET", "conf": 0.99, "ok": True, "box": [260, 171, 411, 196]},
    ]
    assert find_vendor(lines)[0]["value"] == "FRESH MART"
