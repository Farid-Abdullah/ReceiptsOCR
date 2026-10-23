"""Turn OCR text lines from receipt photos into structured fields and validate them.

Input is what the camera component sends for one photo: a list of text segments, each with an
id, the text, a confidence, a box in photo pixels and the text angle.

The guided scan photographs one area at a time, so there is a finder per step (find_vendor,
find_datetime, find_total, find_item_count, find_items) that returns candidates for the user to
confirm, plus merge_items for overlapping item photos and summarize for the final checks.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from difflib import SequenceMatcher

MONEY_TOL = 0.011

MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC"
MONTH_NUM = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
DATE_PATTERNS = [
    ("dmy", re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b")),
    ("ymd", re.compile(r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b")),
    ("d_mon_y", re.compile(rf"\b(\d{{1,2}})[\s\-]?({MONTHS})[A-Z]*[\s,\-]*(\d{{4}}|\d{{2}})\b")),
    ("mon_d_y", re.compile(rf"\b({MONTHS})[A-Z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}}|\d{{2}})\b")),
]
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])\s?:\s?([0-5]\d)(?:\s?:\s?([0-5]\d))?\s*([AP])?\.?M?\b")
# a time with letters that look like digits, e.g. "B:44 PM"
TIMEISH_RE = re.compile(r"(?<![A-Z0-9])([0-9BOSIlZ]{1,2})\s?:\s?([0-9OSIlZ]{2})(?![0-9])", re.I)

CURRENCY = r"(?:QR|QAR|SAR|AED|KWD|BHD|OMR|USD|EUR|GBP|RS|INR|\$|€|£)"
AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}(?:[.,]\d{1,2})?"  # 1,234.50 / 66 / 12.5 / 12,50
PRICE_TOKEN = re.compile(r"^(-)?\$?(\d{1,3}(?:,\d{3})+|\d{1,6})[.,](\d{2})(-)?$")
QTY_RE = re.compile(
    r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)\s*(LBS?|KG|G|OZ|EA|PCS?|PC)?\s*(?:@|X|\*)\s*\$?(\d{1,5}[.,]\d{2})"
)
QTY_WORD_RE = re.compile(r"\bQTY\s*[:#]?\s*(\d{1,3})\b")
BARCODE_RE = re.compile(r"^\d{8,14}$")
CODE_LEAD_RE = re.compile(r"^(\d{3,14})\s+(?=\S)")
CODE_TRAIL_RE = re.compile(r"\s+(\d{6,14})$")

# What the label next to an amount says about it. Covers the wording of common receipt formats
# (supermarkets such as Al Meera, LuLu, Carrefour, delivery apps, restaurants).
TOTAL_PHRASE_RE = re.compile(
    r"\b(GRAND\s*TOTAL|NET\s*TOTAL|NET\s*AMOUNT|NET\s*PAYABLE|TOTAL\s*AMOUNT|AMOUNT\s*DUE|TOTAL\s*DUE|"
    r"BALANCE\s*DUE|TOTAL\s*PAYABLE|AMOUNT\s*PAYABLE|BILL\s*(?:AMOUNT|TOTAL)|INVOICE\s*(?:TOTAL|AMOUNT)|"
    r"TOTAL\s*(?:QAR|QR|AED|SAR|KWD|BHD|OMR|USD))\b"
)
NOT_TOTAL_RE = re.compile(r"TOTAL\s*(TAX|VAT|SAVINGS?|SAVED|DISCOUNTS?|ITEMS?|QTY|QUANTITY|POINTS|WEIGHT|UNITS|PCS)")
NEVER_TOTAL_RE = re.compile(  # amounts that are never the total: payment, change, points, rounding...
    r"\b(POINTS?|PTS|LOYALTY|EARNED|REDEEMED|BALANCE|CASH|CHANGE|TENDER(?:ED)?|ROUND(?:ING)?|ROUND\s*OFF|"
    r"SAVINGS?|SAVED|REFUND|EFT|AUTH|APPROVAL|APPROVED|TRANS(?:ACTION)?|REF|RRN|TERMINAL|RECEIPT|MOBILE|PHONE)\b"
)
PAYMENT_RE = re.compile(  # card payments: usually equal to the total, but not the total line itself
    r"\b(VISA|MASTER\s*CARD|MASTERCARD|AMEX|DEBIT|CREDIT|CARD|MADA|NAPS|APPLE\s*PAY|PAID|PAYMENT)\b"
)
COUNT_RES = [
    re.compile(
        r"\b(?:ITEMS?\s*(?:SOLD|COUNT|PURCHASED|QTY)?|NO\.?\s*OF\s*ITEMS?|#\s*ITEMS?|TOTAL\s*(?:ITEMS?|QTY))"
        r"\s*[:#=]?\s*(\d{1,3})\b"
    ),
    re.compile(r"\b(\d{1,3})\s+ITEMS?\b"),
]
# summary lines between the subtotal and the total, with the sign they apply to the items sum
ADJUSTMENTS = [
    ("tax", +1, re.compile(r"\b(TAX|VAT|GST|HST|PST)\b")),
    ("discount", -1, re.compile(r"\b(DISCOUNT|COUPON|VOUCHER|PROMO(?:TION)?|OFFER|LESS)\b")),
    ("delivery", +1, re.compile(r"\b(DELIVERY|SHIPPING)\b")),
    ("service", +1, re.compile(r"\b(SERVICE|FEE|TIP|CHARGE|PACKAGING|BAG)\b")),
]
GROSS_RE = re.compile(r"\b(GROSS|SUB\s*-?\s*TOTAL)\b")
DISCOUNT_RE = re.compile(r"\b(DISCOUNT|COUPON|SAVINGS?|PROMO|OFF|DEAL|MARKDOWN)\b")
VENDOR_LABEL_WORDS = ["VENDOR", "MERCHANT", "SHOP", "RESTAURANT", "SELLER", "OUTLET"]
# store names to recognise even when a stylised logo comes out garbled ("CARREF0UR", "LULU HYPERMARKET DOHA")
KNOWN_STORES = {
    "Al Meera": ["ALMEERA"], "LuLu Hypermarket": ["LULU"], "Carrefour": ["CARREFOUR"],
    "Monoprix": ["MONOPRIX"], "Spar": ["SPAR"], "MegaMart": ["MEGAMART"], "Family Food Centre": ["FAMILYFOOD"],
    "Safari Hypermarket": ["SAFARI"], "Ansar Gallery": ["ANSAR"], "Grand Hypermarket": ["GRANDHYPER"],
    "Quality Hypermarket": ["QUALITYHYPER"], "Rawabi Hypermarket": ["RAWABI"], "Saudia Hypermarket": ["SAUDIA"],
    "Paris Hypermarket": ["PARISHYPER"], "Food Palace": ["FOODPALACE"],
}
# delivery apps print their own logo; the store is the restaurant named on the slip
DELIVERY_APPS = ["RAFEEQ", "TALABAT", "SNOONU", "DELIVEROO", "CAREEM", "UBEREATS", "KEETA"]
HEADER_NOISE_RE = re.compile(
    r"\b(TEL|PHONE|FAX|WWW|HTTP|STREET|ST\.?|AVE|AVENUE|ROAD|RD|BLVD|SUITE|STE|RECEIPT|INVOICE|"
    r"WELCOME|CASHIER|REGISTER|STORE\s*(?:#|NO)|TERMINAL|EMPLOYEE|CUSTOMER|MOBILE|ADDRESS|BUILDING|"
    r"FLOOR|APARTMENT|ZONE|ORDER|VAT\s*NO|TRN|TAX\s*ID|DESCRIPTION|QTY|PRICE|AMOUNT)\b|\.COM\b|[A-Z]@[A-Z]"
)
COLUMN_HEADER_RE = re.compile(r"\b(ITEM|DESCRIPTION|QTY|QUANTITY|PRICE|AMOUNT)\b")

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
    angle: float = 0.0
    conf: float = 1.0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def level(self) -> float:
        """Height of this segment's text line at x = 0 (removes tilt, for ordering rows)."""
        return self.cy - self.cx * math.tan(self.angle)


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
    def level(self) -> float:
        return sum(s.level for s in self.segments) / len(self.segments)


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


