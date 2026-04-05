#!/usr/bin/env python3
"""Generate the inkdav project logo. Run from app/ with: uv run python scripts/generate_logo.py"""

from PIL import Image, ImageDraw, ImageFont

W, H = 512, 512

# RGBA helpers
BLACK  = (0,   0,   0,   255)
WHITE  = (255, 255, 255, 255)
DGREY  = (60,  60,  60,  255)
MGREY  = (140, 140, 140, 255)
LGREY  = (180, 180, 180, 255)
TODAY  = (238, 238, 238, 255)
TRANSP = (0,   0,   0,   0)

# ── Canvas (transparent) ─────────────────────────────────────────────────────
img  = Image.new("RGBA", (W, H), TRANSP)
draw = ImageDraw.Draw(img)

# ── E-ink display device ─────────────────────────────────────────────────────
FX, FY = 44, 32
FW, FH = 424, 296
FR = 18

# Device body (black bezel)
draw.rounded_rectangle([FX, FY, FX + FW, FY + FH], radius=FR, fill=BLACK, outline=BLACK)

# Screen (white inset)
BEZEL = 12
SX = FX + BEZEL
SY = FY + BEZEL
SW = FW - 2 * BEZEL
SH = FH - 2 * BEZEL
draw.rectangle([SX, SY, SX + SW, SY + SH], fill=WHITE)

# ── Week grid inside the screen ───────────────────────────────────────────────
COLS = 7
ROWS = 7

HDR_H = 24
GX = SX + 2
GY = SY + HDR_H + 2
GW = SW - 4
GH = SH - HDR_H - 4

col_w = GW / COLS
row_h = GH / ROWS


def try_font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


SANS_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSText.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
BOLD_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

font_day  = try_font(SANS_PATHS, 12)
font_logo = try_font(BOLD_PATHS, 80)
font_tag  = try_font(SANS_PATHS, 20)

# Day header background
draw.rectangle([SX, SY, SX + SW, SY + HDR_H], fill=BLACK)

day_names = ["S", "M", "T", "W", "T", "F", "S"]
for i, name in enumerate(day_names):
    cx = SX + 2 + (i + 0.5) * col_w
    bb = draw.textbbox((0, 0), name, font=font_day)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    draw.text((cx - tw / 2, SY + (HDR_H - th) / 2 - 1), name, fill=WHITE, font=font_day)

# Today column highlight (Tuesday = col 2)
TODAY_COL = 2
draw.rectangle(
    [GX + TODAY_COL * col_w + 1, GY, GX + (TODAY_COL + 1) * col_w - 1, GY + GH],
    fill=TODAY,
)

# Grid lines
for i in range(COLS + 1):
    x = GX + i * col_w
    lw = 2 if i == TODAY_COL or i == TODAY_COL + 1 else 1
    draw.line([(x, GY), (x, GY + GH)], fill=LGREY, width=lw)
for j in range(ROWS + 1):
    y = GY + j * row_h
    draw.line([(GX, y), (GX + GW, y)], fill=LGREY, width=1)

# Events: (col, row_start, row_span, fill)
events = [
    (1, 0, 2, BLACK),
    (1, 4, 2, BLACK),
    (2, 1, 3, BLACK),
    (3, 0, 1, DGREY),
    (3, 3, 3, DGREY),
    (4, 2, 2, BLACK),
    (5, 0, 4, DGREY),
    (6, 5, 2, BLACK),
]
PAD = 2
for col, row_s, row_n, fill in events:
    ex = GX + col * col_w + PAD
    ey = GY + row_s * row_h + PAD
    ew = col_w - 2 * PAD
    eh = row_n * row_h - 2 * PAD
    draw.rectangle([ex, ey, ex + ew, ey + eh], fill=fill)

# "Now" line at row 2.3 with triangle marker
now_y = GY + 2.3 * row_h
draw.line([(GX, now_y), (GX + GW, now_y)], fill=BLACK, width=2)
draw.polygon([(GX, now_y - 5), (GX, now_y + 5), (GX + 8, now_y)], fill=BLACK)

# ── Typography below the device ───────────────────────────────────────────────
TEXT_Y = FY + FH + 22

logo_text = "inkdav"
bb = draw.textbbox((0, 0), logo_text, font=font_logo)
tw = bb[2] - bb[0]
th = bb[3] - bb[1]
tx = (W - tw) // 2
draw.text((tx, TEXT_Y), logo_text, fill=BLACK, font=font_logo)

tag_text = "caldav  \u00b7  e\u2011ink"
bb2 = draw.textbbox((0, 0), tag_text, font=font_tag)
tw2 = bb2[2] - bb2[0]
tx2 = (W - tw2) // 2
draw.text((tx2, TEXT_Y + th + 10), tag_text, fill=MGREY, font=font_tag)

# ── Save ──────────────────────────────────────────────────────────────────────
out = "static/logo.png"
img.save(out)
print(f"Saved {out}")
