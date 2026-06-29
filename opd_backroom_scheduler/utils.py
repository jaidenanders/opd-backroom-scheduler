import re
from . import constants as _constants

def auto_text_color(hex_bg):
    """Return #000000 or #FFFFFF for best readability on hex_bg."""
    if not hex_bg or len(hex_bg) < 7:
        return "#000000"
    try:
        r = int(hex_bg[1:3], 16)
        g = int(hex_bg[3:5], 16)
        b = int(hex_bg[5:7], 16)
        lum = 0.2126*r + 0.7152*g + 0.0722*b
        return "#000000" if lum > 140 else "#FFFFFF"
    except Exception:
        return "#000000"

def get_coverage_color(n):
    """Return bar color for headcount n using current thresholds."""
    color = _constants._thresholds[0][1]
    for min_n, col in _constants._thresholds:
        if n >= min_n:
            color = col
    return color

# ── SHIFT PARSING ─────────────────────────────────────────────────────────────
def parse_shift(raw):
    if raw is None: return None
    raw = raw.strip().replace(" ", "")
    if raw == "" or raw.upper() in ("NONE", "N/A", "NA") or raw == "-":
        return None
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?[-–](\d{1,2})(?::(\d{2}))?$', raw)
    if not m:
        raise ValueError(f'Unrecognised format "{raw}".\nUse H-H or H:MM-H:MM  e.g. 9-2 or 8:30-2:30')
    sh=int(m.group(1)); sm=int(m.group(2) or 0)
    eh=int(m.group(3)); em=int(m.group(4) or 0)
    if sm > 59 or em > 59: raise ValueError('Minutes must be 00-59')
    sh24 = sh+12 if 1<=sh<=4 else sh
    if sh24 > 23: raise ValueError(f'Start hour {sh} out of range')
    def resolve(eh, em):
        cands = [eh] if eh>=12 else [eh+12, eh]
        for e in cands:
            if e > 23: continue
            d = (e*60+em)-(sh24*60+sm)
            if 60 <= d <= 840: return e
        return None
    eh24 = resolve(eh, em)
    if eh24 is None:
        raise ValueError(f'Could not resolve a valid end from "{raw}".\nCheck end is after start and shift is 1–14h.')
    return sh24, sm, eh24, em, ((eh24*60+em)-(sh24*60+sm))/60

def shift_display(sh24, sm, eh24, em):
    def f(h, m):
        p = "AM" if h < 12 else "PM"; h12 = h%12 or 12
        return f"{h12}:{m:02d} {p}" if m else f"{h12} {p}"
    return f"{f(sh24,sm)} – {f(eh24,em)}"

def shift_sort_key(shift_str):
    try:
        r = parse_shift(shift_str)
        if r: return (0, r[0]*60+r[1])
    except: pass
    return (1, 9999)
