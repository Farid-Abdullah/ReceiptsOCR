"""Render a synthetic thermal-style receipt (PNG) and fake camera feeds (MJPEG) for testing."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "data"

ITEMS = [
    ("4011", "BANANAS", 2, 0.59),
    ("0712", "WHOLE MILK 1GAL", 1, 3.49),
    ("2231", "BREAD WHEAT", 1, 2.99),
    ("4011", "BANANAS", 2, 0.59),
    ("9921", "EGGS LARGE 12CT", 1, 4.29),
    ("5520", "CHEDDAR CHEESE", 1, 5.79),
    ("1180", "APPLE JUICE", 3, 1.99),
    ("3307", "PASTA PENNE", 2, 1.25),
    ("8812", "TOMATO SAUCE", 1, 2.15),
    ("6604", "COFFEE BEANS", 1, 9.99),
]
TAX_RATE = 0.05


def receipt_lines(items=ITEMS, total_override=None):
    lines = [("FRESH MART", "big"), ("123 MAIN STREET", "c"), ("SPRINGFIELD  TEL 555-0142", "c"),
             ("", ""), ("DATE: 10/21/2026   TIME: 14:35", "l"), ("", "")]
    subtotal = 0.0
    for code, name, qty, unit in items:
        total = round(qty * unit, 2)
        subtotal += total
        if qty > 1:
            lines.append((f"{code} {name}", "l"))
            lines.append((f"   {qty} @ {unit:.2f}", "l", f"{total:.2f}"))
        else:
            lines.append((f"{code} {name}", "l", f"{total:.2f}"))
    subtotal = round(subtotal, 2)
    tax = round(subtotal * TAX_RATE, 2)
    total = total_override if total_override is not None else round(subtotal + tax, 2)
    lines += [("", ""), ("SUBTOTAL", "l", f"{subtotal:.2f}"), ("TAX 5%", "l", f"{tax:.2f}"),
              ("TOTAL", "bold", f"{total:.2f}"), ("", ""), ("VISA", "l", f"{total:.2f}"),
              (f"ITEMS SOLD: {sum(i[2] for i in items)}", "l"), ("THANK YOU FOR SHOPPING", "c")]
    return lines, subtotal, tax, total


def render(lines, width=560):
    font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 24)
    bold = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 28)
    big = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 44)
    lh = 38
    img = Image.new("RGB", (width, 80 + lh * (len(lines) + 2)), (246, 244, 238))
    d = ImageDraw.Draw(img)
    y = 50
    for entry in lines:
        text, style = entry[0], entry[1]
        right = entry[2] if len(entry) > 2 else None
        f = big if style == "big" else bold if style == "bold" else font
        ink = (40, 40, 45)
        if style in ("c", "big"):
            w = d.textlength(text, font=f)
            d.text(((width - w) / 2, y), text, font=f, fill=ink)
        else:
            d.text((30, y), text, font=f, fill=ink)
        if right:
            w = d.textlength(right, font=f)
            d.text((width - 30 - w, y), right, font=f, fill=ink)
        y += lh + (24 if style == "big" else 0)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def still_video(receipt, path, frame_w=720, frame_h=1280, frames=30):
    """A phone held still over the receipt (portrait): the whole receipt on a dark table, a little noise."""
    # generous margins: Chrome's fake camera crops the frame's edges to fit the requested aspect
    scale = min((frame_w - 60) / receipt.width, (frame_h * 0.72) / receipt.height)
    r = receipt.resize((int(receipt.width * scale), int(receipt.height * scale)), Image.BICUBIC)
    frame = Image.new("RGB", (frame_w, frame_h), (70, 58, 50))
    frame.paste(r, ((frame_w - r.width) // 2, (frame_h - r.height) // 2))
    with open(path, "wb") as fh:
        for _ in range(frames):
            frame.save(fh, "JPEG", quality=90)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    lines, sub, tax, total = receipt_lines()
    img = render(lines)
    img.save(OUT / "receipt.png")
    for angle in (4, -6):  # tilted copies for the OCR tests
        img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(70, 58, 50)).save(OUT / f"receipt_rot{angle}.png")
    print(f"subtotal={sub} tax={tax} total={total}")
    if "--video" in sys.argv:  # fake camera feeds for the end-to-end test (tests/js/e2e.js)
        still_video(img, OUT / "receipt_still.mjpeg")
        bad, *_ = receipt_lines(total_override=45.51)
        still_video(render(bad), OUT / "receipt_still_bad.mjpeg")
        still_video(img.rotate(4, resample=Image.BICUBIC, expand=True, fillcolor=(70, 58, 50)),
                    OUT / "receipt_still_tilted.mjpeg")
        print("videos written")