# --- rows ----------------------------------------------------------------------------------------------
def _clean_segment_text(text: str) -> str:
    text = text.strip()
    # the recognizer sometimes drops the decimal point of a price: "4 00" -> "4.00"
    m = re.fullmatch(r"(\d{1,4}) (\d{2})", text)
    return f"{m.group(1)}.{m.group(2)}" if m else text


def _segments(lines: list[dict]) -> list[Segment]:
    segs = []
    for l in lines:
        text = _clean_segment_text(str(l.get("text", "")))
        conf = float(l.get("conf", 1.0))
        # 1-2 character readings with low confidence are slivers of bigger letters, not text
        if not text or (len(text) <= 2 and conf < 0.5):
            continue
        segs.append(Segment(str(l["id"]), text, bool(l.get("ok", False)), *map(float, l["box"]),
                            angle=float(l.get("angle", 0.0)), conf=conf))
    return segs


def _line_gap(a: Segment, b: Segment) -> float:
    """Vertical distance between two segments' text lines, measured along the longer one's slope
    (a short segment like "7.25" has no reliable slope of its own)."""
    ref, other = (a, b) if (a.x1 - a.x0) >= (b.x1 - b.x0) else (b, a)
    return abs(other.cy - (ref.cy + (other.cx - ref.cx) * math.tan(ref.angle)))


