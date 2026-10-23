"""Turn the scanner's de-duplicated text lines into structured receipt fields and validate them.

Input is what the camera component sends: a list of text segments, each with an id, the
consensus text, a confidence flag and a box in receipt ("document") coordinates.  The parser
is stateless - it re-parses the whole document every time, which keeps it robust to the
scanner revising earlier readings.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict

MONEY_TOL = 0.011

MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC"
DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b"),
    re.compile(r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b"),
    re.compile(rf"\b(\d{{1,2}})[\s\-]?({MONTHS})[A-Z]*[\s,\-]*(\d{{4}}|\d{{2}})\b"),
    re.compile(rf"\b({MONTHS})[A-Z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}}|\d{{2}})\b"),
]
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])\s?:\s?([0-5]\d)(?:\s?:\s?([0-5]\d))?\s*([AP]\.?M\.?)?(?![\d.])")

PRICE_TOKEN = re.compile(r"^(-)?\$?(\d{1,6})[.,](\d{2})(-)?$")
QTY_RE = re.compile(
    r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)\s*(LBS?|KG|G|OZ|EA|PCS?|PC)?\s*(?:@|X|\*)\s*\$?(\d{1,5}[.,]\d{2})"
)
QTY_WORD_RE = re.compile(r"\bQTY\s*[:#]?\s*(\d{1,3})\b")
CODE_LEAD_RE = re.compile(r"^(\d{3,14})\s+(?=\S)")
CODE_TRAIL_RE = re.compile(r"\s+(\d{6,14})$")

SUBTOTAL_RE = re.compile(r"\bSUB\s*-?\s*TOTAL\b")
TAX_RE = re.compile(r"\b(TAX|VAT|GST|HST|PST)\b")
TOTAL_RE = re.compile(
    r"\b(GRAND\s*TOTAL|TOTAL\s*DUE|AMOUNT\s*DUE|BALANCE\s*DUE|TOTAL\s*AMOUNT|NET\s*TOTAL|TOTAL)\b"
)
NOT_TOTAL_RE = re.compile(r"TOTAL\s*(TAX|SAVINGS?|SAVED|DISCOUNTS?|ITEMS?|QTY|QUANTITY|POINTS)")
COUNT_RES = [
    re.compile(
        r"\b(?:ITEMS?\s*(?:SOLD|COUNT|PURCHASED|QTY)?|NO\.?\s*OF\s*ITEMS?|#\s*ITEMS?|TOTAL\s*(?:ITEMS?|QTY))"
        r"\s*[:#=]?\s*(\d{1,3})\b"
    ),
    re.compile(r"\b(\d{1,3})\s+ITEMS?\b"),
]
PAYMENT_RE = re.compile(
    r"\b(CASH|CHANGE|VISA|MASTERCARD|MASTER\s*CARD|AMEX|DEBIT|CREDIT|CARD|TENDER(?:ED)?|PAID|"
    r"APPROVED|AUTH|REFUND|BALANCE|PAYMENT|EFT|MC)\b"
)
DISCOUNT_RE = re.compile(r"\b(DISCOUNT|COUPON|SAVINGS?|PROMO|OFF|DEAL|MARKDOWN)\b")
HEADER_NOISE_RE = re.compile(
    r"\b(TEL|PHONE|FAX|WWW|HTTP|STREET|ST\.?|AVE|AVENUE|ROAD|RD|BLVD|SUITE|STE|RECEIPT|INVOICE|"
    r"WELCOME|CASHIER|REGISTER|STORE\s*#|VAT\s*NO|TRN|TAX\s*ID)\b|\.COM\b|[A-Z]@[A-Z]"
)

# OCR confusions that only make sense to fix inside number-like tokens.
DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "Z": "2"})


@dataclass
class Segment:
    id: str
    text: str
    ok: bool
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    segments: list[Segment] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.segments]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    @property
    def upper(self) -> str:
        return self.text.upper()

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.segments)

    @property
    def height(self) -> float:
        return max(s.h for s in self.segments)

    @property
    def y0(self) -> float:
        return min(s.y0 for s in self.segments)

    @property
    def y1(self) -> float:
        return max(s.y1 for s in self.segments)


@dataclass
class Item:
    name: str
    code: str | None
    qty: float
    unit_price: float
    total: float
    line_ids: list[str]
    discount: bool = False
    uncertain: bool = False
    qty_mismatch: bool = False


def _num_token(tok: str) -> str:
    """Repair a token that looks numeric but contains letter look-alikes (e.g. '4.O9' -> '4.09')."""
    if sum(c.isdigit() for c in tok) >= max(1, len(tok) // 2):
        return tok.translate(DIGIT_FIX)
    return tok


def parse_money(tok: str) -> float | None:
    m = PRICE_TOKEN.match(_num_token(tok.strip()))
    if not m:
        return None
    value = float(f"{m.group(2)}.{m.group(3)}")
    return -value if (m.group(1) or m.group(4)) else value


def row_amount(text: str) -> tuple[float | None, str]:
    """Return (amount at the right end of the row, remaining text). Tolerates a trailing tax flag."""
    toks = text.split()
    if toks and len(toks) > 1 and re.fullmatch(r"[A-Z*#]{1,2}", toks[-1]) and parse_money(toks[-2]) is not None:
        toks = toks[:-1]
    if toks:
        tail = toks[-1]
        # "1.18A" style: price with a glued tax flag
        glued = re.fullmatch(r"(.*\d)([A-Z*])", tail)
        for cand in (tail, glued.group(1) if glued else None):
            if cand is None:
                continue
            value = parse_money(cand)
            rest = " ".join(toks[:-1])
            # "3 @ 1.00" - the trailing price is a unit price, not the line amount
            if value is not None and not re.search(r"(@|\bX|\*|/)\s*\$?$", rest.upper()):
                return value, rest
    return None, text


def _deskew(segments: list[Segment], angles: list[float]) -> None:
    """Undo the receipt's tilt so a row's label and its price (far right) end up at the same height."""
    long_ones = sorted(a for s, a in zip(segments, angles) if (s.x1 - s.x0) > 3 * s.h)
    if not long_ones:
        return
    tilt = math.tan(long_ones[len(long_ones) // 2])
    for s in segments:
        shift = -((s.x0 + s.x1) / 2) * tilt
        s.y0 += shift
        s.y1 += shift


def group_rows(segments: list[Segment]) -> list[Row]:
    rows: list[Row] = []
    for seg in sorted(segments, key=lambda s: s.cy):
        best, best_overlap = None, 0.0
        for row in rows[-4:]:
            overlap = min(row.y1, seg.y1) - max(row.y0, seg.y0)
            ratio = overlap / max(1.0, min(row.height, seg.h))
            if ratio > best_overlap:
                best, best_overlap = row, ratio
        if best is not None and best_overlap >= 0.5:
            best.segments.append(seg)
        else:
            rows.append(Row([seg]))
    for row in rows:
        row.segments.sort(key=lambda s: s.x0)
    rows.sort(key=lambda r: r.y0)
    return rows


def _find_date(text: str) -> str | None:
    up = text.upper()
    for pat in DATE_PATTERNS:
        m = pat.search(up)
        if m:
            return m.group(0)
    return None


def _find_time(text: str) -> str | None:
    m = TIME_RE.search(text.upper())
    if not m:
        return None
    out = f"{int(m.group(1)):02d}:{m.group(2)}"
    if m.group(3):
        out += f":{m.group(3)}"
    if m.group(4):
        out += " " + m.group(4).replace(".", "")
    return out


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _split_code(name: str) -> tuple[str, str | None]:
    m = CODE_LEAD_RE.match(name)
    if m:
        return name[m.end():].strip(), m.group(1)
    m = CODE_TRAIL_RE.search(name)
    if m:
        return name[: m.start()].strip(), m.group(1)
    return name.strip(), None


def _qty_info(text: str) -> tuple[float, float, str] | None:
    """Find 'qty @ unit' inside text. Returns (qty, unit_price, text_without_it)."""
    m = QTY_RE.search(text.upper())
    if m:
        qty = float(m.group(1))
        unit = float(m.group(3).replace(",", "."))
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        rest = re.sub(r"/\s*(LBS?|KG|EA)\b", "", rest, flags=re.I).strip()
        return qty, unit, rest
    m = QTY_WORD_RE.search(text.upper())
    if m:
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        return float(m.group(1)), 0.0, rest
    return None


def parse(lines: list[dict]) -> dict:
    lines = [l for l in lines if str(l.get("text", "")).strip()]
    segments = [
        Segment(str(l["id"]), str(l["text"]).strip(), bool(l.get("ok", False)), *map(float, l["box"])) for l in lines
    ]
    _deskew(segments, [float(l.get("angle", 0.0)) for l in lines])
    rows = group_rows(segments)

    roles: dict[str, str] = {}
    result: dict = {
        "vendor": None, "date": None, "time": None, "total": None, "subtotal": None,
        "tax": None, "stated_item_count": None, "items": [],
    }

    # --- locate the summary block (first subtotal/total/tax row) -----------------------------
    summary_start = None
    total_candidates: list[tuple[int, float, int]] = []  # (priority, amount, row index)
    taxes: list[float] = []
    for i, row in enumerate(rows):
        up = row.upper
        amount, _ = row_amount(row.text)
        if SUBTOTAL_RE.search(up):
            summary_start = i if summary_start is None else summary_start
            if amount is not None and result["subtotal"] is None:
                result["subtotal"] = amount
                roles.update({sid: "subtotal" for sid in row.ids})
            continue
        if TOTAL_RE.search(up) and not NOT_TOTAL_RE.search(up) and not PAYMENT_RE.search(up):
            summary_start = i if summary_start is None else summary_start
            if amount is not None and amount > 0:
                kw = TOTAL_RE.search(up).group(1)
                priority = 0 if kw.replace(" ", "") == "TOTAL" else 1
                total_candidates.append((priority, amount, i))
            continue
        if TAX_RE.search(up) and amount is not None and not HEADER_NOISE_RE.search(up):
            summary_start = i if summary_start is None else summary_start
            taxes.append(amount)
            roles.update({sid: "tax" for sid in row.ids})

    if total_candidates:
        _, total, ti = max(total_candidates, key=lambda c: (c[0], c[1]))
        result["total"] = total
        roles.update({sid: "total" for sid in rows[ti].ids})
    if taxes:
        result["tax"] = round(sum(taxes), 2)

    # --- date / time / item count ---------------------------------------------------------
    for row in rows:
        if result["date"] is None and (d := _find_date(row.text)):
            result["date"] = d
            _tag(roles, row, "date", _find_date)
        if result["time"] is None and (t := _find_time(row.text)):
            result["time"] = t
            _tag(roles, row, "time", _find_time)
        if result["stated_item_count"] is None:
            for pat in COUNT_RES:
                m = pat.search(row.upper)
                if m and not PRICE_TOKEN.match(m.group(1)):
                    result["stated_item_count"] = int(m.group(1))
                    roles.update({sid: "count" for sid in row.ids})
                    break

    # --- products: rows with a price, above the summary block --------------------------------
    end = summary_start if summary_start is not None else len(rows)
    items: list[Item] = []
    pending: Row | None = None  # name-only row waiting for its price row
    first_item_row = None
    for i in range(end):
        row = rows[i]
        up = row.upper
        amount, rest = row_amount(row.text)
        if any(roles.get(sid) in ("date", "time", "count") for sid in row.ids) and amount is None:
            pending = None
            continue
        if amount is None:
            if _letters(rest) >= 2 and not HEADER_NOISE_RE.search(up) and not _find_date(row.text):
                qinfo = _qty_info(rest)
                if qinfo and items and _letters(qinfo[2]) < 2:
                    # "2 @ 0.59" printed under an item that already carries its total
                    prev = items[-1]
                    prev.qty, prev.unit_price = qinfo[0], qinfo[1] or prev.unit_price
                    prev.line_ids += row.ids
                    prev.qty_mismatch = bool(qinfo[1]) and abs(qinfo[0] * qinfo[1] - prev.total) > MONEY_TOL
                    pending = None
                else:
                    pending = row
            else:
                qinfo = _qty_info(rest)
                if qinfo and items and qinfo[1]:
                    prev = items[-1]
                    prev.qty, prev.unit_price = qinfo[0], qinfo[1]
                    prev.line_ids += row.ids
                    prev.qty_mismatch = abs(qinfo[0] * qinfo[1] - prev.total) > MONEY_TOL
            continue
        if PAYMENT_RE.search(up) or (_find_date(row.text) and _letters(rest) < 3):
            pending = None
            continue
        if HEADER_NOISE_RE.search(up) and first_item_row is None:
            continue

        qinfo = _qty_info(rest)
        name_part = qinfo[2] if qinfo else rest
        line_ids = list(row.ids)
        uncertain = not row.ok
        if _letters(name_part) < 2 and pending is not None:
            name_part = pending.text
            line_ids = pending.ids + line_ids
            uncertain = uncertain or not pending.ok
        elif _letters(name_part) < 2 and items and qinfo is None:
            # bare price row with no name - most likely a wrapped continuation; skip it
            continue
        pending = None

        name, code = _split_code(name_part)
        discount = amount < 0 or bool(DISCOUNT_RE.search(name.upper()))
        if discount and amount > 0:
            amount = -amount
        qty, unit = (qinfo[0], qinfo[1]) if qinfo and qinfo[1] else (qinfo[0] if qinfo else 1.0, 0.0)
        if not unit:
            unit = round(amount / qty, 2) if qty else amount
        item = Item(
            name=name or "(unnamed)", code=code, qty=qty, unit_price=unit, total=amount,
            line_ids=line_ids, discount=discount, uncertain=uncertain,
            qty_mismatch=abs(qty * unit - amount) > MONEY_TOL + 0.005 * qty and not discount,
        )
        items.append(item)
        first_item_row = i if first_item_row is None else first_item_row

    for item in items:
        for sid in item.line_ids:
            roles[sid] = "item"
    result["items"] = [asdict(it) for it in items]

    # --- vendor: biggest alphabetic text above the first item -----------------------------
    header_end = first_item_row if first_item_row is not None else min(len(rows), 6)
    candidates = []
    for i, row in enumerate(rows[: max(1, min(header_end, 8))]):
        up = row.upper
        if _letters(row.text) < 3 or HEADER_NOISE_RE.search(up) or _find_date(row.text) or _find_time(row.text):
            continue
        if sum(c.isdigit() for c in row.text) > _letters(row.text):
            continue
        candidates.append((row.height, -i, row))
    if candidates:
        vendor_row = max(candidates, key=lambda c: (c[0], c[1]))[2]
        result["vendor"] = vendor_row.text
        roles.update({sid: "vendor" for sid in vendor_row.ids})

    result["validation"] = validate(result)
    result["roles"] = roles
    product_ids = [sid for it in items for sid in it.line_ids]
    result["section"] = {"ids": product_ids, "status": result["validation"]["status"],
                         "message": result["validation"]["message"]}
    result["rows"] = [row.text for row in rows]
    return result


def _tag(roles: dict[str, str], row: Row, role: str, finder) -> None:
    """Give `role` to the segments that hold the match, or to the whole row if it is split across segments."""
    hits = [s.id for s in row.segments if finder(s.text)]
    for sid in hits or row.ids:
        roles.setdefault(sid, role)


def validate(r: dict) -> dict:
    items = r["items"]
    items_sum = round(sum(it["total"] for it in items), 2)
    unit_count = sum(int(it["qty"]) if float(it["qty"]).is_integer() else 1 for it in items if not it["discount"])
    line_count = sum(1 for it in items if not it["discount"])
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("Vendor found", r["vendor"] is not None, r["vendor"] or "not seen yet")
    check("Date found", r["date"] is not None, r["date"] or "not seen yet")
    check("Time found", r["time"] is not None, r["time"] or "not seen yet")
    check("Total found", r["total"] is not None, f"{r['total']:.2f}" if r["total"] is not None else "not seen yet")
    if r["stated_item_count"] is not None:
        ok = r["stated_item_count"] in (unit_count, line_count)
        check("Item count", ok, f"receipt says {r['stated_item_count']}, scanned {line_count} lines / {unit_count} units")
    else:
        check("Item count", None, f"scanned {line_count} lines / {unit_count} units (count not printed or not seen)")
    bad_qty = [it["name"] for it in items if it["qty_mismatch"]]
    check("Qty x unit price", not bad_qty, "all lines consistent" if not bad_qty else "check: " + ", ".join(bad_qty))

    status, message = "pending", "Waiting for items and total"
    if items and r["total"] is not None:
        tax = r["tax"] or 0.0
        if abs(items_sum - r["total"]) <= MONEY_TOL:
            status, message = "ok", f"Items {items_sum:.2f} = total {r['total']:.2f}"
        elif tax and abs(items_sum + tax - r["total"]) <= MONEY_TOL:
            status, message = "ok", f"Items {items_sum:.2f} + tax {tax:.2f} = total {r['total']:.2f}"
        else:
            status = "mismatch"
            message = f"Items {items_sum:.2f}" + (f" + tax {tax:.2f}" if tax else "") + f" != total {r['total']:.2f}"
        if r["subtotal"] is not None:
            check("Items = subtotal", abs(items_sum - r["subtotal"]) <= MONEY_TOL,
                  f"items {items_sum:.2f} vs subtotal {r['subtotal']:.2f}")
    check("Items add up to total", {"ok": True, "mismatch": False}.get(status), message)
    return {"status": status, "message": message, "items_sum": items_sum, "checks": checks,
            "line_count": line_count, "unit_count": unit_count}
