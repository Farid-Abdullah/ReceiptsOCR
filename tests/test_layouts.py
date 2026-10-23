"""Common receipt layouts written out as OCR text rows (no images).

These cases pin down the wording and layouts common receipt formats use (LuLu, Carrefour, US-style
"balance due", thousands separators, loyalty points), so the parser keeps handling them.
"""
from receipt_parser import find_items, find_total, find_vendor, summarize


def receipt(*rows):
    """OCR lines for printed rows. Each row is a string; " | " separates columns (left label,
    middle columns, right-aligned amount), like the separate boxes the OCR returns."""
    lines, y = [], 40
    for r, row in enumerate(rows):
        cols = [c.strip() for c in row.split(" | ")]
        big = r == 0
        xs = [20] if len(cols) == 1 else [20] + [260 + 70 * k for k in range(len(cols) - 2)] + [420]
        for c, (text, x) in enumerate(zip(cols, xs)):
            width = 14 * len(text) if c == 0 else 60
            lines.append({"id": f"r{r}c{c}", "text": text, "conf": 0.98, "ok": True, "angle": 0.0,
                          "box": [x, y, x + width, y + (40 if big else 24)]})
        y += 54 if big else 32
    return lines


LULU = receipt(
    "LULU HYPERMARKET",
    "Item | Qty | Rate | Amount",
    "TOMATO LOCAL | 1.250 | 4.50 | 5.63",
    "NIDO MILK POWDER 2.5KG | 1 | 89.00 | 89.00",
    "PEPSI 330ML | 6 | 1.50 | 9.00",
    "Gross Amount | 103.63",
    "Discount | 3.63",
    "Net Amount | 100.00",
    "Round Off | 0.00",
    "Cash Tendered | 150.00",
    "Balance | 50.00",
)


def test_lulu_style():
    assert find_vendor(LULU)[0]["value"] == "LuLu Hypermarket"
    items = find_items(LULU)["items"]
    assert [(i["name"], i["qty"], i["unit_price"], i["total"]) for i in items] == [
        ("TOMATO LOCAL", 1.25, 4.5, 5.63), ("NIDO MILK POWDER 2.5KG", 1, 89.0, 89.0), ("PEPSI 330ML", 6, 1.5, 9.0)]
    found = find_total(LULU)
    assert found["total"][0]["value"] == 100.00  # not the cash tendered or the balance (change)
    assert found["subtotal"] == 103.63
    assert [(a["kind"], a["amount"]) for a in found["adjustments"]] == [("discount", -3.63)]
    fields = {"total": 100.0, "subtotal": found["subtotal"], "adjustments": found["adjustments"]}
    assert summarize(fields, items)["status"] == "ok"


CARREFOUR = receipt(
    "Carrefour",
    "ALMARAI LABAN 1L | 6.50",
    "2 X 3.25",
    "BANANA CHIQUITA | 7.95",
    "PROMOTION | -1.00",
    "TOTAL SAVINGS | 1.00",
    "Sub-Total | 13.45",
    "VAT 5% | 0.67",
    "TOTAL AMOUNT DUE AED | 14.12",
    "VISA | 14.12",
    "CHANGE DUE | 0.00",
)


def test_carrefour_style():
    assert find_vendor(CARREFOUR)[0]["value"] == "Carrefour"
    items = find_items(CARREFOUR)["items"]
    assert [(i["name"], i["qty"], i["unit_price"], i["total"]) for i in items] == [
        ("ALMARAI LABAN 1L", 2, 3.25, 6.5), ("BANANA CHIQUITA", 1, 7.95, 7.95), ("PROMOTION", 1, -1.0, -1.0)]
    assert items[2]["discount"]
    found = find_total(CARREFOUR)
    assert found["total"][0]["value"] == 14.12
    assert [(a["kind"], a["amount"]) for a in found["adjustments"]] == [("tax", 0.67)]
    fields = {"total": 14.12, "subtotal": found["subtotal"], "adjustments": found["adjustments"]}
    assert summarize(fields, items)["status"] == "ok"


def test_balance_due_with_cash_and_change():
    lines = receipt("CORNER DELI", "SANDWICH | 18.40", "COFFEE | 7.00", "BALANCE DUE | 25.40", "CASH | 30.00",
                    "CHANGE | 4.60")
    assert find_total(lines)["total"][0]["value"] == 25.40
    assert [i["name"] for i in find_items(lines)["items"]] == ["SANDWICH", "COFFEE"]


def test_thousands_separator_and_currency_prefix():
    lines = receipt("ELECTRONICS STORE", "TV 55 INCH | 1,199.00", "HDMI CABLE | 35.50", "TOTAL | QAR 1,234.50",
                    "CASH | QAR 1,300.00", "CHANGE | QAR 65.50")
    assert find_total(lines)["total"][0]["value"] == 1234.50
    assert [i["total"] for i in find_items(lines)["items"]] == [1199.0, 35.5]


def test_points_and_loyalty_never_offered_first():
    lines = receipt("SOME MARKET", "RICE 5KG | 32.00", "TOTAL | 32.00", "Points earned | 320.00",
                    "Points balance | 4,500.00", "Loyalty card | ****1234")
    assert find_total(lines)["total"][0]["value"] == 32.00


def test_total_on_the_line_below_its_label():
    lines = receipt("SNACK BAR", "JUICE | 12", "Total amount", "| 12 QR")
    assert find_total(lines)["total"][0]["value"] == 12