def group_rows(segments: list[Segment]) -> list[Row]:
    """Group segments into printed rows. Each segment's own angle is followed, so curved or tilted
    paper does not merge a line with the next one."""
    rows: list[Row] = []
    for seg in sorted(segments, key=lambda s: s.level):
        best, best_gap = None, math.inf
        for row in rows[-6:]:
            gap = min(_line_gap(t, seg) for t in row.segments)
            if gap < best_gap:
                best, best_gap = row, gap
        if best is not None and best_gap <= 0.35 * min(seg.h, best.height):
            best.segments.append(seg)
        else:
            rows.append(Row([seg]))
    for row in rows:
        row.segments.sort(key=lambda s: s.x0)
    rows.sort(key=lambda r: r.level)
    return rows


def _rows(lines: list[dict]) -> list[Row]:
    return group_rows(_segments(lines))


# --- amounts ----------------------------------------------------------------------------------------
def _num_token(tok: str) -> str:
    """Repair a token that looks numeric but contains letter look-alikes (e.g. '4.O9' -> '4.09')."""
    if sum(c.isdigit() for c in tok) >= max(1, len(tok) // 2):
        return tok.translate(DIGIT_FIX)
    return tok


def parse_money(tok: str) -> float | None:
    m = PRICE_TOKEN.match(_num_token(tok.strip()))
    if not m:
        return None
    value = float(f"{m.group(2).replace(',', '')}.{m.group(3)}")
    return -value if (m.group(1) or m.group(4)) else value


def _to_float(tok: str) -> float:
    """"1,234.50" -> 1234.5, "12,50" -> 12.5, "66" -> 66.0"""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", tok):
        return float(tok.replace(",", ""))
    return float(tok.replace(",", "."))


def _number(tok: str) -> float | None:
    tok = tok.strip(".,:;()")
    if re.fullmatch(r"\d{1,6}(?:[.,]\d{1,3})?", tok):
        return float(tok.replace(",", "."))
    return None


def row_amount(text: str) -> tuple[float | None, str]:
    """The amount printed at the right end of a row, and the rest of the row's text.
    Accepts "3.49", "3.49 A" (tax flag), "-1.00" / "1.00-" (discount), "66 QR", "40QR", "QR 66"."""
    toks = text.split()
    if not toks:
        return None, text
    # whole or decimal amount with a currency next to it: "66 QR", "40QR", "- 40 QR", "QAR 500.00"
    up = text.upper()
    m = re.search(rf"(-\s*)?(?<![A-Z0-9.,])({AMOUNT})\s*{CURRENCY}\.?\s*(-)?$", up)
    if m:
        value = _to_float(m.group(2))
        return (-value if (m.group(1) or m.group(3)) else value), text[: m.start()].strip()
    m = re.search(rf"(?<![A-Z]){CURRENCY}\s*:?\s*({AMOUNT})$", up)
    if m:
        return _to_float(m.group(1)), text[: m.start()].strip()
    if len(toks) > 1 and re.fullmatch(r"[A-Z*#]{1,2}", toks[-1]) and parse_money(toks[-2]) is not None:
        toks = toks[:-1]
    tail = toks[-1]
    glued = re.fullmatch(r"(.*\d)([A-Z*])", tail)  # "1.18A": price with a glued tax flag
    for cand in (tail, glued.group(1) if glued else None):
        if cand is None:
            continue
        value = parse_money(cand)
        rest = " ".join(toks[:-1])
        # "3 @ 1.00" - the trailing price is a unit price, not the line amount
        if value is not None and not re.search(r"(@|\bX|\*|/)\s*\$?$", rest.upper()):
            return value, rest
    return None, text


def _numbers_only(row: Row) -> list[float] | None:
    """The values of a price row made only of numbers, else None: "1 8 8" (qty, price, total) or
    "20 20" (price, total). The numbers must agree with each other, so stray digits don't count."""
    toks = [t for t in row.text.replace("|", " ").split() if re.search(r"[A-Za-z0-9]", t)]  # drop symbol junk
    nums = [_number(t) for t in toks]
    if len(nums) < 2 or any(n is None for n in nums):
        return None
    consistent = abs(nums[-1] - nums[-2]) <= MONEY_TOL or (
        len(nums) >= 3 and abs(nums[-3] * nums[-2] - nums[-1]) <= MONEY_TOL)
    return nums if consistent else None


def _amount(row: Row) -> tuple[float | None, str]:
    """row_amount, plus whole-number prices in rows of bare numbers ("1 8 8" = qty, price, total)."""
    value, rest = row_amount(row.text)
    if value is not None:
        return value, rest
    nums = _numbers_only(row)
    if nums and nums[-1] > 0 and not BARCODE_RE.match(row.text.split()[-1]):
        return nums[-1], " ".join(row.text.split()[:-1])
    return None, row.text


# --- words ------------------------------------------------------------------------------------------
def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _latin(text: str) -> int:
    return sum("A" <= c <= "Z" or "a" <= c <= "z" for c in text)


def _edit_ratio(a: str, b: str) -> float:
    """1 - (letters changed, added or dropped) / longer length. Suits OCR misreads ("VERDER"/"VENDOR")."""
    if a == b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b), 1)


def _has_word(text: str, word: str, min_ratio: float = 0.8) -> bool:
    """`word` appears in text, allowing one misread letter in longer words ("TATAL" for "TOTAL")."""
    for tok in re.findall(r"[A-Z]+", text.upper()):
        if tok == word or (len(word) >= 5 and _edit_ratio(tok, word) >= min_ratio):
            return True
    return False


def _is_column_header(up: str) -> bool:
    return len(COLUMN_HEADER_RE.findall(up)) >= 2


def _is_total_row(up: str) -> bool:
    """Does this text name the final total? (not a subtotal, a count, savings, payment or points)"""
    if _is_subtotal_row(up) or NOT_TOTAL_RE.search(up) or _is_column_header(up):
        return False
    if TOTAL_PHRASE_RE.search(up):
        return True
    return _has_word(up, "TOTAL") and not NEVER_TOTAL_RE.search(up)


def _is_subtotal_row(up: str) -> bool:
    """Subtotal, or the "Gross Amount / Gross Total" some supermarkets print before discounts."""
    return (bool(re.search(r"\bSUB\s*-?\s*TOTAL\b|\bGROSS\s*(?:TOTAL|AMOUNT|SALES|VALUE)\b", up))
            or _has_word(up, "SUBTOTAL", 0.85))


def _name_score(row: Row) -> float:
    """How much a row looks like real words: Latin letters in words of 3+ letters, by confidence.
    Arabic lines read by the English model come out as short junk ("AÎ¼s E") and score low."""
    return sum(sum(_latin(w) for w in s.text.split() if _latin(w) >= 3) * s.conf for s in row.segments)


def _is_name(row: Row) -> bool:
    """Could this row be an item's name? (Latin words, not a header, total, date or bare code.)"""
    text = row.text
    latin = _latin(text)
    if latin < 3 or latin < 0.5 * sum(c.isalnum() for c in text):
        return False
    up = row.upper
    return not (_find_date(text) or _find_time(text) or _is_total_row(up) or _is_subtotal_row(up)
                or _is_column_header(up))


# --- dates and times ----------------------------------------------------------------------------------
def _fix_time_digits(text: str) -> str:
    return TIMEISH_RE.sub(lambda m: f"{m.group(1).translate(DIGIT_FIX)}:{m.group(2).translate(DIGIT_FIX)}", text)


def _find_date(text: str) -> str | None:
    """The first date in text, as ISO (YYYY-MM-DD). Numeric dates are read day-first unless that is
    impossible (e.g. 10/21/2026)."""
    up = text.upper()
    for kind, pat in DATE_PATTERNS:
        m = pat.search(up)
        if not m:
            continue
        g = m.groups()
        try:
            if kind == "dmy":
                d, mo, y = int(g[0]), int(g[1]), int(g[2])
                if mo > 12 and d <= 12:
                    d, mo = mo, d
            elif kind == "ymd":
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif kind == "d_mon_y":
                d, mo, y = int(g[0]), MONTH_NUM[g[1][:3]], int(g[2])
            else:
                mo, d, y = MONTH_NUM[g[0][:3]], int(g[1]), int(g[2])
            y = y + 2000 if y < 100 else y
            return date(y, mo, d).isoformat()
        except (ValueError, KeyError):
            continue
    return None


def _find_time(text: str) -> str | None:
    """The first time in text, as 24-hour HH:MM."""
    m = TIME_RE.search(_fix_time_digits(text).upper())
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    if m.group(4) == "P" and h < 12:
        h += 12
    elif m.group(4) == "A" and h == 12:
        h = 0
    return f"{h:02d}:{minute:02d}"


# --- guided scan: one finder per step -------------------------------------------------------------
# Each returns candidates [{"value", "label", "ids"}], best first; "ids" are the segments to outline.

def _candidate(value, row: Row, ids: list[str] | None = None) -> dict:
    return {"value": value, "label": row.text, "ids": ids or row.ids}


def _vendor_from_label(row: Row) -> str | None:
    """"Vendor (...) : Pizza Place - Downtown" -> "Pizza Place" (the part after " - " is usually the branch)."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z ]{2,20}?)\s*(?:\([^)]*\)?)?\s*[:;]\s*(.+)$", row.text)
    if not m:
        return None
    label = m.group(1).upper().strip()
    if max(_edit_ratio(label, w) for w in VENDOR_LABEL_WORDS) < 0.6:
        return None
    value = re.split(r"\s+-\s+", m.group(2).strip())[0].strip(" -")
    return value if _latin(value) >= 2 else None


def _known_store(text: str) -> str | None:
    squashed = re.sub(r"[^A-Z]", "", text.upper())
    words = re.findall(r"[A-Z]+", text.upper())
    for name, keys in KNOWN_STORES.items():
        for key in keys:
            if (key in squashed if len(key) >= 5 else key in words) or (
                    len(squashed) <= len(key) + 3 and _edit_ratio(squashed, key) >= 0.8):
                return name
    return None


def find_vendor(lines: list[dict], limit: int = 3) -> list[dict]:
    """The store name: a known store (even from a garbled logo), a "Vendor:" style label, otherwise
    the largest confident line of words. Addresses, phone numbers, labels, prices and delivery-app
    logos rank last."""
    known, labelled, ranked = [], [], []
    rows = _rows(lines)
    # the store's name is printed above the prices; below them a match is a store-brand product
    first_price = next((i for i, r in enumerate(rows) if _amount(r)[0] is not None), len(rows))
    for i, row in enumerate(rows):
        store = _known_store(row.text) if i < first_price else None
        if store and store not in [c["value"] for c in known]:
            known.append(_candidate(store, row))
            continue
        if value := _vendor_from_label(row):
            labelled.append(_candidate(value, row))
            continue
        text = row.text
        if _latin(text) < 3 or _find_date(text) or _find_time(text) or _amount(row)[0] is not None:
            continue
        if sum(c.isdigit() for c in text) > _letters(text):
            continue
        noisy = bool(HEADER_NOISE_RE.search(row.upper)) or ":" in text
        app = any(_edit_ratio(re.sub(r"[^A-Z]", "", row.upper), a) >= 0.75 for a in DELIVERY_APPS)
        # stylised logos and Arabic lines come out of the English model as low-confidence junk
        unsure = sum(seg.conf for seg in row.segments) / len(row.segments) < 0.7
        ranked.append((app, noisy, unsure, -row.height, row))
    ranked.sort(key=lambda r: r[:4])
    return (labelled + known + [_candidate(r[4].text, r[4]) for r in ranked])[:limit]


def find_datetime(lines: list[dict]) -> dict:
    """{"date": [...], "time": [...]}: date as YYYY-MM-DD, time as 24-hour HH:MM."""
    found: dict = {"date": [], "time": []}
    for row in _rows(lines):
        for key, finder in (("date", _find_date), ("time", _find_time)):
            value = finder(row.text)
            if value and value not in [c["value"] for c in found[key]]:
                ids = [s.id for s in row.segments if finder(s.text)] or row.ids
                found[key].append(_candidate(value, row, ids))
    return found


def _labelled_amounts(rows: list[Row]) -> list[dict]:
    """Every amount in the photo with the words that describe it. The label is the text on the same
    row; an amount printed apart ("Total" / "66 QR", or "12.50" a hair above "Total") takes the
    label-only row on the same line, else the one just above; a one-word label ("Fee") also takes
    a label-only row right above it ("Delivery")."""
    def label_only(j: int) -> bool:
        # confident text only: Arabic labels read by the English model come out as junk ("Wa")
        return (0 <= j < len(rows) and _amount(rows[j])[0] is None and _latin(rows[j].text) >= 2
                and sum(seg.conf for seg in rows[j].segments) / len(rows[j].segments) >= 0.7)

    out = []
    for i, row in enumerate(rows):
        value, rest = _amount(row)
        if value is None:
            continue
        label, ids = rest.strip(), list(row.ids)
        h = row.height
        if _latin(label) < 2:
            same_line = [j for j in (i - 1, i + 1) if label_only(j) and abs(rows[j].level - row.level) <= 0.6 * h]
            above = next((j for j in (i - 1, i - 2, i - 3) if label_only(j)
                          and all(_amount(rows[k])[0] is None for k in range(j, i))), None)
            j = min(same_line, key=lambda k: abs(rows[k].level - row.level)) if same_line else above
            if j is not None:
                label, ids = rows[j].text, rows[j].ids + ids
        elif len(label.split()) == 1 and label_only(i - 1) and row.level - rows[i - 1].level <= 1.6 * h:
            label, ids = f"{rows[i - 1].text} {label}", rows[i - 1].ids + ids
        out.append({"value": value, "label": label, "ids": ids, "row": i})
    return out


def _adjustment_kind(up: str) -> tuple[str, int] | None:
    if NEVER_TOTAL_RE.search(up) or _is_total_row(up) or _is_subtotal_row(up):
        return None
    for kind, sign, pat in ADJUSTMENTS:
        if pat.search(up) and not (kind == "tax" and re.search(r"TAX\s*(ID|NO|REG|INVOICE)", up)):
            return kind, sign
    return None


def _summary(rows: list[Row]) -> dict:
    """Subtotal, adjustments (tax, discount, delivery...), labelled amounts and where the item list
    ends (the first subtotal, total or summary line)."""
    end = len(rows)
    for i, row in enumerate(rows):
        up = row.upper
        kind = _adjustment_kind(up)
        if _is_subtotal_row(up) or _is_total_row(up) or (kind and kind[0] != "discount"):
            end = i
            break
    amounts = _labelled_amounts(rows)
    subtotal, adjustments, seen = None, [], set()
    for a in amounts:
        up = a["label"].upper()
        if subtotal is None and _is_subtotal_row(up):
            subtotal = abs(a["value"])
            continue
        kind = _adjustment_kind(up)
        # item-level discounts inside the list belong to the items; summary ones come after it
        if kind and a["row"] >= end and kind[0] not in seen:
            seen.add(kind[0])
            adjustments.append({"kind": kind[0], "label": a["label"], "amount": round(kind[1] * abs(a["value"]), 2)})
    return {"subtotal": subtotal, "adjustments": adjustments, "amounts": amounts, "end": end}


def _total_scores(s: dict) -> list[dict]:
    """Score every amount as the final total. Words next to it count most (TOTAL, NET AMOUNT, AMOUNT
    DUE... up; cash, change, points, rounding, savings, subtotal, tax... down); the value being
    printed again (total, net amount, card payment), or matching subtotal + adjustments or
    cash - change, counts too."""
    amounts = s["amounts"]
    by_value: dict[float, int] = {}
    for a in amounts:
        by_value[round(a["value"], 2)] = by_value.get(round(a["value"], 2), 0) + 1
    expected = None
    if s["subtotal"] is not None:
        expected = round(s["subtotal"] + sum(a["amount"] for a in s["adjustments"]), 2)
    cash = [a["value"] for a in amounts if re.search(r"\b(CASH|TENDER)", a["label"].upper())
            and not re.search(r"CHANGE|BALANCE", a["label"].upper())]
    change = [a["value"] for a in amounts if re.search(r"\b(CHANGE|BALANCE)\b", a["label"].upper())
              and not re.search(r"POINTS?|DUE", a["label"].upper())]
    paid = round(max(cash) - max(change), 2) if cash and change else None

    scored = []
    for a in amounts:
        up, v = a["label"].upper(), round(a["value"], 2)
        if v <= 0:
            continue
        score = 0.0
        if TOTAL_PHRASE_RE.search(up):
            score += 6
        elif _is_total_row(up):
            score += 5
        if _is_subtotal_row(up) or GROSS_RE.search(up):
            score -= 4
        if NOT_TOTAL_RE.search(up) or _adjustment_kind(up) or (NEVER_TOTAL_RE.search(up) and not TOTAL_PHRASE_RE.search(up)):
            score -= 8
        elif PAYMENT_RE.search(up):
            score -= 2
        score += 1.5 * min(3, by_value[v] - 1)
        if expected is not None and abs(expected - v) <= MONEY_TOL:
            score += 4
        if paid is not None and abs(paid - v) <= 1.0:
            score += 3 if abs(paid - v) > MONEY_TOL else 4
        scored.append({**a, "score": score})
    return sorted(scored, key=lambda a: (-a["score"], -a["value"]))


def find_total(lines: list[dict]) -> dict:
    """{"total": [...], "subtotal": float|None, "adjustments": [{"kind", "label", "amount"}]}.
    Every amount in the photo is offered, most likely total first (see _total_scores)."""
    s = _summary(_rows(lines))
    cands, seen = [], set()
    for a in _total_scores(s):
        if a["value"] not in seen:
            seen.add(a["value"])
            cands.append({"value": a["value"], "label": a["label"], "ids": a["ids"]})
    return {"total": cands, "subtotal": s["subtotal"], "adjustments": s["adjustments"]}


def find_item_count(lines: list[dict]) -> list[dict]:
    """"ITEMS SOLD: 15", or an "Items" column header with the number printed under it."""
    rows = _rows(lines)
    found = []
    for i, row in enumerate(rows):
        for pat in COUNT_RES:
            m = pat.search(row.upper)
            if m and int(m.group(1)) not in [c["value"] for c in found]:
                found.append(_candidate(int(m.group(1)), row))
                break
        else:
            # "Items | Total | Net Amount" header, values printed in the first row below that has amounts
            head = next((s for s in row.segments if _edit_ratio(s.text.upper().strip(".:"), "ITEMS") >= 0.8), None)
            values = next((r for r in rows[i + 1:i + 4] if any(parse_money(t.text) is not None for t in r.segments)), None)
            if head is not None and values is not None:
                below = [s for s in values.segments if re.fullmatch(r"\d{1,3}", s.text)]
                if below:
                    s = min(below, key=lambda b: abs(b.cx - head.cx))
                    if abs(s.cx - head.cx) < 3 * head.h and int(s.text) not in [c["value"] for c in found]:
                        found.append({"value": int(s.text), "label": f"{row.text} / {values.text}",
                                      "ids": [head.id, s.id]})
    return found


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


def _price_row(row: Row) -> dict | None:
    """Read a row that ends with an item amount: code, quantity and unit price columns, and any name."""
    amount, rest = _amount(row)
    if amount is None:
        return None
    toks = rest.split()
    code = None
    barcode_at = next((k for k, t in enumerate(toks) if BARCODE_RE.match(t)), None)
    if barcode_at is not None:
        code = toks[barcode_at]
        toks = toks[barcode_at + 1:]  # on barcode-first receipts anything left of the code is noise
    qinfo = _qty_info(" ".join(toks))
    qty, unit = (qinfo[0], qinfo[1]) if qinfo else (None, None)
    words = qinfo[2].split() if qinfo else toks
    # trailing number columns: [qty] [unit price] before the amount
    nums = []
    while words and _number(words[-1]) is not None:
        nums.insert(0, _number(words.pop()))
    if qty is None and len(nums) >= 2:
        q, u = nums[-2], nums[-1]
        if abs(q * u - amount) <= MONEY_TOL + 0.006 * max(1.0, q):
            qty, unit = q, u
    elif qty is None and len(nums) == 1:
        n = nums[0]
        if abs(n - amount) <= MONEY_TOL:
            qty, unit = 1.0, n
        elif n.is_integer() and 0 < n < 100 and abs(amount / n - round(amount / n, 2)) < 1e-6:
            qty, unit = n, round(amount / n, 2)
    name = " ".join(words).strip(" -.:")
    if code is None:
        name, code = _split_code(name)
    return {"amount": amount, "qty": qty, "unit": unit, "code": code, "name": name if _latin(name) >= 2 else ""}


def find_items(lines: list[dict]) -> dict:
    """Items in a photo of the item list, plus the subtotal, adjustments and total if the photo
    reached them. Handles the name on the same row as the price, the name on the row above
    ("Orange juice" / "1 8 8"), and barcode-first receipts where the name follows the price row."""
    rows = _rows(lines)
    s = _summary(rows)
    items: list[Item] = []
    pending: Row | None = None  # first name-like row since the last item (for names printed above)
    used: set[int] = set()
    for i in range(s["end"]):
        if i in used:
            continue
        row = rows[i]
        if _is_column_header(row.upper):
            pending = None  # column titles: the list starts below
            continue
        info = _price_row(row)
        if info is None:
            qinfo = _qty_info(row.text)
            if qinfo and qinfo[1] and _latin(qinfo[2]) < 2 and items and (
                    abs(qinfo[0] * qinfo[1] - items[-1].total) <= MONEY_TOL + 0.006 * max(1.0, qinfo[0])):
                # "2 X 3.50" printed under an item that already shows its amount
                items[-1].qty, items[-1].unit_price = qinfo[0], qinfo[1]
                items[-1].line_ids += row.ids
                items[-1].qty_mismatch = False
                continue
            if _is_name(row) and not HEADER_NOISE_RE.search(row.upper) and (
                    pending is None or _name_score(row) > _name_score(pending)):
                pending = row
            continue
        if NEVER_TOTAL_RE.search(row.upper) or PAYMENT_RE.search(row.upper) or (
                _find_date(row.text) and not info["name"]):
            pending = None
            continue
        name, ids = info["name"], list(row.ids)
        if not name and info["code"]:
            # barcode-first layout: the name is on the next row that reads like words
            for j in range(i + 1, min(i + 4, s["end"])):
                if _price_row(rows[j]) is not None:
                    break
                if _is_name(rows[j]):
                    name, ids = rows[j].text, ids + rows[j].ids
                    used.add(j)
                    break
        if not name and pending is not None:
            name, ids = pending.text, pending.ids + ids
        pending = None
        if not name and not info["code"] and info["qty"] is None:
            continue  # a bare amount with nothing to say what it is
        amount = info["amount"]
        name = re.split(r"\s+-\s*$|\s+-\s+(?=[^A-Za-z]*$)", name)[0].strip(" -") or "(unnamed)"
        discount = amount < 0 or bool(DISCOUNT_RE.search(name.upper()))
        if discount and amount > 0:
            amount = -amount
        qty = info["qty"] if info["qty"] is not None else 1.0
        unit = info["unit"] if info["unit"] else (round(amount / qty, 2) if qty else amount)
        items.append(Item(
            name=name, code=info["code"], qty=qty, unit_price=unit, total=amount, line_ids=ids,
            discount=discount, uncertain=not all(rows[k].ok for k in range(len(rows)) if set(rows[k].ids) & set(ids)),
            qty_mismatch=not discount and abs(qty * unit - amount) > MONEY_TOL + 0.006 * max(1.0, qty),
        ))
    scored = [a for a in _total_scores(s) if a["score"] >= 5]
    total = scored[0]["value"] if scored else None
    return {"items": [asdict(it) for it in items], "subtotal": s["subtotal"], "adjustments": s["adjustments"],
            "total": total}


# --- several item photos, and the final checks ----------------------------------------------------------
def _same_item(a: dict, b: dict) -> bool:
    if abs(a["total"] - b["total"]) > MONEY_TOL:
        return False
    return SequenceMatcher(None, a["name"].upper(), b["name"].upper()).ratio() >= 0.75


def merge_items(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """Append a new photo's items. Consecutive photos may overlap: the longest run at the end of the
    list that repeats at the start of the new photo is skipped. Returns (merged, skipped)."""
    for k in range(min(len(existing), len(new)), 0, -1):
        if all(_same_item(existing[len(existing) - k + i], new[i]) for i in range(k)):
            return existing + new[k:], k
    return existing + new, 0


def validate(r: dict) -> dict:
    """Checks over vendor, date, time, total, item count and items vs total (with adjustments)."""
    items = r["items"]
    items_sum = round(sum(it["total"] for it in items), 2)
    unit_count = sum(int(it["qty"]) if float(it["qty"]).is_integer() else 1 for it in items if not it["discount"])
    line_count = sum(1 for it in items if not it["discount"])
    adjustments = r.get("adjustments") or []
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
        adjusted = round(items_sum + sum(a["amount"] for a in adjustments), 2)
        parts = f"Items {items_sum:.2f}" + "".join(
            f" {'+' if a['amount'] >= 0 else '-'} {a['kind']} {abs(a['amount']):.2f}" for a in adjustments)
        if abs(items_sum - r["total"]) <= MONEY_TOL:
            status, message = "ok", f"Items {items_sum:.2f} = total {r['total']:.2f}"
        elif adjustments and abs(adjusted - r["total"]) <= MONEY_TOL:
            status, message = "ok", f"{parts} = total {r['total']:.2f}"
        else:
            status, message = "mismatch", f"{parts} != total {r['total']:.2f}"
        if r.get("subtotal") is not None:
            check("Items = subtotal", abs(items_sum - r["subtotal"]) <= MONEY_TOL,
                  f"items {items_sum:.2f} vs subtotal {r['subtotal']:.2f}")
    check("Items add up to total", {"ok": True, "mismatch": False}.get(status), message)
    return {"status": status, "message": message, "items_sum": items_sum, "checks": checks,
            "line_count": line_count, "unit_count": unit_count}


def summarize(fields: dict, items: list[dict]) -> dict:
    """Final checks over the confirmed fields and items."""
    r = {
        "vendor": fields.get("vendor"), "date": fields.get("date"), "time": fields.get("time"),
        "total": fields.get("total"), "subtotal": fields.get("subtotal"),
        "adjustments": fields.get("adjustments") or [], "stated_item_count": fields.get("item_count"),
        "items": items,
    }
    return validate(r)


# --- Version 1 (live scan): the whole receipt at once ------------------------------------------------
def read_receipt(lines: list[dict]) -> dict:
    """Every field's best candidate, the items and the checks for a whole receipt, plus which
    segments hold what ("roles", "section") so the live overlay can label them."""
    vendor, dt, totals = find_vendor(lines), find_datetime(lines), find_total(lines)
    count, found = find_item_count(lines), find_items(lines)
    first = lambda cands: cands[0] if cands else None  # noqa: E731
    fields = {
        "vendor": first(vendor)["value"] if vendor else None,
        "date": first(dt["date"])["value"] if dt["date"] else None,
        "time": first(dt["time"])["value"] if dt["time"] else None,
        "total": first(totals["total"])["value"] if totals["total"] else None,
        "subtotal": totals["subtotal"], "adjustments": totals["adjustments"],
        "item_count": first(count)["value"] if count else None,
    }
    items = found["items"]
    validation = summarize(fields, items)
    roles: dict[str, str] = {}
    for role, cands in (("vendor", vendor), ("date", dt["date"]), ("time", dt["time"]),
                        ("total", totals["total"]), ("count", count)):
        for sid in (cands[0]["ids"] if cands else []):
            roles.setdefault(sid, role)
    item_ids = [sid for it in items for sid in it["line_ids"]]
    for sid in item_ids:
        roles.setdefault(sid, "item")
    return {
        **fields, "stated_item_count": fields["item_count"], "items": items, "validation": validation,
        "roles": roles, "section": {"ids": item_ids, "status": validation["status"], "message": validation["message"]},
        "rows": [row.text for row in _rows(lines)],
    }
