import tkinter as tk
from tkinter import ttk, messagebox
import os, re, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
DAYS        = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL    = {"Mon":"Monday","Tue":"Tuesday","Wed":"Wednesday","Thu":"Thursday",
               "Fri":"Friday","Sat":"Saturday","Sun":"Sunday"}
SHIFT_SUGGESTIONS = ["5-2", "6-3", "7-4", "9-6", "11-8", "1-10", ""]

CLR_HEADER_BLUE  = "#1565C0"
CLR_BLUE_LIGHT   = "#BBDEFB"
CLR_GOLD         = "#FFD54F"
CLR_GOLD_DARK    = "#F9A825"
CLR_BG           = "#F5F5F5"
CLR_WHITE        = "#FFFFFF"
CLR_DARK         = "#1A1F2E"
CLR_MUTED        = "#9AA0B4"
CLR_PERSONAL     = "#E3F2FD"
CLR_BACKROOM     = "#FFF8E1"
CLR_GRAY_ROW     = "#D6D6D6"
CLR_GRAY_TEXT    = "#9E9E9E"
CLR_BACKROOM_BTN = "#1565C0"
CLR_PERSONAL_BTN = "#2E7D32"

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backroom_scheduler.db")

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

# ── COVERAGE THRESHOLDS ───────────────────────────────────────────────────────
# Keys are minimum headcount values; the entry applies when n <= next_key - 1
# Stored as a plain list of (min_count, color) sorted ascending.
# Default: red≤1, orange≤2, yellow≤3, light-green≤4, dark-green=5+
DEFAULT_THRESHOLDS = [
    (0, "#EF5350"),   # red
    (1, "#FF9800"),   # orange
    (2, "#FFF176"),   # yellow
    (3, "#A5D6A7"),   # light green
    (4, "#66BB6A"),   # medium green
    (5, "#2E7D32"),   # dark green  (5+ = great)
]
# Runtime thresholds — modified by settings dialog
_thresholds = [list(t) for t in DEFAULT_THRESHOLDS]

# Backroom capacity cap — modified by settings dialog
_backroom_max = 10

def get_coverage_color(n):
    """Return bar color for headcount n using current thresholds."""
    color = _thresholds[0][1]
    for min_n, col in _thresholds:
        if n >= min_n:
            color = col
    return color

# ── SHIFT PARSING ─────────────────────────────────────────────────────────────
def parse_shift(raw):
    if raw is None: return None
    raw = raw.strip().replace(" ", "")
    if raw == "" or raw.upper() in ("NONE", "N/A", "NA") or raw == "-":
        return None
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?[-\u2013](\d{1,2})(?::(\d{2}))?$', raw)
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

# ── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""CREATE TABLE IF NOT EXISTS associates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        shift TEXT NOT NULL DEFAULT '',
        backroom_pref INTEGER NOT NULL DEFAULT 0,
        color_bg TEXT NOT NULL DEFAULT '',
        color_text TEXT NOT NULL DEFAULT '',
        days_off TEXT NOT NULL DEFAULT '')""")
    try: con.execute("ALTER TABLE associates ADD COLUMN can_exceptions INTEGER NOT NULL DEFAULT 0")
    except: pass
    for col,default in [("color_bg","''"),("color_text","''"),("days_off","''")]:
        try: con.execute(f"ALTER TABLE associates ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
        except: pass
    con.execute("""CREATE TABLE IF NOT EXISTS weekly_schedule (
        associate_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        present INTEGER NOT NULL DEFAULT 0,
        assignment TEXT NOT NULL DEFAULT 'auto',
        today_shift TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (associate_id, day),
        FOREIGN KEY (associate_id) REFERENCES associates(id) ON DELETE CASCADE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS call_outs (
        associate_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (associate_id, day),
        FOREIGN KEY (associate_id) REFERENCES associates(id) ON DELETE CASCADE)""")
    con.commit(); con.close()

_associates_cache = None   # None = stale; invalidated on any write

def _invalidate_cache():
    global _associates_cache
    _associates_cache = None

def get_associates():
    global _associates_cache
    if _associates_cache is not None:
        return _associates_cache
    con = sqlite3.connect(DB_FILE)
    rows = con.execute(
        "SELECT id,name,shift,backroom_pref,color_bg,color_text,days_off,can_exceptions "
        "FROM associates ORDER BY name"
    ).fetchall()
    con.close()
    _associates_cache = rows
    return rows

def add_associate(name, shift, pref, color_bg="", color_text="", days_off="", can_exc=0):
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "INSERT INTO associates(name,shift,backroom_pref,color_bg,color_text,days_off,can_exceptions) "
        "VALUES(?,?,?,?,?,?,?)",
        (name.strip(), shift, pref, color_bg, color_text, days_off, int(can_exc))
    )
    con.commit(); con.close()
    _invalidate_cache()

def update_associate(aid, name, shift, pref, color_bg="", color_text="", days_off="", can_exc=0):
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "UPDATE associates SET name=?,shift=?,backroom_pref=?,color_bg=?,color_text=?,days_off=?,can_exceptions=? "
        "WHERE id=?",
        (name.strip(), shift, pref, color_bg, color_text, days_off, int(can_exc), aid)
    )
    con.commit(); con.close()
    _invalidate_cache()

def delete_associate(aid):
    con = sqlite3.connect(DB_FILE)
    con.execute("DELETE FROM associates WHERE id=?", (aid,))
    con.commit(); con.close()
    _invalidate_cache()

def get_day_state(day):
    """Return {aid: {present,assignment,today_shift}} for a day."""
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("SELECT associate_id,present,assignment,today_shift FROM weekly_schedule WHERE day=?",(day,)).fetchall()
    con.close()
    return {r[0]:{"present":bool(r[1]),"assignment":r[2],"today_shift":r[3]} for r in rows}

def save_day_state(day, aid, present, assignment, today_shift):
    con = sqlite3.connect(DB_FILE)
    con.execute("""INSERT INTO weekly_schedule(associate_id,day,present,assignment,today_shift)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(associate_id,day) DO UPDATE SET
                   present=excluded.present,
                   assignment=excluded.assignment,
                   today_shift=excluded.today_shift""",
                (aid, day, int(present), assignment, today_shift))
    con.commit(); con.close()

def clear_week():
    con = sqlite3.connect(DB_FILE)
    con.execute("DELETE FROM weekly_schedule")
    con.commit(); con.close()

def get_week_summary():
    """Return {day: present_count} for the badge counts."""
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("SELECT day,COUNT(*) FROM weekly_schedule WHERE present=1 GROUP BY day").fetchall()
    con.close()
    return dict(rows)

def get_call_outs(day):
    """Return {aid: status} for a day. status = 'callout'."""
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("SELECT associate_id,status FROM call_outs WHERE day=?",(day,)).fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def set_call_out(aid, day, status):
    """Set or clear a call-out. status='' clears it."""
    con = sqlite3.connect(DB_FILE)
    if status:
        con.execute("""INSERT INTO call_outs(associate_id,day,status) VALUES(?,?,?)
                       ON CONFLICT(associate_id,day) DO UPDATE SET status=excluded.status""",
                    (aid, day, status))
    else:
        con.execute("DELETE FROM call_outs WHERE associate_id=? AND day=?", (aid, day))
    con.commit(); con.close()

def clear_call_outs_for_week():
    con = sqlite3.connect(DB_FILE)
    con.execute("DELETE FROM call_outs")
    con.commit(); con.close()

def reset_associate_attendance(aid):
    """Delete stored present/assignment for this associate across all days
       so auto-logic re-derives from days_off on next view."""
    con = sqlite3.connect(DB_FILE)
    con.execute("DELETE FROM weekly_schedule WHERE associate_id=?", (aid,))
    con.commit(); con.close()

# ── TIME / BREAK HELPERS ──────────────────────────────────────────────────────
def to_dt(h, m=0): return datetime(2000,1,1,h,m)

def fmt_compact(dt):
    h,m = dt.hour, dt.minute
    p = "am" if h<12 else "pm"; h12 = h%12 or 12
    return f"{h12}:{m:02d}{p}" if m else f"{h12}{p}"

def get_break_slots(sh24, sm, eh24, em, dur):
    start = to_dt(sh24,sm); end = to_dt(eh24,em); r={}
    if dur < 4: return r
    r["b1"] = [start+timedelta(hours=2)+timedelta(minutes=20*i) for i in range(3)]
    if dur >= 6:
        opts = [start+timedelta(hours=o) for o in [4,5]
                if start+timedelta(hours=o)+timedelta(hours=1)<=end]
        if opts: r["ln"] = opts
    if dur >= 8:
        r["b2"] = [start+timedelta(hours=7)+timedelta(minutes=20*i) for i in range(3)]
    return r

def build_presence_map(parsed):
    p = defaultdict(int)
    for sh24,sm,eh24,em,_ in parsed:
        t,end = to_dt(sh24,sm),to_dt(eh24,em)
        while t<end: p[t]+=1; t+=timedelta(minutes=20)
    return p

def absence_cost(slot, dur, amap):
    c,t = 0,slot; end=slot+timedelta(minutes=dur)
    while t<end: c+=amap[t]; t+=timedelta(minutes=20)
    return c

def coverage_floor(slot, dur, pmap, amap):
    f,t = 999,slot; end=slot+timedelta(minutes=dur)
    while t<end: f=min(f,pmap[t]-amap[t]-1); t+=timedelta(minutes=20)
    return f

def mark_absent(slot, dur, amap):
    t,end=slot,slot+timedelta(minutes=dur)
    while t<end: amap[t]+=1; t+=timedelta(minutes=20)

def pick_slot(cands, dur, pmap, amap):
    return min(cands, key=lambda s:(-coverage_floor(s,dur,pmap,amap), absence_cost(s,dur,amap), s))

# ── SCHEDULE BUILDER ──────────────────────────────────────────────────────────
def build_schedule(associates):
    """associates: list of (id,name,shift_str,pref,assignment,today_shift)"""
    BACKROOM_MAX = _backroom_max
    parsed=[]
    for a in associates:
        aid,name,shift_str,pref,assignment,today_sh = a
        eff = today_sh if today_sh else shift_str
        try:
            r = parse_shift(eff)
            if r is None: continue
            sh24,sm,eh24,em,dur = r
            parsed.append((aid,name,eff,pref,assignment,sh24,sm,eh24,em,dur))
        except ValueError:
            pass

    def is_personal_only(sh24): return sh24==5
    manual_br  = [p for p in parsed if p[4]=="backroom"]
    manual_ps  = [p for p in parsed if p[4]=="personal"]
    auto_pool  = [p for p in parsed if p[4]=="auto" and not is_personal_only(p[5])]
    forced_ps  = [p for p in parsed if p[4]=="auto" and is_personal_only(p[5])]

    # Manual assignments always honoured — cap only limits auto assignments
    slots_left = max(0, BACKROOM_MAX - len(manual_br))
    auto_s     = sorted(auto_pool, key=lambda p:(-p[3],p[1]))
    br_assocs  = manual_br + auto_s[:slots_left]
    ps_assocs  = manual_ps + forced_ps + auto_s[slots_left:]

    def make_rows(lst):
        ordered = sorted(lst, key=lambda p:(p[5]*60+p[6], p[1]))
        pmap = build_presence_map([(p[5],p[6],p[7],p[8],p[9]) for p in ordered])
        amap = defaultdict(int)
        rows=[]
        for p in ordered:
            aid,name,shift_str,pref = p[0],p[1],p[2],p[3]
            sh24,sm,eh24,em,dur    = p[5],p[6],p[7],p[8],p[9]
            slots = get_break_slots(sh24,sm,eh24,em,dur)
            b1=ln=b2="—"
            if "b1" in slots:
                s=pick_slot(slots["b1"],20,pmap,amap); mark_absent(s,20,amap); b1=fmt_compact(s)
            if "ln" in slots:
                s=pick_slot(slots["ln"],60,pmap,amap); mark_absent(s,60,amap); ln=fmt_compact(s)
            if "b2" in slots:
                s=pick_slot(slots["b2"],20,pmap,amap); mark_absent(s,20,amap); b2=fmt_compact(s)
            rows.append({"id":aid,"name":name,"shift":shift_str,
                         "shift_display":shift_display(sh24,sm,eh24,em),
                         "break1":b1,"lunch":ln,"break2":b2,"pref":pref,"dur":dur})
        return rows
    return make_rows(br_assocs), make_rows(ps_assocs)



def assign_exceptions(ps_rows, assoc_can_exc, required_slots=None):
    """
    Assign exceptions slots to eligible PS associates.
    - Only associates with can_exceptions=True are eligible
    - Only LUNCH blocks an associate (not 20-min breaks)
    - Use as few people as possible (keep same person consecutive hours)
    - If no eligible person can cover a slot due to lunch conflicts, try
      swapping a non-EX associate's lunch to free up coverage
    Returns: {aid: [hour_label, ...]}  (ps_rows may be mutated: lunch field updated)
    """
    from datetime import datetime, timedelta
    import re

    def parse_compact(s):
        if not s or s in ("—", "–", "-"): return None
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", s.lower())
        if not m: return None
        h=int(m.group(1)); mn=int(m.group(2) or 0); p=m.group(3)
        if p=="pm" and h!=12: h+=12
        elif p=="am" and h==12: h=0
        return datetime(2000,1,1,h,mn)

    def parse_disp(disp):
        m = re.match(
            r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[\u2013-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
            disp)
        if not m: return None, None
        def c(h,mn,p):
            h=int(h); mn=int(mn or 0)
            if p=="PM" and h!=12: h+=12
            elif p=="AM" and h==12: h=0
            return datetime(2000,1,1,h,mn)
        return c(m.group(1),m.group(2),m.group(3)), c(m.group(4),m.group(5),m.group(6))

    def fmt_h(dt):
        h12 = dt.hour % 12 or 12
        return f"{h12}{'am' if dt.hour < 12 else 'pm'}"

    def fmt_compact_dt(dt):
        """Format datetime to compact string like 11am or 1:20pm."""
        h12 = dt.hour % 12 or 12
        suf = "am" if dt.hour < 12 else "pm"
        return f"{h12}:{dt.minute:02d}{suf}" if dt.minute else f"{h12}{suf}"

    def on_lunch_during(r, slot):
        """True only if this associate's LUNCH overlaps the hour slot."""
        lt = parse_compact(r.get("lunch", "—"))
        if lt is None: return False
        slot_end = slot + timedelta(hours=1)
        lt_end   = lt  + timedelta(minutes=60)
        return lt < slot_end and lt_end > slot

    def avail_for_slot(candidates, slot):
        """Return candidates working at slot who are NOT on lunch."""
        result = []
        for r in candidates:
            s, e = parse_disp(r["shift_display"])
            if not (s and e and s <= slot < e): continue
            if on_lunch_during(r, slot): continue
            result.append(r)
        return result

    def valid_lunch_times(r):
        """
        Return list of datetime objects that are valid alternative lunch starts
        for this associate (4h+ shift, lunch between h+3 and h+5 from start).
        """
        s, e = parse_disp(r["shift_display"])
        if not s: return []
        dur_h = (e - s).seconds // 3600
        if dur_h < 6: return []   # no lunch for <6h shifts
        opts = []
        # Valid window: 3-5 hours after shift start, at least 1h before end
        for offset in range(3, min(6, dur_h)):
            candidate = s + timedelta(hours=offset)
            if candidate + timedelta(hours=1) <= e:
                opts.append(candidate)
        return opts

    eligible = [r for r in ps_rows if assoc_can_exc.get(r["id"], 0)]
    if not eligible: return {}

    starts = [parse_disp(r["shift_display"])[0] for r in ps_rows]
    ends   = [parse_disp(r["shift_display"])[1] for r in ps_rows]
    starts = [s for s in starts if s]
    ends   = [e for e in ends   if e]
    if not starts or not ends: return {}

    first = min(starts).replace(minute=0)
    last  = max(ends)

    all_slots = []
    t = first
    while t < last:
        all_slots.append(t); t += timedelta(hours=1)

    if required_slots is not None:
        req_set = set(required_slots)
        all_slots = [s for s in all_slots if s in req_set]

    assignments = {}
    last_used   = None

    for slot in all_slots:
        avail = avail_for_slot(eligible, slot)

        # ── No one available — try lunch swaps ──────────────────────
        if not avail:
            swap_candidates = (
                [r for r in ps_rows if not assoc_can_exc.get(r["id"], 0)] +
                [r for r in eligible]
            )
            for donor in swap_candidates:
                s, e = parse_disp(donor["shift_display"])
                if not (s and e and s <= slot < e): continue
                if not on_lunch_during(donor, slot): continue
                alts = [t2 for t2 in valid_lunch_times(donor)
                        if not (t2 < slot + timedelta(hours=1) and
                                t2 + timedelta(hours=1) > slot)]
                if not alts: continue
                old_lunch = donor["lunch"]
                new_ln    = min(alts)
                donor["lunch"] = fmt_compact_dt(new_ln)
                avail = avail_for_slot(eligible, slot)
                if assoc_can_exc.get(donor["id"], 0):
                    avail = [r for r in avail if r["id"] != donor["id"]]
                if avail:
                    break
                else:
                    donor["lunch"] = old_lunch
            if not avail:
                last_used = None   # reset only when truly uncoverable
                continue

        # ── Reset last_used if that person is no longer available ─────
        if last_used and not any(r["id"] == last_used for r in avail):
            last_used = None

        # ── Pick who covers this slot ─────────────────────────────────
        if last_used:
            chosen = next(r for r in avail if r["id"] == last_used)
        else:
            # Sort: most remaining shift hours first, then by name for stability
            def hours_left(r, _slot=slot):
                _, e = parse_disp(r["shift_display"])
                if not e: return 0
                diff = (e - _slot)
                return int(diff.total_seconds()) // 3600
            avail_s = sorted(avail, key=lambda r: (-hours_left(r), r.get("name","")))
            chosen   = avail_s[0]
            last_used = chosen["id"]

        assignments.setdefault(chosen["id"], []).append(fmt_h(slot))

    return assignments


def format_exc_label(hours_list):
    """
    Convert hour list to compact label:
      Consecutive → 'EX 5-2'  (just numbers, no am/pm except on end)
      Single      → 'EX @ 10am'
      Mixed       → 'EX 2pm,5pm'
    """
    import re as _re
    if not hours_list:
        return ""

    def to_h(s):
        m = _re.match(r"(\d{1,2})(am|pm)", s.lower())
        if not m: return None
        h = int(m.group(1))
        if m.group(2) == "pm" and h != 12: h += 12
        elif m.group(2) == "am" and h == 12: h = 0
        return h

    def fmt_h(h24):
        h12 = h24 % 12 or 12
        return f"{h12}{'am' if h24 < 12 else 'pm'}"

    parsed = sorted([(to_h(s), s) for s in hours_list if to_h(s) is not None],
                    key=lambda x: x[0])

    if not parsed:
        return ""
    if len(parsed) == 1:
        return f"EX @ {parsed[0][1]}"

    # Check consecutive
    consecutive = all(parsed[i+1][0] - parsed[i][0] == 1
                      for i in range(len(parsed) - 1))
    if consecutive:
        start_h = parsed[0][0]
        end_h   = parsed[-1][0] + 1          # exclusive end
        # Use plain numbers, only suffix on end hour
        start_n = start_h % 12 or 12
        end_s   = fmt_h(end_h)
        return f"EX {start_n}-{end_s}"
    else:
        # Group consecutive runs, show each as a range
        runs = []
        run_start = parsed[0][0]
        run_end   = parsed[0][0]
        for h, _ in parsed[1:]:
            if h == run_end + 1:
                run_end = h
            else:
                runs.append((run_start, run_end))
                run_start = run_end = h
        runs.append((run_start, run_end))
        parts = []
        for rs, re2 in runs:
            if rs == re2:
                parts.append(fmt_h(rs))
            else:
                parts.append(f"{rs%12 or 12}-{fmt_h(re2+1)}")
        return "EX " + ",".join(parts)


# ── TIMELINE CANVAS ───────────────────────────────────────────────────────────

class TimelineCanvas(tk.Frame):
    """
    Draws a visual timeline of backroom coverage for one day.

    Layout (top to bottom):
      HEADER    — hour labels (5 AM … 10 PM)
      ROWS      — one lane per backroom associate showing working / break / off
      DIVIDER   — thin separator
      COUNT BAR — per-tick headcount bar, color-coded by coverage level
      FLOOR     — text showing minimum coverage and any zero-coverage windows
    """

    DAY_START   = 5    # 5 AM
    DAY_END     = 22   # 10 PM (dept closes)
    LABEL_W     = 120  # px reserved for name labels on left
    TICK_W      = 17   # px per 20-min tick
    HEADER_H    = 20   # hour-label row height
    ROW_H       = 16   # per-associate lane height
    COUNT_H     = 32   # headcount bar height
    PAD_V       = 4    # vertical padding between sections

    # Shift-block colours
    CLR_WORKING  = "#4C6EF5"   # in backroom
    CLR_BREAK    = "#FFD54F"   # on 20-min break
    CLR_LUNCH    = "#FF9800"   # on lunch
    CLR_OFF      = "#E0E0E0"   # before/after shift
    CLR_AXIS     = "#B0BEC5"
    CLR_GRID     = "#ECEFF1"
    CLR_TEXT     = "#37474F"
    CLR_PERSONAL = "#90CAF9"   # personal-shopper lane (dimmed)

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#F5F5F5", **kwargs)
        self._rows      = []   # list of row dicts from build_schedule
        self._ps_rows   = []
        self._canvas    = None
        self._built     = False

    # ── public API ────────────────────────────────────────────────────
    def update_schedule(self, br_rows, ps_rows):
        """Call after generate to refresh the timeline."""
        self._rows    = br_rows
        self._ps_rows = ps_rows
        self._render()

    def clear(self):
        self._rows = []; self._ps_rows = []
        if self._canvas:
            self._canvas.delete("all")
            self._draw_empty()

    # ── internals ─────────────────────────────────────────────────────
    def _total_h(self):
        n = max(len(self._rows), 1)
        return (self.HEADER_H + self.PAD_V
                + n * (self.ROW_H + 1)
                + self.PAD_V + self.COUNT_H + self.PAD_V + 18)

    MAX_VISIBLE_H = 280   # max visible height before scroll kicks in

    def _ensure_canvas(self):
        if hasattr(self, "_scroll_frame") and self._scroll_frame:
            self._scroll_frame.destroy()
        total_ticks = (self.DAY_END - self.DAY_START) * 3
        w = self.LABEL_W + total_ticks * self.TICK_W + 4
        h = self._total_h()

        self._scroll_frame = tk.Frame(self, bg="#F8F9FA")
        self._scroll_frame.pack(fill="x")

        visible_h = min(h, self.MAX_VISIBLE_H)
        self._canvas = tk.Canvas(self._scroll_frame, bg="#F8F9FA",
                                  width=w, height=visible_h,
                                  scrollregion=(0, 0, w, h),
                                  highlightthickness=0)
        vsb = ttk.Scrollbar(self._scroll_frame, orient="vertical",
                             command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        if h > visible_h:
            vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="x", expand=True)

        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all(
            "<MouseWheel>", lambda ev: self._canvas.yview_scroll(
                -1*(ev.delta//120), "units")))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

        self._tip_win = tk.Toplevel(self)
        self._tip_win.withdraw()
        self._tip_win.overrideredirect(True)
        self._tip_win.attributes("-topmost", True)
        self._tip_lbl = tk.Label(self._tip_win, text="", bg="#FFFFCC", fg="#333",
                                  font=("Helvetica", 8), relief="solid", bd=1,
                                  justify="left", padx=4, pady=2)
        self._tip_lbl.pack()
        self._canvas.bind("<Motion>",  self._on_mouse_move)
        self._canvas.bind("<Leave>",   lambda e: self._tip_win.withdraw())

    def _tick_x(self, dt):
        mins = (dt.hour - self.DAY_START) * 60 + dt.minute
        return self.LABEL_W + (mins // 20) * self.TICK_W

    def _parse_compact(self, s):
        import re
        if not s or s == "\u2014": return None
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", s.lower())
        if not m: return None
        h = int(m.group(1)); mins = int(m.group(2) or 0); per = m.group(3)
        if per == "pm" and h != 12: h += 12
        elif per == "am" and h == 12: h = 0
        return datetime(2000, 1, 1, h, mins)

    def _parse_shift_disp(self, s):
        import re
        m = re.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[\u2013-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)", s)
        if not m: return None, None
        def cvt(h, mn, p):
            h = int(h); mn = int(mn or 0)
            if p == "PM" and h != 12: h += 12
            elif p == "AM" and h == 12: h = 0
            return datetime(2000, 1, 1, h, mn)
        return cvt(m.group(1),m.group(2),m.group(3)), cvt(m.group(4),m.group(5),m.group(6))

    def _headcount_color(self, n, max_n):
        return get_coverage_color(n)

    def _bar_label_color(self, n, max_n):
        if n == 0: return "#FFFFFF"
        pct = n / max(max_n, 1)
        return "#FFFFFF" if pct > 0.5 else "#333333"

    def _draw_empty(self):
        if not hasattr(self, "_scroll_frame") or not self._scroll_frame:
            return
        # Just show a placeholder text on a small canvas
        total_ticks = (self.DAY_END - self.DAY_START) * 3
        w = self.LABEL_W + total_ticks * self.TICK_W + 4
        if self._canvas:
            self._canvas.delete("all")
        else:
            self._ensure_canvas()
        self._canvas.create_text(w // 2, 30,
            text="Generate schedule to see backroom coverage",
            font=("Helvetica", 9, "italic"), fill="#9E9E9E")

    def _render(self):
        from datetime import timedelta
        self._ensure_canvas()
        c = self._canvas
        LABEL_W = self.LABEL_W; TICK_W = self.TICK_W
        DAY_START = self.DAY_START; DAY_END = self.DAY_END
        ROW_H = self.ROW_H

        def to_dt(h, m=0): return datetime(2000, 1, 1, h, m)
        def tx(dt):
            mins = (dt.hour - DAY_START) * 60 + dt.minute
            return LABEL_W + (mins // 20) * TICK_W

        total_ticks = (DAY_END - DAY_START) * 3
        canvas_w    = LABEL_W + total_ticks * TICK_W

        # Grid + hour labels
        y_hdr = self.PAD_V
        for h in range(DAY_START, DAY_END + 1):
            x = tx(to_dt(h))
            c.create_line(x, y_hdr, x, self._total_h(), fill=self.CLR_AXIS)
            label = f"{h%12 or 12}{'am' if h<12 else 'pm'}"
            c.create_text(x + 2, y_hdr + self.HEADER_H // 2, text=label,
                          anchor="w", font=("Helvetica", 7), fill=self.CLR_TEXT)
        for i in range(total_ticks):
            x = LABEL_W + i * TICK_W
            c.create_line(x, y_hdr + self.HEADER_H, x, self._total_h(),
                          fill=self.CLR_GRID)

        # Build coverage maps
        ticks_list = []
        t = to_dt(DAY_START)
        while t < to_dt(DAY_END):
            ticks_list.append(t); t += timedelta(minutes=20)

        coverage    = {t: 0 for t in ticks_list}
        tick_names  = {t: [] for t in ticks_list}
        absent_names = {t: [] for t in ticks_list}

        y_row = y_hdr + self.HEADER_H + self.PAD_V

        def draw_row(row, y):
            start_dt, end_dt = self._parse_shift_disp(row["shift_display"])
            if not start_dt: return
            b1_dt = self._parse_compact(row["break1"])
            ln_dt = self._parse_compact(row["lunch"])
            b2_dt = self._parse_compact(row["break2"])
            absent_ranges = []
            if b1_dt: absent_ranges.append((b1_dt, b1_dt+timedelta(minutes=20), self.CLR_BREAK))
            if ln_dt: absent_ranges.append((ln_dt, ln_dt+timedelta(minutes=60), self.CLR_LUNCH))
            if b2_dt: absent_ranges.append((b2_dt, b2_dt+timedelta(minutes=20), self.CLR_BREAK))
            c.create_rectangle(LABEL_W, y, canvas_w, y+ROW_H, fill=self.CLR_OFF, outline="")
            sx = tx(start_dt); ex = tx(end_dt)
            c.create_rectangle(sx, y, ex, y+ROW_H, fill=self.CLR_WORKING, outline="")
            for ab_s, ab_e, ab_col in absent_ranges:
                if ab_s >= to_dt(DAY_START) and ab_e <= to_dt(DAY_END):
                    c.create_rectangle(tx(ab_s), y, tx(ab_e), y+ROW_H, fill=ab_col, outline="")
            absent_set = set()
            for ab_s, ab_e, _ in absent_ranges:
                t2 = ab_s
                while t2 < ab_e:
                    absent_set.add(t2); t2 += timedelta(minutes=20)
            t2 = start_dt
            while t2 < end_dt:
                if t2 in coverage:
                    if t2 in absent_set:
                        absent_names[t2].append(row["name"])
                    else:
                        coverage[t2] += 1
                        tick_names[t2].append(row["name"])
                t2 += timedelta(minutes=20)
            short = row["name"].split()[0][:10]
            c.create_text(LABEL_W-4, y+ROW_H//2, text=short, anchor="e",
                          font=("Helvetica", 7, "bold"), fill=self.CLR_TEXT)

        for row in self._rows:
            draw_row(row, y_row)
            y_row += ROW_H + 1

        # Coverage bar
        y_bar = y_row + self.PAD_V
        max_cov = max(coverage.values()) if any(coverage.values()) else 1
        bar_unit = self.COUNT_H / max(max_cov, 1)
        c.create_rectangle(LABEL_W, y_bar, canvas_w, y_bar+self.COUNT_H,
                           fill="#ECEFF1", outline=self.CLR_AXIS)
        for i, t in enumerate(ticks_list):
            n   = coverage[t]
            col = get_coverage_color(n)
            x1  = LABEL_W + i*TICK_W; x2 = x1 + TICK_W
            bar_top = y_bar + self.COUNT_H - int(n * bar_unit)
            c.create_rectangle(x1, bar_top, x2, y_bar+self.COUNT_H, fill=col, outline="")
            if i % 3 == 0 and n > 0:
                lbl_col = "#FFFFFF" if n >= 3 else "#333333"
                c.create_text(x1+TICK_W*1.5, y_bar+self.COUNT_H-9,
                              text=str(n), font=("Helvetica", 7, "bold"),
                              fill=lbl_col, anchor="center")
        c.create_text(LABEL_W-4, y_bar+self.COUNT_H//2, text="BR\ncount",
                      anchor="e", font=("Helvetica", 7), fill=self.CLR_TEXT, justify="right")

        # Floor annotation
        working_ticks = [t for t in ticks_list if coverage.get(t,0) > 0 or absent_names.get(t)]
        if working_ticks:
            mn = min(coverage.get(t,0) for t in working_ticks)
            fc = "#C62828" if mn == 0 else ("#E65100" if mn == 1 else "#2E7D32")
            c.create_text(LABEL_W, y_bar+self.COUNT_H+3,
                          text=f"Min backroom coverage: {mn}",
                          anchor="w", font=("Helvetica", 8, "bold"), fill=fc)

        self._coverage    = coverage
        self._tick_names  = tick_names
        self._absent_n    = absent_names
        self._ticks_list  = ticks_list

    def _on_mouse_move(self, event):
        x, y = event.x, event.y
        if x < self.LABEL_W: self._tip_win.withdraw(); return
        tick_i = (x - self.LABEL_W) // self.TICK_W
        if not hasattr(self, "_ticks_list") or tick_i >= len(self._ticks_list):
            self._tip_win.withdraw(); return
        t = self._ticks_list[tick_i]
        from datetime import timedelta
        def fmt(dt):
            h12 = dt.hour%12 or 12; p="am" if dt.hour<12 else "pm"
            return f"{h12}:{dt.minute:02d}{p}" if dt.minute else f"{h12}{p}"
        t_end = t + timedelta(minutes=20)
        n = self._coverage.get(t, 0)
        present  = self._tick_names.get(t, [])
        on_break = self._absent_n.get(t, [])
        lines = [f"{fmt(t)} \u2013 {fmt(t_end)}  |  {n} in backroom"]
        if present:  lines.append("Working: " + ", ".join(present))
        if on_break: lines.append("Away: "    + ", ".join(on_break))
        self._tip_lbl.config(text="\n".join(lines))
        rx = self._canvas.winfo_rootx() + x + 14
        ry = self._canvas.winfo_rooty() + y - 28
        self._tip_win.geometry(f"+{rx}+{ry}")
        self._tip_win.deiconify()



# ── PERSONAL SHOPPER TIMELINE ────────────────────────────────────────────────
class PSTimelineCanvas(tk.Frame):
    """
    Headcount bar + per-lane timeline for personal shoppers (pickers).
    """
    DAY_START = 5
    DAY_END   = 22
    LABEL_W   = 80
    TICK_W    = 17
    HEADER_H  = 20
    COUNT_H   = 48
    PAD_V     = 4
    CLR_TEXT    = "#37474F"
    CLR_AXIS    = "#B0BEC5"
    CLR_GRID    = "#ECEFF1"
    CLR_WORKING = "#4C6EF5"
    CLR_BREAK   = "#FFD54F"
    CLR_LUNCH   = "#FF9800"
    CLR_OFF     = "#E0E0E0"

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#F5F5F5", **kwargs)
        self._ps_rows    = []
        self._canvas     = None
        self._scroll_frame = None

    def update_schedule(self, ps_rows):
        self._ps_rows = ps_rows
        self._render()

    def clear(self):
        self._ps_rows = []
        if self._canvas:
            self._canvas.delete("all")
            self._draw_empty()

    def _total_h(self):
        n_rows = max(len(self._ps_rows), 1)
        return (self.HEADER_H + self.PAD_V
                + n_rows * 17        # lane rows (ROW_H=16 + 1 gap)
                + self.PAD_V + self.COUNT_H + self.PAD_V + 18)

    MAX_VISIBLE_H = 280

    def _ensure_canvas(self):
        if hasattr(self, "_scroll_frame") and self._scroll_frame:
            self._scroll_frame.destroy()
        total_ticks = (self.DAY_END - self.DAY_START) * 3
        w = self.LABEL_W + total_ticks * self.TICK_W + 4
        h = self._total_h()

        self._scroll_frame = tk.Frame(self, bg="#F8F9FA")
        self._scroll_frame.pack(fill="x")

        visible_h = min(h, self.MAX_VISIBLE_H)
        self._canvas = tk.Canvas(self._scroll_frame, bg="#F8F9FA",
                                  width=w, height=visible_h,
                                  scrollregion=(0, 0, w, h),
                                  highlightthickness=0)
        vsb = ttk.Scrollbar(self._scroll_frame, orient="vertical",
                             command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        if h > visible_h:
            vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="x", expand=True)

        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all(
            "<MouseWheel>", lambda ev: self._canvas.yview_scroll(
                -1*(ev.delta//120), "units")))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))
        self._tip_win = tk.Toplevel(self)
        self._tip_win.withdraw()
        self._tip_win.overrideredirect(True)
        self._tip_win.attributes("-topmost", True)
        self._tip_lbl = tk.Label(self._tip_win, text="", bg="#FFFFCC", fg="#333",
                                  font=("Helvetica", 8), relief="solid", bd=1,
                                  justify="left", padx=4, pady=2)
        self._tip_lbl.pack()
        self._canvas.bind("<Motion>", self._on_mouse_move)
        self._canvas.bind("<Leave>",  lambda e: self._tip_win.withdraw())

    def _tick_x(self, dt):
        mins = (dt.hour - self.DAY_START) * 60 + dt.minute
        return self.LABEL_W + (mins // 20) * self.TICK_W

    def _parse_compact(self, s):
        import re
        if not s or s == "—": return None
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", s.lower())
        if not m: return None
        h = int(m.group(1)); mins = int(m.group(2) or 0); per = m.group(3)
        if per == "pm" and h != 12: h += 12
        elif per == "am" and h == 12: h = 0
        return datetime(2000, 1, 1, h, mins)

    def _parse_shift_disp(self, s):
        import re
        m = re.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)", s)
        if not m: return None, None
        def cvt(h, mn, p):
            h = int(h); mn = int(mn or 0)
            if p == "PM" and h != 12: h += 12
            elif p == "AM" and h == 12: h = 0
            return datetime(2000, 1, 1, h, mn)
        return cvt(m.group(1),m.group(2),m.group(3)), cvt(m.group(4),m.group(5),m.group(6))

    def _render(self):
        from datetime import datetime, timedelta
        self._ensure_canvas()
        c = self._canvas
        LABEL_W = self.LABEL_W; TICK_W = self.TICK_W
        DAY_START = self.DAY_START; DAY_END = self.DAY_END

        def to_dt(h, m=0): return datetime(2000, 1, 1, h, m)
        def tx(dt):
            mins = (dt.hour - DAY_START) * 60 + dt.minute
            return LABEL_W + (mins // 20) * TICK_W

        total_ticks = (DAY_END - DAY_START) * 3
        canvas_w    = LABEL_W + total_ticks * TICK_W

        # Hour grid + labels
        y_hdr = self.PAD_V
        for h in range(DAY_START, DAY_END + 1):
            x = tx(to_dt(h))
            c.create_line(x, y_hdr, x, self._total_h(), fill=self.CLR_AXIS)
            label = f"{h%12 or 12}{'am' if h<12 else 'pm'}"
            c.create_text(x + 2, y_hdr + self.HEADER_H//2, text=label,
                          anchor="w", font=("Helvetica", 7), fill=self.CLR_TEXT)
        for i in range(total_ticks):
            x = LABEL_W + i * TICK_W
            c.create_line(x, y_hdr + self.HEADER_H, x, self._total_h(),
                          fill=self.CLR_GRID)

        # Build PS coverage and draw lanes
        ticks_list = []
        t = to_dt(DAY_START)
        while t < to_dt(DAY_END):
            ticks_list.append(t); t += timedelta(minutes=20)

        coverage   = {t: 0 for t in ticks_list}
        tick_names = {t: [] for t in ticks_list}
        absent_n   = {t: [] for t in ticks_list}

        CLR_WORKING = "#4C6EF5"
        CLR_BREAK   = "#FFD54F"
        CLR_LUNCH   = "#FF9800"
        CLR_OFF     = "#E0E0E0"
        ROW_H       = 16

        y_row = y_hdr + self.HEADER_H + self.PAD_V

        for row in self._ps_rows:
            start_dt, end_dt = self._parse_shift_disp(row["shift_display"])
            if not start_dt: continue
            b1_dt = self._parse_compact(row["break1"])
            ln_dt = self._parse_compact(row["lunch"])
            b2_dt = self._parse_compact(row["break2"])
            absent_set = set()
            absent_ranges = []
            for bdt, dur, col in [(b1_dt, 20, CLR_BREAK), (ln_dt, 60, CLR_LUNCH), (b2_dt, 20, CLR_BREAK)]:
                if bdt:
                    t2 = bdt
                    while t2 < bdt + timedelta(minutes=dur):
                        absent_set.add(t2); t2 += timedelta(minutes=20)
                    absent_ranges.append((bdt, bdt + timedelta(minutes=dur), col))

            # Draw lane
            c.create_rectangle(LABEL_W, y_row, canvas_w, y_row + ROW_H,
                                fill=CLR_OFF, outline="")
            sx = tx(start_dt); ex = tx(end_dt)
            c.create_rectangle(sx, y_row, ex, y_row + ROW_H, fill=CLR_WORKING, outline="")
            for ab_s, ab_e, ab_col in absent_ranges:
                if ab_s >= to_dt(DAY_START) and ab_e <= to_dt(DAY_END):
                    c.create_rectangle(tx(ab_s), y_row, tx(ab_e), y_row + ROW_H,
                                       fill=ab_col, outline="")
            short = row["name"].split()[0][:10]
            c.create_text(LABEL_W - 4, y_row + ROW_H // 2,
                          text=short, anchor="e",
                          font=("Helvetica", 7, "bold"), fill=self.CLR_TEXT)
            y_row += ROW_H + 1

            t2 = start_dt
            while t2 < end_dt:
                if t2 in coverage:
                    if t2 in absent_set:
                        absent_n[t2].append(row["name"])
                    else:
                        coverage[t2] += 1
                        tick_names[t2].append(row["name"])
                t2 += timedelta(minutes=20)

        # Draw bar
        y_bar   = y_row + self.PAD_V
        max_cov = max(coverage.values()) if any(coverage.values()) else 1
        bar_unit = self.COUNT_H / max(max_cov, 1)

        c.create_rectangle(LABEL_W, y_bar, canvas_w, y_bar + self.COUNT_H,
                            fill="#ECEFF1", outline=self.CLR_AXIS)

        for i, t in enumerate(ticks_list):
            n   = coverage[t]
            col = get_coverage_color(n)
            x1  = LABEL_W + i * TICK_W; x2 = x1 + TICK_W
            bar_top = y_bar + self.COUNT_H - int(n * bar_unit)
            c.create_rectangle(x1, bar_top, x2, y_bar + self.COUNT_H,
                                fill=col, outline="")
            if i % 3 == 0 and n > 0:
                lbl_col = "#FFFFFF" if n >= 3 else "#333333"
                c.create_text(x1 + TICK_W*1.5, y_bar + self.COUNT_H - 9,
                               text=str(n), font=("Helvetica", 7, "bold"),
                               fill=lbl_col, anchor="center")

        c.create_text(LABEL_W - 4, y_bar + self.COUNT_H//2,
                      text="PS\ncount", anchor="e",
                      font=("Helvetica", 7), fill=self.CLR_TEXT, justify="right")

        # Floor annotation
        y_floor = y_bar + self.COUNT_H + 3
        working_ticks = [t for t in ticks_list
                         if coverage.get(t,0) > 0 or len(absent_n.get(t,[])) > 0]
        if working_ticks:
            mn = min(coverage.get(t,0) for t in working_ticks)
            floor_col = "#C62828" if mn == 0 else ("#E65100" if mn == 1 else "#2E7D32")
            c.create_text(LABEL_W, y_floor, text=f"Min picking coverage: {mn}",
                          anchor="w", font=("Helvetica", 8, "bold"), fill=floor_col)

        self._coverage   = coverage
        self._tick_names = tick_names
        self._absent_n   = absent_n
        self._ticks_list = ticks_list

    def _on_mouse_move(self, event):
        x, y = event.x, event.y
        if x < self.LABEL_W: self._tip_win.withdraw(); return
        tick_i = (x - self.LABEL_W) // self.TICK_W
        if not hasattr(self, "_ticks_list") or tick_i >= len(self._ticks_list):
            self._tip_win.withdraw(); return
        t = self._ticks_list[tick_i]
        from datetime import timedelta
        def fmt(dt):
            h12=dt.hour%12 or 12; p="am" if dt.hour<12 else "pm"
            return f"{h12}:{dt.minute:02d}{p}" if dt.minute else f"{h12}{p}"
        t_end = t + timedelta(minutes=20)
        n = self._coverage.get(t, 0)
        present  = self._tick_names.get(t, [])
        on_break = self._absent_n.get(t, [])
        lines = [f"{fmt(t)} – {fmt(t_end)}  |  {n} picking"]
        if present:  lines.append("Picking: "  + ", ".join(present))
        if on_break: lines.append("On break: " + ", ".join(on_break))
        self._tip_lbl.config(text="\n".join(lines))
        rx = self._canvas.winfo_rootx() + x + 14
        ry = self._canvas.winfo_rooty() + y - 28
        self._tip_win.geometry(f"+{rx}+{ry}")
        self._tip_win.deiconify()

    def _draw_empty(self):
        if not self._canvas: return
        total_ticks = (self.DAY_END - self.DAY_START) * 3
        w = self.LABEL_W + total_ticks * self.TICK_W + 4
        self._canvas.config(width=w, height=60)
        self._canvas.create_text(w//2, 30,
            text="Generate schedule to see picking coverage",
            font=("Helvetica", 9, "italic"), fill=CLR_MUTED)

# ── MAIN APP ──────────────────────────────────────────────────────────────────
class BackroomApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPD Backroom Auto-Scheduler")
        self.configure(bg=CLR_BG)
        self.minsize(1200, 720)
        self.resizable(True, True)
        init_db()
        clear_call_outs_for_week()  # start each session with clean call-out state
        self.active_day = "Mon"
        self._day_state  = {}   # day -> {aid: {present,assignment,today_shift}}
        self._day_rows   = {}   # day -> list of row widgets (for inline shift entries)
        self._shift_vars = {}   # (day,aid) -> StringVar for inline shift field
        self._pres_vars  = {}   # (day,aid) -> BooleanVar
        self._generated  = set()  # days that have been generated
        self.day_timelines    = {}  # day -> TimelineCanvas (backroom)
        self.day_ps_timelines = {}  # day -> PSTimelineCanvas (personal)
        self._last_br_rows  = {d: [] for d in DAYS}
        self._last_ps_rows  = {d: [] for d in DAYS}
        self.day_att_inner   = {}  # day -> attendance inner Frame
        self.day_att_canvas  = {}  # day -> attendance Canvas
        self.day_att_outer   = {}  # day -> attendance LabelFrame (row 1)
        self.day_conf_bar    = {}  # day -> confirm bar Frame (row 2)
        self.day_tables_row   = {}  # day -> tables frame
        self.day_tl_row       = {}  # day -> timelines frame
        self.board_break_taps = {}  # day -> break tap state
        self.board_name_taps  = {}  # day -> name tap state
        self._day_hc_var      = {}  # day -> StringVar for headcount label
        self._day_exc_warn_var = {}  # day -> StringVar for exceptions warning
        self._day_exc_warn_lbl = {}  # day -> Label widget for exceptions warning
        self.attendance     = {}   # aid -> {present, assignment, today_shift}
        self._delete_mode   = False
        self._delete_checks = {}
        self._build_ui()
        self._load_day(self.active_day)

    # ── UI SKELETON ───────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top header ───────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR_HEADER_BLUE, padx=20, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="OPD Backroom Auto-Scheduler — Weekly",
                 font=("Helvetica", 16, "bold"),
                 bg=CLR_HEADER_BLUE, fg=CLR_WHITE).pack(side="left")

        right = tk.Frame(hdr, bg=CLR_HEADER_BLUE)
        right.pack(side="right")
        tk.Button(right, text="📅 Clear Day", command=self._clear_day,
                  bg="#37474F", fg=CLR_WHITE, relief="flat", padx=10, pady=5,
                  font=("Helvetica", 9), cursor="hand2").pack(side="left", padx=4)
        tk.Button(right, text="🗑 Clear Week", command=self._clear_week,
                  bg="#455A64", fg=CLR_WHITE, relief="flat", padx=10, pady=5,
                  font=("Helvetica", 9), cursor="hand2").pack(side="left", padx=4)

        # ── Main notebook ─────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook",     background=CLR_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#CFD8DC", foreground=CLR_DARK,
                        padding=[12,5], font=("Helvetica", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", CLR_HEADER_BLUE)],
                  foreground=[("selected", CLR_WHITE)])
        style.configure("Sched.Treeview", font=("Helvetica", 9), rowheight=26,
                        background=CLR_WHITE, fieldbackground=CLR_WHITE)
        style.configure("Sched.Treeview.Heading", font=("Helvetica", 9,"bold"),
                        background=CLR_HEADER_BLUE, foreground=CLR_WHITE)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        # Roster tab
        self.roster_frame = tk.Frame(self.nb, bg=CLR_BG)
        self.nb.add(self.roster_frame, text="  👥 Roster  ")
        self._build_roster_tab()

        # Day tabs
        self.day_frames   = {}
        self.day_tv_br    = {}
        self.day_tv_ps    = {}
        for day in DAYS:
            f = tk.Frame(self.nb, bg=CLR_BG)
            self.nb.add(f, text=f"  {day}  ")
            self.day_frames[day] = f
            self._build_day_tab(f, day)

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # ── ROSTER TAB ────────────────────────────────────────────────────
    def _build_roster_tab(self):
        tab = self.roster_frame
        self._delete_mode   = False
        self._delete_checks = {}

        # ── Top action bar ───────────────────────────────────────────
        bar = tk.Frame(tab, bg=CLR_BG, pady=6)
        bar.pack(fill="x", padx=16, pady=(10, 2))

        tk.Button(bar, text="＋  Add Associate", command=self._open_add_popup,
                  bg=CLR_HEADER_BLUE, fg=CLR_WHITE, relief="flat",
                  padx=14, pady=5, font=("Helvetica", 10, "bold"),
                  cursor="hand2").pack(side="left", padx=(0, 8))

        self._delete_btn = tk.Button(bar, text="🗑  Delete Associates",
                  command=self._toggle_delete_mode,
                  bg="#C62828", fg=CLR_WHITE, relief="flat",
                  padx=14, pady=5, font=("Helvetica", 10, "bold"), cursor="hand2")
        self._delete_btn.pack(side="left", padx=(0, 4))

        self._confirm_btn = tk.Button(bar, text="✓ Confirm Delete",
                  command=self._confirm_delete,
                  bg="#B71C1C", fg=CLR_WHITE, relief="flat",
                  padx=14, pady=5, font=("Helvetica", 10, "bold"), cursor="hand2")
        # shown only in delete mode

        tk.Label(bar, text="Click any field to edit  ·  Color swatches are clickable",
                 bg=CLR_BG, fg=CLR_MUTED, font=("Helvetica", 8, "italic")).pack(
            side="left", padx=14)

        # ── Column headers for Staff editor ───────────────────────────
        hf = tk.Frame(tab, bg=CLR_HEADER_BLUE)
        hf.pack(fill="x", padx=16, pady=(6, 0))
        for txt, w in [("Name", 140), ("Shift", 68), ("Hours", 145),
                        ("Days Off", 210), ("BR Pref", 60), ("EXC", 40), ("Box", 36)]:
            tk.Label(hf, text=txt, bg=CLR_HEADER_BLUE, fg=CLR_WHITE,
                     font=("Helvetica", 8, "bold"), width=max(w//7, 1),
                     anchor="center", pady=4).pack(side="left", padx=1)
        self.day_btns = {}  # kept for _refresh_day_btn_styles compat

        # ── Scrollable canvas ─────────────────────────────────────────
        cf = tk.Frame(tab, bg=CLR_BG)
        cf.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.roster_canvas = tk.Canvas(cf, bg=CLR_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(cf, orient="vertical", command=self.roster_canvas.yview)
        self.roster_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.roster_canvas.pack(side="left", fill="both", expand=True)
        self.roster_inner = tk.Frame(self.roster_canvas, bg=CLR_BG)
        self._cwin = self.roster_canvas.create_window(
            (0, 0), window=self.roster_inner, anchor="nw")
        self.roster_inner.bind("<Configure>",
            lambda e: self.roster_canvas.configure(
                scrollregion=self.roster_canvas.bbox("all")))
        self.roster_canvas.bind("<Configure>",
            lambda e: self.roster_canvas.itemconfig(self._cwin, width=e.width))
        self.roster_canvas.bind("<MouseWheel>",
            lambda e: self.roster_canvas.yview_scroll(-1*(e.delta//120), "units"))
        self.roster_canvas.bind("<Enter>",
            lambda e: self.roster_canvas.bind_all("<MouseWheel>",
                lambda ev: self.roster_canvas.yview_scroll(-1*(ev.delta//120), "units")))
        self.roster_canvas.bind("<Leave>",
            lambda e: self.roster_canvas.unbind_all("<MouseWheel>"))

    def _recompute_exc_warning(self, day):
        """Read current ps_rows and recompute uncovered hours warning."""
        import re as _rw
        from datetime import datetime as _dtw
        ps_rows = self._last_ps_rows.get(day, [])
        covered = set()
        for r in ps_rows:
            exc = r.get("exceptions","—")
            if not exc or exc == "—": continue
            body = exc.replace("EX","").replace("@","").strip()
            for part in body.split(","):
                part = part.strip()
                rng = _rw.match(r"(\d{1,2})(am|pm)?-(\d{1,2})(am|pm)", part.lower())
                if rng:
                    sn=int(rng.group(1));s_per=rng.group(2)
                    en=int(rng.group(3));e_per=rng.group(4)
                    eh=en+(12 if e_per=="pm" and en!=12 else 0)
                    sh=sn+(12 if s_per=="pm" and sn!=12 else 0) if s_per else (sn+(12 if e_per=="pm" and sn<=en and sn!=12 else 0))
                    for h in range(sh,eh): covered.add(h)
                else:
                    sm2=_rw.match(r"(\d{1,2})(am|pm)",part.lower())
                    if sm2:
                        h=int(sm2.group(1))
                        if sm2.group(2)=="pm" and h!=12: h+=12
                        elif sm2.group(2)=="am" and h==12: h=0
                        covered.add(h)
        def _pd(d):
            m=_rw.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",d)
            if not m: return None,None
            def c(h,mn,p):
                h=int(h);mn=int(mn or 0)
                if p=="PM" and h!=12: h+=12
                elif p=="AM" and h==12: h=0
                return _dtw(2000,1,1,h,mn)
            return c(m.group(1),m.group(2),m.group(3)),c(m.group(4),m.group(5),m.group(6))
        sts=[_pd(r["shift_display"])[0] for r in ps_rows]
        ens=[_pd(r["shift_display"])[1] for r in ps_rows]
        sts=[s for s in sts if s]; ens=[e for e in ens if e]
        unc=[]
        if sts and ens:
            for h in range(min(s.hour for s in sts),max(e.hour for e in ens)):
                if h not in covered: unc.append(h)
        self._update_exc_warning(day, unc)

    def _update_exc_warning(self, day, uncovered_hours):
        """Set or clear the red box exceptions warning in the day header."""
        if day not in self._day_exc_warn_var: return
        var = self._day_exc_warn_var[day]
        lbl = self._day_exc_warn_lbl.get(day)

        if not uncovered_hours:
            var.set("")
            if lbl:
                try: lbl.pack_forget()
                except Exception: pass
            return

        def fmt_h24(h):
            h12 = h % 12 or 12
            return f"{h12}{'AM' if h < 12 else 'PM'}"

        runs = []
        rs = re2 = uncovered_hours[0]
        for h in uncovered_hours[1:]:
            if h == re2 + 1: re2 = h
            else: runs.append((rs, re2)); rs = re2 = h
        runs.append((rs, re2))

        parts = [fmt_h24(rs) if rs == re2 else f"{fmt_h24(rs)}-{fmt_h24(re2+1)}"
                 for rs, re2 in runs]
        var.set("⚠ NO EXCEPTIONS AT " + ", ".join(parts))
        if lbl:
            try:
                lbl.pack_forget()
                lbl.pack(side="left", padx=6)
            except Exception: pass

    def _update_day_headcount(self, day):
        """Update Total people = X in the day tab header."""
        if day not in self._day_hc_var: return
        day_state  = get_day_state(day)
        associates = get_associates()
        call_outs  = get_call_outs(day)
        total = 0
        for a in associates:
            aid      = a[0]
            days_off = a[6] if len(a) > 6 else ""
            s = day_state.get(aid, {})
            if "present" in s:
                present = bool(s["present"])
            else:
                present = day not in (days_off.split(",") if days_off else [])
            if present and call_outs.get(aid, "") != "callout":
                total += 1
        self._day_hc_var[day].set(f"Total people = {total}")

    # ── DAY TAB ───────────────────────────────────────────────────────
    def _build_day_tab(self, frame, day):
        frame.grid_rowconfigure(0, weight=0)   # header
        frame.grid_rowconfigure(1, weight=1)   # attendance panel — expands
        frame.grid_rowconfigure(2, weight=0)   # confirm button bar
        frame.grid_rowconfigure(3, weight=1)   # schedule tables — half
        frame.grid_rowconfigure(4, weight=1)   # timelines — half
        frame.grid_columnconfigure(0, weight=1)

        # ── Header ───────────────────────────────────────────────────
        hdr = tk.Frame(frame, bg=CLR_HEADER_BLUE, padx=14, pady=7)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text=f"{DAY_FULL[day]} Schedule",
                 font=("Helvetica", 13, "bold"),
                 bg=CLR_HEADER_BLUE, fg=CLR_WHITE).pack(side="left")
        hc_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=hc_var,
                 font=("Helvetica", 10),
                 bg=CLR_HEADER_BLUE, fg="#90CAF9").pack(side="left", padx=12)
        self._day_hc_var[day] = hc_var
        # Warning label for uncovered exceptions hours
        exc_warn_var = tk.StringVar(value="")
        exc_warn_lbl = tk.Label(hdr, textvariable=exc_warn_var,
                                font=("Helvetica", 9, "bold"),
                                bg="#D32F2F", fg="#000000",
                                padx=8, pady=3, relief="flat")
        exc_warn_lbl.pack(side="left", padx=6)   # visible when text set
        exc_warn_lbl.pack_forget()                 # hidden until needed
        self._day_exc_warn_var[day] = exc_warn_var
        self._day_exc_warn_lbl[day] = exc_warn_lbl
        tk.Button(hdr, text="⚙ Coverage Colors",
                  command=self._open_threshold_settings,
                  bg="#37474F", fg=CLR_WHITE, relief="flat",
                  padx=10, pady=3, font=("Helvetica", 8),
                  cursor="hand2").pack(side="right", padx=4)
        tk.Button(hdr, text="📋 View Board",
                  command=lambda d=day: self._open_board_view(d),
                  bg=CLR_GOLD_DARK, fg=CLR_DARK, relief="flat",
                  padx=10, pady=3, font=("Helvetica", 8, "bold"),
                  cursor="hand2").pack(side="right", padx=4)
        tk.Label(hdr, text="Generated from roster  ·  — = not applicable",
                 font=("Helvetica", 8),
                 bg=CLR_HEADER_BLUE, fg="#90CAF9").pack(side="right")

        # ── Attendance panel (row 1) — who is scheduled today ───────
        att_outer = tk.LabelFrame(frame,
                                   text="  👥  Today's Attendance — check/uncheck · floaters enter shift  ",
                                   bg=CLR_BG, fg=CLR_DARK,
                                   font=("Helvetica", 9, "bold"),
                                   padx=6, pady=4, bd=1, relief="groove")
        att_outer.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 2))
        att_outer.grid_rowconfigure(0, weight=1)
        att_outer.grid_columnconfigure(0, weight=1)

        # Column header for attendance panel
        att_hdr = tk.Frame(att_outer, bg=CLR_HEADER_BLUE)
        att_hdr.pack(fill="x")
        for txt, w in [("✓", 28), ("Name", 160), ("Shift", 70),
                        ("Hours", 155), ("Status", 160)]:
            tk.Label(att_hdr, text=txt, bg=CLR_HEADER_BLUE, fg=CLR_WHITE,
                     font=("Helvetica", 8, "bold"), width=max(w//7,1),
                     anchor="center", pady=3).pack(side="left", padx=1)

        att_canvas = tk.Canvas(att_outer, bg=CLR_BG, highlightthickness=0, height=160)
        att_vsb = ttk.Scrollbar(att_outer, orient="vertical", command=att_canvas.yview)
        att_canvas.configure(yscrollcommand=att_vsb.set)
        att_vsb.pack(side="right", fill="y")
        att_canvas.pack(side="left", fill="both", expand=True)
        att_inner = tk.Frame(att_canvas, bg=CLR_BG)
        att_cwin  = att_canvas.create_window((0,0), window=att_inner, anchor="nw")
        att_inner.bind("<Configure>",
            lambda e, c=att_canvas: c.after_idle(
                lambda: c.configure(scrollregion=c.bbox("all"))))
        att_canvas.bind("<Configure>",
            lambda e, c=att_canvas, cw=att_cwin: c.itemconfig(cw, width=e.width))
        # Bind scroll to this canvas when mouse is over it
        att_canvas.bind("<Enter>",
            lambda e, c=att_canvas: c.bind_all("<MouseWheel>",
                lambda ev, c=c: c.yview_scroll(-1*(ev.delta//120), "units")))
        att_canvas.bind("<Leave>",
            lambda e, c=att_canvas: c.unbind_all("<MouseWheel>"))
        self.day_att_inner[day]  = att_inner
        self.day_att_canvas[day] = att_canvas
        self.day_att_outer[day]  = att_outer

        # ── Confirm bar (row 2) ───────────────────────────────────────
        conf_bar = tk.Frame(frame, bg="#E8EEF6", pady=4)
        conf_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=2)
        self.day_conf_bar[day] = conf_bar
        tk.Label(conf_bar,
                 text="Review attendance above, then confirm to see schedule:",
                 bg="#E8EEF6", fg=CLR_DARK,
                 font=("Helvetica", 9)).pack(side="left", padx=10)
        tk.Button(conf_bar, text="✓  Confirm & Show Schedule",
                  command=lambda d=day: self._confirm_attendance(d),
                  bg="#2E7D32", fg=CLR_WHITE, relief="flat",
                  padx=14, pady=4, font=("Helvetica", 9, "bold"),
                  cursor="hand2").pack(side="left", padx=8)
        tk.Label(conf_bar,
                 text="Double-click schedule row to transfer BR ↔ PS  ·  Right-click to mark call-out",
                 bg="#E8EEF6", fg=CLR_MUTED,
                 font=("Helvetica", 8, "italic")).pack(side="left", padx=10)

        # ── Tables side-by-side (row 3) — hidden until Confirm clicked ──
        tables_row = tk.Frame(frame, bg=CLR_BG)
        tables_row.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 0))
        tables_row.grid_remove()
        tables_row.grid_rowconfigure(0, weight=1)
        tables_row.grid_columnconfigure(0, weight=1)
        tables_row.grid_columnconfigure(1, weight=1)

        cols = ("Associate", "Shift", "Break 1", "Lunch", "Break 2", "Exceptions")

        for col_idx, (label, kind, row_even) in enumerate([
            ("📦 Backroom Crew",     "br", CLR_GOLD),
            ("🛒 Personal Shoppers", "ps", CLR_BLUE_LIGHT),
        ]):
            sec = tk.LabelFrame(tables_row, text=f"  {label}  ",
                                 bg=CLR_BG, fg=CLR_DARK,
                                 font=("Helvetica", 9, "bold"),
                                 padx=4, pady=4, bd=1, relief="groove")
            sec.grid(row=0, column=col_idx, sticky="nsew",
                     padx=(0 if col_idx == 0 else 6, 0))
            sec.grid_rowconfigure(0, weight=1)
            sec.grid_columnconfigure(0, weight=1)

            tv = ttk.Treeview(sec, columns=cols, show="headings",
                              height=1, style="Sched.Treeview")
            for col, w in zip(cols, [130, 110, 72, 72, 72, 130]):
                tv.heading(col, text=col)
                tv.column(col, width=w, anchor="center")

            tv.tag_configure("even",    background=row_even)
            tv.tag_configure("odd",     background=CLR_WHITE)
            tv.tag_configure("pref",    background="#FFF9C4")
            tv.tag_configure("nobreak", background="#F0F0F0",
                              foreground=CLR_MUTED)
            tv.tag_configure("callout", background="#FFCDD2",
                              foreground="#B71C1C")

            sb = ttk.Scrollbar(sec, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=sb.set)
            tv.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")

            tv.bind("<Double-Button-1>",
                    lambda e, d=day, k=kind: self._transfer_associate(d, k))
            tv.bind("<Button-1>",
                    lambda e, t=tv: t.selection_set(t.identify_row(e.y))
                    if t.identify_row(e.y) else None)
            tv.bind("<Button-3>",
                    lambda e, d=day, k=kind, t=tv:
                    self._callout_menu(e, d, k, t))

            if kind == "br":
                self.day_tv_br[day] = tv
            else:
                self.day_tv_ps[day] = tv

        # ── Collapsible side-by-side timelines (row 4) ──────────────
        tl_row = tk.Frame(frame, bg=CLR_BG)
        tl_row.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 6))
        tl_row.grid_remove()   # hidden until Confirm clicked
        # Store refs for show/hide
        self.day_tables_row[day] = tables_row
        self.day_tl_row[day]     = tl_row

        # Collapse toggle bar
        tl_toggle_bar = tk.Frame(tl_row, bg="#CFD8DC", pady=2)
        tl_toggle_bar.pack(fill="x", pady=(0, 2))
        tl_collapsed  = {"v": False}
        tl_toggle_lbl = tk.Label(tl_toggle_bar,
                                  text="▼  Coverage Timelines  (click to collapse)",
                                  bg="#CFD8DC", fg="#37474F",
                                  font=("Helvetica", 8, "bold"), cursor="hand2")
        tl_toggle_lbl.pack(side="left", padx=8)

        # Inner frame that holds both graphs — hidden on collapse
        tl_inner = tk.Frame(tl_row, bg=CLR_BG)
        tl_inner.pack(fill="x")

        def _toggle_tl(e=None, lbl=tl_toggle_lbl,
                       inner=tl_inner, state=tl_collapsed):
            state["v"] = not state["v"]
            if state["v"]:
                inner.pack_forget()
                lbl.config(text="▶  Coverage Timelines  (click to expand)")
            else:
                inner.pack(fill="x")
                lbl.config(text="▼  Coverage Timelines  (click to collapse)")

        tl_toggle_lbl.bind("<Button-1>", _toggle_tl)
        tl_toggle_bar.bind("<Button-1>", _toggle_tl)

        # Backroom timeline (left)
        br_outer = tk.LabelFrame(tl_inner, text="  📊  Backroom Coverage  ",
                                  bg=CLR_BG, fg=CLR_DARK,
                                  font=("Helvetica", 9, "bold"),
                                  padx=4, pady=4, bd=1, relief="groove")
        br_outer.pack(side="left", fill="both", expand=True, padx=(0, 4))
        br_leg = tk.Frame(br_outer, bg=CLR_BG)
        br_leg.pack(anchor="w", padx=4, pady=(0, 2))
        for txt, col in [("Working","#4C6EF5"), ("Break","#FFD54F"),
                          ("Lunch","#FF9800"),  ("Off","#E0E0E0")]:
            tk.Frame(br_leg, bg=col, width=9, height=9,
                     relief="solid", bd=1).pack(side="left", padx=(0,2))
            tk.Label(br_leg, text=txt, bg=CLR_BG, font=("Helvetica",7),
                     fg=CLR_MUTED).pack(side="left", padx=(0,8))
        tk.Label(br_leg,
                 text="Scroll to see all  ·  Hover for details",
                 bg=CLR_BG, fg=CLR_MUTED,
                 font=("Helvetica",7)).pack(side="left", padx=(6,0))
        br_tl = TimelineCanvas(br_outer)
        br_tl.pack(fill="x")
        br_tl._draw_empty()
        self.day_timelines[day] = br_tl

        # Personal Shopper timeline (right)
        ps_outer = tk.LabelFrame(tl_inner, text="  🛒  Picking Coverage  ",
                                  bg=CLR_BG, fg=CLR_DARK,
                                  font=("Helvetica", 9, "bold"),
                                  padx=4, pady=4, bd=1, relief="groove")
        ps_outer.pack(side="left", fill="both", expand=True, padx=(4, 0))
        ps_leg = tk.Frame(ps_outer, bg=CLR_BG)
        ps_leg.pack(anchor="w", padx=4, pady=(0, 2))
        tk.Label(ps_leg,
                 text="Scroll to see all  |  Hover for names",
                 bg=CLR_BG, fg=CLR_MUTED,
                 font=("Helvetica",7)).pack(side="left")
        ps_tl = PSTimelineCanvas(ps_outer)
        ps_tl.pack(fill="x")
        ps_tl._draw_empty()
        self.day_ps_timelines[day] = ps_tl


    # ── ADD POPUP ─────────────────────────────────────────────────────
    def _open_add_popup(self):
        dlg = tk.Toplevel(self)
        dlg.title("Add Associate")
        dlg.configure(bg=CLR_BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="Add New Associate",
                 font=("Helvetica", 13, "bold"),
                 bg=CLR_BG, fg=CLR_DARK, pady=10).pack(fill="x", padx=20)

        form = tk.Frame(dlg, bg=CLR_BG)
        form.pack(fill="x", padx=20, pady=(0, 4))

        def lbl(text, r):
            tk.Label(form, text=text, bg=CLR_BG, fg=CLR_DARK,
                     font=("Helvetica", 10), anchor="w").grid(
                row=r, column=0, sticky="w", pady=5, padx=(0,10))

        lbl("Name:", 0)
        name_v = tk.StringVar()
        tk.Entry(form, textvariable=name_v, width=24, font=("Helvetica",10),
                 relief="solid", bd=1).grid(row=0, column=1, columnspan=3,
                 sticky="ew", pady=5)

        lbl("Shift:", 1)
        shift_v = tk.StringVar(value="")
        prev_v  = tk.StringVar()
        cb = ttk.Combobox(form, textvariable=shift_v,
                          values=SHIFT_SUGGESTIONS, state="normal",
                          width=12, font=("Helvetica",10))
        cb.grid(row=1, column=1, sticky="w", pady=5)
        tk.Label(form, textvariable=prev_v, bg=CLR_BG, fg="#2E7D32",
                 font=("Helvetica",8)).grid(row=1, column=2, columnspan=2,
                 sticky="w", padx=6)
        def upd(*_):
            try:
                r = parse_shift(shift_v.get())
                prev_v.set(f"✓ {shift_display(*r[:4])}  ·  {r[4]:.1f}h" if r else "Floater")
            except: prev_v.set("⚠ invalid" if shift_v.get() else "")
        shift_v.trace_add("write", upd)

        lbl("Backroom Pref:", 2)
        pref_v = tk.BooleanVar()
        tk.Checkbutton(form, variable=pref_v, bg=CLR_BG,
                       activebackground=CLR_BG, cursor="hand2").grid(
            row=2, column=1, sticky="w", pady=5)

        lbl("Days Off:", 3)
        dframe = tk.Frame(form, bg=CLR_BG)
        dframe.grid(row=3, column=1, columnspan=3, sticky="w", pady=5)
        days_vars = {}
        for d in DAYS:
            v = tk.BooleanVar(); days_vars[d] = v
            tk.Checkbutton(dframe, text=d[:3], variable=v, bg=CLR_BG,
                           activebackground=CLR_BG, font=("Helvetica",8),
                           cursor="hand2").pack(side="left", padx=2)

        lbl("Box Color:", 4)
        bg_v = tk.StringVar(value="")
        bg_sw = tk.Frame(form, width=28, height=20, bg="#DDDDDD",
                          relief="solid", bd=1, cursor="hand2")
        bg_sw.grid(row=4, column=1, sticky="w", pady=5)
        bg_sw.pack_propagate(False)
        tk.Label(form, text="click to pick", bg=CLR_BG, fg=CLR_MUTED,
                 font=("Helvetica",8)).grid(row=4, column=2, sticky="w", padx=4)

        tx_v = tk.StringVar(value="")  # auto-computed from box color

        lbl("Can do Exceptions:", 6)
        exc_v = tk.BooleanVar()
        tk.Checkbutton(form, variable=exc_v, bg=CLR_BG,
                       activebackground=CLR_BG, cursor="hand2").grid(
            row=6, column=1, sticky="w", pady=5)
        tk.Label(form, text="Eligible to run backroom exceptions",
                 bg=CLR_BG, fg=CLR_MUTED,
                 font=("Helvetica", 8)).grid(row=6, column=2, columnspan=2,
                 sticky="w", padx=4)

        def pick_bg():
            from tkinter.colorchooser import askcolor
            res = askcolor(color=bg_v.get() or "#FFFFFF", parent=dlg)
            if res and res[1]:
                bg_v.set(res[1]); bg_sw.config(bg=res[1])
                tx_v.set(auto_text_color(res[1]))

        bg_sw.bind("<Button-1>", lambda e: pick_bg())

        def do_add():
            name = name_v.get().strip()
            if not name:
                messagebox.showwarning("Missing name","Enter a name.",parent=dlg); return
            try: parse_shift(shift_v.get().strip())
            except ValueError as ex:
                messagebox.showerror("Invalid shift",str(ex),parent=dlg); return
            days_off = ",".join(d for d in DAYS if days_vars[d].get())
            try:
                add_associate(name, shift_v.get().strip(), int(pref_v.get()),
                              bg_v.get(), auto_text_color(bg_v.get()),
                              days_off, int(exc_v.get()))
                self._load_day()
                dlg.destroy()
            except Exception as ex:
                messagebox.showerror("Error",f"Could not add:\n{ex}",parent=dlg)

        btn_r = tk.Frame(dlg, bg=CLR_BG)
        btn_r.pack(pady=12)
        tk.Button(btn_r, text="Add", command=do_add,
                  bg=CLR_HEADER_BLUE, fg=CLR_WHITE, relief="flat",
                  padx=16, pady=6, font=("Helvetica",10,"bold"),
                  cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_r, text="Cancel", command=dlg.destroy,
                  bg="#607D8B", fg=CLR_WHITE, relief="flat",
                  padx=12, pady=6, font=("Helvetica",9),
                  cursor="hand2").pack(side="left", padx=6)

    # ── DELETE MODE ───────────────────────────────────────────────────
    def _toggle_delete_mode(self):
        self._delete_mode = not self._delete_mode
        self._delete_checks.clear()
        if self._delete_mode:
            self._delete_btn.config(text="✕  Cancel", bg="#607D8B")
            self._confirm_btn.pack(side="left", padx=(0,8),
                                    in_=self._delete_btn.master)
        else:
            self._delete_btn.config(text="🗑  Delete Associates", bg="#C62828")
            self._confirm_btn.pack_forget()
        self._load_day()

    def _confirm_delete(self):
        to_del = [aid for aid,v in self._delete_checks.items() if v.get()]
        if not to_del:
            messagebox.showinfo("Nothing selected",
                                "Check at least one associate.", parent=self); return
        amap  = {a[0]: a[1] for a in get_associates()}
        names = ", ".join(amap.get(a,"?") for a in to_del)
        if not messagebox.askyesno("Confirm Delete",
                                   f"Permanently delete:\n{names}?", parent=self):
            return
        for aid in to_del:
            delete_associate(aid)
            self.attendance.pop(aid, None)
        self._delete_mode = False
        self._delete_checks.clear()
        self._delete_btn.config(text="🗑  Delete Associates", bg="#C62828")
        self._confirm_btn.pack_forget()
        self._load_day()


    # ── DAY SWITCHING ─────────────────────────────────────────────────
    def _switch_day(self, day):
        self.active_day = day
        self._refresh_day_btn_styles()
        self._render_attendance_panel(day)

    def _on_tab_change(self, event):
        idx = self.nb.index("current")
        if idx == 0:
            self._load_day()
            return
        day = DAYS[idx - 1]
        self.active_day = day
        self._refresh_day_btn_styles()
        self._update_day_headcount(day)
        self._render_attendance_panel(day)

    def _refresh_day_btn_styles(self):
        # Count present per day — from DB if saved, else 0
        summary = get_week_summary()
        # Also count auto-present (not yet saved) based on days_off
        associates = get_associates()
        for day, btn in self.day_btns.items():  # day_btns is {} in Staff tab — safe
            saved_cnt = summary.get(day, None)
            if saved_cnt is not None:
                cnt = saved_cnt
            else:
                # Auto-count: present unless day off
                cnt = sum(
                    1 for a in associates
                    if day not in (a[6].split(",") if a[6] else [])
                )
            is_active = day == self.active_day
            bg = CLR_HEADER_BLUE if is_active else ("#C8E6C9" if cnt>0 else "#E0E0E0")
            fg = CLR_WHITE if is_active else (CLR_DARK if cnt>0 else "#888")
            label = f"{day} ({cnt})" if cnt > 0 else day
            btn.config(bg=bg, fg=fg, text=label)
            # Update day tab label — guard against being called before day tabs exist
            tab_idx = DAYS.index(day) + 1
            if self.nb.index("end") > tab_idx:
                gen_mark = " ✓" if day in self._generated else ""
                self.nb.tab(tab_idx, text=f"  {day}{' ('+str(cnt)+')' if cnt>0 else ''}{gen_mark}  ")

    # ── STAFF EDITOR (roster tab) ────────────────────────────────────
    def _load_day(self, day="__staff__"):
        """
        Renders the Staff editor in roster_inner.
        This is always the editing view — no present/absent, no attendance.
        Each row: Name | Shift | Days-Off chips | BR pref | Box/Text colors
        Inline-editable. day arg kept for API compat but ignored.
        """
        for w in self.roster_inner.winfo_children():
            w.destroy()

        associates = get_associates()

        def sort_key(a):
            try:
                r = parse_shift(a[2])
                if r: return (0, r[0]*60+r[1], a[1])
            except: pass
            return (1, 9999, a[1])

        associates = sorted(associates, key=sort_key)

        ROW_BG = CLR_WHITE
        ALT_BG = "#F7F9FC"

        for idx, (aid, name, shift, pref, color_bg, color_text, days_off, can_exc, *_) in enumerate(associates):
            row_bg = ROW_BG if idx % 2 == 0 else ALT_BG

            # Delete-mode checkbox
            if self._delete_mode:
                row = tk.Frame(self.roster_inner, bg=row_bg,
                               highlightbackground="#DDDDDD", highlightthickness=1)
                row.pack(fill="x", pady=1)
                dv = tk.BooleanVar()
                self._delete_checks[aid] = dv
                tk.Checkbutton(row, variable=dv, bg=row_bg,
                               activebackground=row_bg,
                               cursor="hand2").pack(side="left", padx=(6,2))
                tk.Label(row, text=name, bg=row_bg, fg=CLR_DARK,
                         font=("Helvetica",10,"bold"),
                         anchor="w").pack(side="left", padx=4, pady=6)
                continue

            row = tk.Frame(self.roster_inner, bg=row_bg,
                           highlightbackground="#DDDDDD", highlightthickness=1)
            row.pack(fill="x", pady=1)

            # ── Name — inline editable, fixed char-width label ────────
            name_cell = tk.Frame(row, bg=row_bg)
            name_cell.pack(side="left", padx=4, pady=4)

            name_lbl = tk.Label(name_cell, text=name, bg=row_bg, fg=CLR_DARK,
                                font=("Helvetica", 10, "bold"),
                                width=16, anchor="w", cursor="hand2")
            name_lbl.pack(side="left")

            def start_name_edit(e, aid=aid, cell=name_cell,
                                lbl=name_lbl, cur=name):
                lbl.pack_forget()
                ent = tk.Entry(cell, font=("Helvetica",10),
                               relief="solid", bd=1, width=16)
                ent.pack(side="left")
                ent.insert(0, cur); ent.focus_set(); ent.select_range(0,"end")
                done = {"x": False}
                def save(e=None):
                    if done["x"]: return
                    done["x"] = True
                    val = ent.get().strip()
                    ent.destroy(); lbl.pack(side="left")
                    if val and val != cur:
                        self._save_assoc_field(aid, name=val)
                ent.bind("<Return>", save)
                ent.bind("<FocusOut>", save)
                ent.bind("<Escape>", lambda e: (done.update({"x":True}),
                    ent.destroy(), lbl.pack(side="left")))
            name_lbl.bind("<Button-1>", start_name_edit)

            # ── Shift — inline editable ───────────────────────────────
            shift_cell = tk.Frame(row, bg=row_bg)
            shift_cell.pack(side="left", padx=4, pady=4)

            shift_lbl = tk.Label(shift_cell, text=shift or "—", bg=row_bg,
                                  fg="#1565C0", font=("Helvetica", 9, "bold"),
                                  width=6, anchor="center", cursor="hand2")
            shift_lbl.pack(side="left")

            def start_shift_edit(e, aid=aid, cell=shift_cell,
                                  lbl=shift_lbl, cur=shift):
                lbl.pack_forget()
                ent = tk.Entry(cell, font=("Helvetica",9),
                               relief="solid", bd=1, width=7, justify="center")
                ent.pack(side="left")
                ent.insert(0, cur); ent.focus_set(); ent.select_range(0,"end")
                done = {"x": False}
                def save(e=None):
                    if done["x"]: return
                    done["x"] = True
                    val = ent.get().strip()
                    ent.destroy(); lbl.pack(side="left")
                    if val != cur:
                        try:
                            parse_shift(val)
                            self._save_assoc_field(aid, shift=val)
                        except ValueError as ex:
                            messagebox.showerror("Invalid shift",str(ex),parent=self)
                ent.bind("<Return>", save)
                ent.bind("<FocusOut>", save)
                ent.bind("<Escape>", lambda e: (done.update({"x":True}),
                    ent.destroy(), lbl.pack(side="left")))
            shift_lbl.bind("<Button-1>", start_shift_edit)

            # ── Hours label ───────────────────────────────────────────
            try:
                r = parse_shift(shift)
                hrs = shift_display(*r[:4]) if r else "Floater"
            except: hrs = "?"
            tk.Label(row, text=hrs, bg=row_bg, fg=CLR_MUTED,
                     font=("Helvetica", 8), width=15,
                     anchor="center").pack(side="left", padx=2)

            # ── Days-off chips ────────────────────────────────────────
            off_list = days_off.split(",") if days_off else []
            chips = tk.Frame(row, bg=row_bg)
            chips.pack(side="left", padx=4)
            for d in DAYS:
                is_off = d in off_list
                chip = tk.Label(chips, text=d[:2],
                                bg="#EF5350" if is_off else "#E8F5E9",
                                fg="white"   if is_off else "#2E7D32",
                                font=("Helvetica", 7, "bold"),
                                padx=4, pady=1, cursor="hand2")
                chip.pack(side="left", padx=1)
                def on_chip(e, aid=aid, d=d, off=off_list):
                    new = list(off)
                    if d in new: new.remove(d)
                    else: new.append(d)
                    self._save_assoc_field(aid, days_off=",".join(new))
                chip.bind("<Button-1>", on_chip)

            # ── BR pref toggle ────────────────────────────────────────
            pref_lbl = tk.Label(row,
                                text="★ BR" if pref else "PS",
                                bg="#FFF3E0" if pref else "#E3F2FD",
                                fg="#E65100" if pref else "#1565C0",
                                font=("Helvetica", 8, "bold"),
                                padx=6, pady=2, cursor="hand2")
            pref_lbl.pack(side="left", padx=6)
            def on_pref(e, aid=aid, cur=pref):
                self._save_assoc_field(aid, pref=0 if cur else 1)
            pref_lbl.bind("<Button-1>", on_pref)

            # ── Exceptions checkbox ───────────────────────────────────
            exc_var = tk.BooleanVar(value=bool(can_exc))
            exc_frame = tk.Frame(row, bg=row_bg)
            exc_frame.pack(side="left", padx=4)
            tk.Label(exc_frame, text="EXC", bg=row_bg,
                     fg="#7B1FA2" if can_exc else CLR_MUTED,
                     font=("Helvetica", 7, "bold")).pack(side="left")
            tk.Checkbutton(exc_frame, variable=exc_var, bg=row_bg,
                           activebackground=row_bg, cursor="hand2",
                           command=lambda aid=aid, v=exc_var:
                               self._save_assoc_field(aid, can_exc=int(v.get()))
                           ).pack(side="left")

            # ── Box color swatch only — text auto from luminance ─────
            sw = tk.Frame(row, width=22, height=18,
                          bg=color_bg or "#CCCCCC",
                          relief="solid", bd=1, cursor="hand2")
            sw.pack(side="left", padx=2)
            sw.pack_propagate(False)
            sw_lbl = tk.Label(sw, text="B",
                     bg=color_bg or "#CCCCCC",
                     fg=auto_text_color(color_bg) if color_bg else "#888",
                     font=("Helvetica", 6))
            sw_lbl.pack(expand=True)
            def on_swatch(e, aid=aid, cur=color_bg):
                from tkinter.colorchooser import askcolor
                res = askcolor(color=cur or "#FFFFFF", parent=self)
                if res and res[1]:
                    self._save_assoc_field(aid, color_bg=res[1],
                                           color_text=auto_text_color(res[1]))
            sw.bind("<Button-1>", on_swatch)
            sw_lbl.bind("<Button-1>", on_swatch)

        # Update roster tab label
        self.nb.tab(0, text=f"  👥  Staff ({len(associates)})  ")

    # ── ATTENDANCE PANEL (inside each day tab) ───────────────────────
    def _render_attendance_panel(self, day):
        """Renders who is scheduled today into the day tab's attendance panel.
        If the day has already been confirmed, keep the schedule visible instead."""
        if day not in self.day_att_inner: return

        frame = self.day_frames[day]

        if day in self._generated:
            # Day already confirmed — show schedule, hide attendance
            if day in self.day_att_outer:  self.day_att_outer[day].grid_remove()
            if day in self.day_conf_bar:   self.day_conf_bar[day].grid_remove()
            if day in self.day_tables_row: self.day_tables_row[day].grid()
            if day in self.day_tl_row:     self.day_tl_row[day].grid()
            frame.grid_rowconfigure(1, weight=0, minsize=0)
            frame.grid_rowconfigure(2, weight=0, minsize=0)
            frame.grid_rowconfigure(3, weight=1)
            frame.grid_rowconfigure(4, weight=0)
            return

        # Not yet confirmed — show attendance + confirm; hide schedule
        if day in self.day_att_outer:  self.day_att_outer[day].grid()
        if day in self.day_conf_bar:   self.day_conf_bar[day].grid()
        if day in self.day_tables_row: self.day_tables_row[day].grid_remove()
        if day in self.day_tl_row:     self.day_tl_row[day].grid_remove()
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=0)
        frame.grid_rowconfigure(3, weight=0, minsize=0)
        frame.grid_rowconfigure(4, weight=0, minsize=0)

        inner = self.day_att_inner[day]
        for w in inner.winfo_children():
            w.destroy()
        self._pres_vars.clear()
        self._shift_vars.clear()

        associates = get_associates()
        day_state  = get_day_state(day)

        def sort_key(a):
            try:
                r = parse_shift(a[2])
                if r: return (0, r[0]*60+r[1], a[1])
            except: pass
            return (1, 9999, a[1])
        associates = sorted(associates, key=sort_key)

        for aid, name, shift, pref, color_bg, color_text, days_off, can_exc, *_ in associates:
            is_day_off = day in (days_off.split(",") if days_off else [])

            # Determine presence:
            # Auto default = present unless it's their scheduled day off.
            # Stored value is only honoured if the user explicitly overrode
            # the auto default (i.e. they toggled PTO for someone who would
            # otherwise be auto-present, or marked someone in on their day off).
            stored     = day_state.get(aid, {})
            auto_default = not is_day_off
            if "present" in stored and bool(stored["present"]) != auto_default:
                # User made a deliberate override — respect it
                present = bool(stored["present"])
            else:
                # Use the auto default (days_off is the source of truth)
                present = auto_default
                save_day_state(day, aid, present,
                               stored.get("assignment", "auto"),
                               stored.get("today_shift", "") or shift)

            today_sh = stored.get("today_shift", "") or shift

            # Row styling
            if is_day_off and not present:
                row_bg  = CLR_GRAY_ROW; name_fg = CLR_GRAY_TEXT
                status_txt = "Day Off"; status_fg = "#9E9E9E"
            elif not present:
                row_bg  = "#FFF3E0"; name_fg = "#E65100"
                status_txt = "PTO / Not Scheduled"; status_fg = "#E65100"
            else:
                row_bg  = CLR_WHITE; name_fg = CLR_DARK
                status_txt = ""; status_fg = CLR_DARK

            row = tk.Frame(inner, bg=row_bg,
                           highlightbackground="#DDDDDD", highlightthickness=1)
            row.pack(fill="x", pady=1)

            # Scheduled toggle checkbox
            pv = tk.BooleanVar(value=present)
            self._pres_vars[(day, aid)] = pv

            def on_toggle(aid=aid, day=day, var=pv, base=shift):
                new_present = var.get()
                assign = day_state.get(aid, {}).get("assignment", "auto")
                today  = self._shift_vars.get((day, aid))
                ts     = today.get().strip() if today else base
                save_day_state(day, aid, new_present, assign, ts or base)
                self._render_attendance_panel(day)
                self._refresh_day_btn_styles()

            tk.Checkbutton(row, variable=pv, command=on_toggle,
                           bg=row_bg, activebackground=row_bg,
                           cursor="hand2").pack(side="left", padx=(8,4), pady=6)

            # Name
            tk.Label(row, text=name, bg=row_bg, fg=name_fg,
                     font=("Helvetica", 11, "bold"),
                     width=18, anchor="w").pack(side="left", padx=4)

            # Shift — ALL associates get an editable entry for today's shift
            # Pre-filled with their default shift; blank = floater, enter to override
            sv_att = tk.StringVar(value=today_sh)
            self._shift_vars[(day, aid)] = sv_att

            try:
                r   = parse_shift(today_sh)
                hrs = shift_display(*r[:4]) if r else ("Enter shift →" if not shift else "No shift")
            except:
                hrs = "?"

            # Hours label — updates live when shift entry changes
            hrs_var = tk.StringVar(value=hrs)

            def update_hrs(sv=sv_att, hv=hrs_var):
                try:
                    r2 = parse_shift(sv.get().strip())
                    hv.set(shift_display(*r2[:4]) if r2 else "")
                except:
                    hv.set("⚠ invalid" if sv.get().strip() else "")

            sv_att.trace_add("write", lambda *_: update_hrs())

            shift_cell = tk.Frame(row, bg=row_bg)
            shift_cell.pack(side="left", padx=4)

            shift_entry = tk.Entry(shift_cell, textvariable=sv_att,
                                   font=("Helvetica", 9, "bold"),
                                   width=7, relief="solid", bd=1,
                                   justify="center", fg="#1565C0",
                                   bg=row_bg if row_bg != CLR_WHITE else "#F0F7FF")
            shift_entry.pack(side="left")

            def save_shift(e=None, aid=aid, d=day, sv=sv_att, base=shift):
                val = sv.get().strip()
                if not val:
                    val = base  # revert to default if cleared
                try:
                    parse_shift(val)
                    ds = get_day_state(d)
                    s2 = ds.get(aid, {})
                    save_day_state(d, aid,
                                   s2.get("present", True),
                                   s2.get("assignment", "auto"),
                                   val if val != base else "")
                except ValueError:
                    sv.set(base or "")  # revert on invalid

            shift_entry.bind("<Return>",   save_shift)
            shift_entry.bind("<FocusOut>", save_shift)

            tk.Label(row, textvariable=hrs_var, bg=row_bg, fg=CLR_MUTED,
                     font=("Helvetica", 9), width=16,
                     anchor="center").pack(side="left", padx=2)

            # Status label
            if status_txt:
                tk.Label(row, text=status_txt, bg=row_bg, fg=status_fg,
                         font=("Helvetica", 8, "italic")).pack(side="left", padx=8)

        present_count = sum(
            1 for a in associates
            if day_state.get(a[0], {}).get("present",
               not (day in (a[6].split(",") if a[6] else [])))
        )
        total = len(associates)
        self._update_day_headcount(day)
        self._refresh_day_btn_styles()
        self.day_att_canvas[day].update_idletasks()
        self.day_att_canvas[day].configure(scrollregion=self.day_att_canvas[day].bbox("all"))


    def _regenerate_day(self, day):
        """
        Re-run the schedule for a day that has already been confirmed.
        Respects current attendance state, call-outs, and manual assignments.
        Call-outs are moved to Personal Shoppers and a replacement is pulled
        from PS into Backroom to maintain coverage.
        """
        if day not in self._generated:
            # Not yet confirmed — just do a normal confirm
            self._confirm_attendance(day)
            return

        call_outs   = get_call_outs(day)
        day_state   = get_day_state(day)
        associates  = get_associates()

        active = []
        for _row in associates:
            aid      = _row[0]; name = _row[1]
            shift    = _row[2]; pref = _row[3]
            days_off = _row[6] if len(_row) > 6 else ""
            s        = day_state.get(aid, {})

            # Presence
            if "present" in s:
                present = bool(s["present"])
            else:
                present = day not in (days_off.split(",") if days_off else [])
            if not present:
                continue

            status   = call_outs.get(aid, "")
            today_sh = s.get("today_shift", "") or shift
            assign   = s.get("assignment", "auto")

            # Called-out associates go to personal shoppers (lower priority)
            if status == "callout":
                assign = "personal"

            active.append((aid, name, shift, pref, assign, today_sh))

        if not active:
            messagebox.showinfo("No one scheduled",
                                f"No associates scheduled for {DAY_FULL[day]}.",
                                parent=self)
            return

        br_rows, ps_rows = build_schedule(active)
        assoc_can_exc = {a[0]: (a[7] if len(a) > 7 else 0) for a in get_associates()}
        exc_map = assign_exceptions(ps_rows, assoc_can_exc)
        for row in ps_rows:
            row["exceptions"] = format_exc_label(exc_map.get(row["id"], [])) or "—"
        for row in br_rows:
            row["exceptions"] = "—"
        self._last_br_rows[day] = br_rows
        self._last_ps_rows[day] = ps_rows
        self._populate_day(day, br_rows, ps_rows)
        self._refresh_day_btn_styles()

    def _confirm_attendance(self, day):
        """Save attendance, build schedule, then collapse the attendance panel."""
        self._save_current_day()
        associates = get_associates()
        day_state  = get_day_state(day)
        active = []
        for _row in associates:
            aid = _row[0]; name = _row[1]; shift = _row[2]; pref = _row[3]
            days_off = _row[6] if len(_row) > 6 else ""
            s = day_state.get(aid, {})
            if "present" in s:
                present = bool(s["present"])
            else:
                present = day not in (days_off.split(",") if days_off else [])
            if not present: continue
            today_sh = s.get("today_shift","") or shift
            active.append((aid, name, shift, pref, s.get("assignment","auto"), today_sh))
        if not active:
            messagebox.showinfo("No one scheduled",
                                f"No associates are scheduled for {DAY_FULL[day]}.",
                                parent=self)
            return
        br_rows, ps_rows = build_schedule(active)
        assoc_can_exc = {a[0]: (a[7] if len(a) > 7 else 0) for a in get_associates()}
        exc_map = assign_exceptions(ps_rows, assoc_can_exc)
        for row in ps_rows:
            row["exceptions"] = format_exc_label(exc_map.get(row["id"], [])) or "—"
        for row in br_rows:
            row["exceptions"] = "—"
        self._last_br_rows[day] = br_rows
        self._last_ps_rows[day] = ps_rows
        self._populate_day(day, br_rows, ps_rows)
        self._generated.add(day)
        self._refresh_day_btn_styles()
        # Compute and show warning after layout settles
        self.update_idletasks()
        self._recompute_exc_warning(day)

        # Hide attendance + confirm; reveal tables + timelines
        frame = self.day_frames[day]
        if day in self.day_att_outer:  self.day_att_outer[day].grid_remove()
        if day in self.day_conf_bar:   self.day_conf_bar[day].grid_remove()
        if day in self.day_tables_row: self.day_tables_row[day].grid()
        if day in self.day_tl_row:     self.day_tl_row[day].grid()
        frame.grid_rowconfigure(1, weight=0, minsize=0)
        frame.grid_rowconfigure(2, weight=0, minsize=0)
        frame.grid_rowconfigure(3, weight=1)
        frame.grid_rowconfigure(4, weight=0)


    # ── SAVE CURRENT DAY ──────────────────────────────────────────────
    def _save_current_day(self):
        """Persist present state for the active attendance day."""
        day = self.active_day
        if not day: return
        day_state = get_day_state(day)
        for (d, aid), pv in list(self._pres_vars.items()):
            if d != day: continue
            sv = self._shift_vars.get((day, aid))
            assign   = day_state.get(aid, {}).get("assignment", "auto")
            today_sh = sv.get().strip() if sv else ""
            save_day_state(day, aid, pv.get(), assign, today_sh)

    # ── ASSIGNMENT ────────────────────────────────────────────────────
    def _set_assignment(self, aid, day, assignment):
        state  = get_day_state(day)
        curr   = state.get(aid,{}).get("assignment","auto")
        new    = "auto" if curr==assignment else assignment
        sv     = self._shift_vars.get((day,aid))
        today_sh = sv.get().strip() if sv else ""
        pv     = self._pres_vars.get((day,aid))
        present = pv.get() if pv else False
        save_day_state(day,aid,present,new,today_sh)
        self._render_attendance_panel(day)

    # ── TODAY SHIFT PROMPT (floaters) ─────────────────────────────────
    def _prompt_today_shift(self, aid, day):
        dlg = tk.Toplevel(self); dlg.title("Enter Today's Shift")
        dlg.configure(bg=CLR_BG); dlg.resizable(False,False); dlg.grab_set()
        tk.Label(dlg, text=f"No default shift — enter shift for {DAY_FULL[day]}:",
                 bg=CLR_BG, fg=CLR_DARK, font=("Helvetica",10,"bold"),pady=10).pack(padx=20)
        ev = tk.StringVar(); pv2 = tk.StringVar()
        tk.Entry(dlg, textvariable=ev, font=("Helvetica",11),
                 relief="solid", bd=1, width=16).pack(padx=20,pady=6)
        lbl = tk.Label(dlg, textvariable=pv2, bg=CLR_BG, fg="#2E7D32", font=("Helvetica",8))
        lbl.pack(padx=20)
        def upd(*_):
            try:
                r=parse_shift(ev.get())
                if r is None: pv2.set("⚠ Please enter a shift"); lbl.config(fg="#C62828")
                else: pv2.set(f"✓ {shift_display(*r[:4])}  ·  {r[4]:.1f}h"); lbl.config(fg="#2E7D32")
            except ValueError: pv2.set("⚠ Unrecognised" if ev.get() else ""); lbl.config(fg="#C62828")
        ev.trace_add("write",upd)
        res={"shift":""}
        def confirm():
            raw=ev.get().strip()
            try:
                r=parse_shift(raw)
                if r is None: messagebox.showwarning("No shift","Please enter a shift.",parent=dlg); return
                res["shift"]=raw; dlg.destroy()
            except ValueError as e: messagebox.showerror("Invalid shift",str(e),parent=dlg)
        br2=tk.Frame(dlg,bg=CLR_BG); br2.pack(pady=10)
        tk.Button(br2,text="Confirm",command=confirm,bg=CLR_HEADER_BLUE,fg=CLR_WHITE,
                  relief="flat",padx=14,pady=4,font=("Helvetica",9,"bold"),cursor="hand2").pack(side="left",padx=6)
        tk.Button(br2,text="Cancel",command=dlg.destroy,bg="#607D8B",fg=CLR_WHITE,
                  relief="flat",padx=10,pady=4,font=("Helvetica",9),cursor="hand2").pack(side="left",padx=6)
        self.wait_window(dlg)
        if res["shift"]:
            save_day_state(day,aid,True,"auto",res["shift"])
        return res["shift"]

    # ── FORM ACTIONS ──────────────────────────────────────────────────
    def _save_assoc_field(self, aid, **kwargs):
        """Update one or more fields for an associate and reload the roster."""
        # Guard against double-fire (FocusOut + Return firing simultaneously)
        guard_key = (aid, tuple(sorted((k, str(v)) for k,v in kwargs.items())))
        if getattr(self, "_last_save_key", None) == guard_key:
            return
        self._last_save_key = guard_key

        assocs = {a[0]: a for a in get_associates()}
        if aid not in assocs:
            self._last_save_key = None
            return
        a = assocs[aid]
        new_days_off = kwargs.get("days_off", a[6])
        new_shift    = kwargs.get("shift",    a[2])
        update_associate(
            aid,
            kwargs.get("name",       a[1]),
            new_shift,
            kwargs.get("pref",       a[3]),
            kwargs.get("color_bg",   a[4]),
            kwargs.get("color_text", a[5]),
            new_days_off,
        )
        if new_days_off != a[6] or new_shift != a[2]:
            reset_associate_attendance(aid)
        # Defer UI reload 10ms — batches any double-fired events
        self.after(10, self._deferred_roster_reload)

    def _deferred_roster_reload(self):
        """Reload staff view + all attendance panels after a field save."""
        self._last_save_key = None
        self._load_day()
        for day in DAYS:
            if day in self.day_att_inner:
                try:
                    self._render_attendance_panel(day)
                except Exception:
                    pass

    def _validate_shift(self):
        raw = self.shift_var.get().strip()
        try:
            return parse_shift(raw)  # None = valid floater
        except ValueError as e:
            messagebox.showerror("Invalid shift",str(e),parent=self)
            return "ERROR"

    def _pick_color(self, target):
        from tkinter.colorchooser import askcolor
        initial = self.color_bg_var.get() if target=="bg" else self.color_text_var.get()
        result  = askcolor(color=initial or "#FFFFFF", parent=self, title="Pick a color")
        if result and result[1]:
            hex_col = result[1]
            if target == "bg":
                self.color_bg_var.set(hex_col)
                self._color_bg_swatch.config(bg=hex_col)
            else:
                self.color_text_var.set(hex_col)
                self._color_text_swatch.config(bg=hex_col)

    def _transfer_associate(self, day, from_kind):
        """Move selected associate between BR and PS without auto-rebalancing."""
        tv = self.day_tv_br[day] if from_kind == "br" else self.day_tv_ps[day]
        sel = tv.selection()
        if not sel: return
        name = tv.item(sel[0], "values")[0]

        from_list = self._last_br_rows[day] if from_kind=="br" else self._last_ps_rows[day]
        to_kind   = "personal" if from_kind=="br" else "backroom"

        # Strip any call-out badge
        clean = name.replace("⚠ ","").replace("  [CALL-OUT]","").strip()
        match = next((r for r in from_list if r["name"]==clean), None)
        if not match: return
        aid = match["id"]

        # Persist assignment
        day_state = get_day_state(day)
        s = day_state.get(aid, {})
        save_day_state(day, aid, s.get("present", True),
                       to_kind, s.get("today_shift", ""))

        # Move the row directly between cached lists — no build_schedule
        # This preserves breaks and prevents auto-filling
        self._direct_transfer(day, aid, from_kind)

    def _transfer_by_id(self, aid, day, from_kind):
        """Transfer by aid — no auto-rebalancing."""
        to_kind = "personal" if from_kind == "br" else "backroom"
        day_state = get_day_state(day)
        s = day_state.get(aid, {})
        save_day_state(day, aid, s.get("present", True),
                       to_kind, s.get("today_shift", ""))
        self._direct_transfer(day, aid, from_kind)

    def _regen_exceptions(self, day):
        """Fully regenerate exceptions. Updates warning label for uncovered hours."""
        ps_rows   = self._last_ps_rows.get(day, [])
        br_rows   = self._last_br_rows.get(day, [])
        call_outs = get_call_outs(day)

        for r in ps_rows + br_rows:
            r["exceptions"] = "—"

        active_ps = [r for r in ps_rows
                     if call_outs.get(r["id"], "") != "callout"]

        assoc_can_exc = {a[0]: (a[7] if len(a) > 7 else 0)
                         for a in get_associates()}

        exc_map = assign_exceptions(active_ps, assoc_can_exc)

        # Collect which hours ARE covered
        covered_hours = set()
        for r in active_ps:
            hours = exc_map.get(r["id"], [])
            r["exceptions"] = format_exc_label(hours) if hours else "—"
            if hours:
                import re as _re
                for h_str in hours:
                    m = _re.match(r"(\d{1,2})(am|pm)", h_str.lower())
                    if m:
                        h = int(m.group(1))
                        if m.group(2) == "pm" and h != 12: h += 12
                        elif m.group(2) == "am" and h == 12: h = 0
                        covered_hours.add(h)

        # Determine expected hours (all shifts of active PS)
        from datetime import datetime as _dt, timedelta as _td
        import re as _re2
        def _pd(disp):
            m = _re2.match(
                r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
                disp)
            if not m: return None, None
            def c(h,mn,p):
                h=int(h); mn=int(mn or 0)
                if p=="PM" and h!=12: h+=12
                elif p=="AM" and h==12: h=0
                return _dt(2000,1,1,h,mn)
            return c(m.group(1),m.group(2),m.group(3)), c(m.group(4),m.group(5),m.group(6))

        all_starts = [_pd(r["shift_display"])[0] for r in active_ps]
        all_ends   = [_pd(r["shift_display"])[1] for r in active_ps]
        all_starts = [s for s in all_starts if s]
        all_ends   = [e for e in all_ends   if e]

        uncovered = []
        if all_starts and all_ends:
            day_s = min(s.hour for s in all_starts)
            day_e = max(e.hour for e in all_ends)
            for h in range(day_s, day_e):
                if h not in covered_hours:
                    uncovered.append(h)

        # Build warning label
        self._update_exc_warning(day, uncovered)

    def _direct_transfer(self, day, aid, from_kind):
        """Move a row between cached BR/PS lists, re-sort by shift, repopulate."""
        br_rows = list(self._last_br_rows.get(day, []))
        ps_rows = list(self._last_ps_rows.get(day, []))

        if from_kind == "br":
            row = next((r for r in br_rows if r["id"] == aid), None)
            if row:
                br_rows.remove(row)
                row["exceptions"] = "—"   # BR crew never do exceptions
                ps_rows.append(row)
        else:
            row = next((r for r in ps_rows if r["id"] == aid), None)
            if row:
                had_exceptions = row.get("exceptions","—") != "—"
                ps_rows.remove(row)
                row["exceptions"] = "—"
                br_rows.append(row)

        def _shift_sort_key(r):
            try:
                parsed = parse_shift(r.get("shift_display", "") or r.get("shift", ""))
                if parsed: return (parsed[0] * 60 + parsed[1], r.get("name",""))
            except Exception: pass
            disp = r.get("shift_display", "")
            import re as _re
            m = _re.match(r'(\d{1,2})(?::(\d{2}))? (AM|PM)', disp)
            if m:
                h = int(m.group(1)); mn = int(m.group(2) or 0)
                if m.group(3) == "PM" and h != 12: h += 12
                elif m.group(3) == "AM" and h == 12: h = 0
                return (h * 60 + mn, r.get("name", ""))
            return (9999, r.get("name", ""))

        br_rows.sort(key=_shift_sort_key)
        ps_rows.sort(key=_shift_sort_key)

        self._last_br_rows[day] = br_rows
        self._last_ps_rows[day] = ps_rows

        # If the moved person had exceptions (PS→BR), regenerate all exceptions
        # so their hours get covered by someone else
        if from_kind == "ps":
            self._regen_exceptions(day)

        self._populate_day(day, br_rows, ps_rows)

    # ── BOARD VIEW POPUP ──────────────────────────────────────────────
    def _open_board_view(self, day):
        """Full-screen no-scroll board for a vertical touchscreen monitor."""
        br_rows = self._last_br_rows.get(day, [])
        ps_rows = self._last_ps_rows.get(day, [])
        if not br_rows and not ps_rows:
            messagebox.showinfo("No Schedule",
                                "Generate the schedule first before viewing the board.",
                                parent=self)
            return

        # ── Window ───────────────────────────────────────────────────
        win = tk.Toplevel(self)
        win.title(f"OPD Board — {DAY_FULL[day]}")
        BG = "#1A1F2E"
        win.configure(bg=BG)
        win.state("zoomed")
        win.update_idletasks()

        board_state = {"last_co": {}, "alive": True}
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (board_state.update({"alive": False}), win.destroy()))

        # ── Layout constants ─────────────────────────────────────────
        COLS      = ["Name", "Shift", "Break 1", "Lunch", "Break 2"]
        COL_HDR   = ["#1C3A5E", "#1C3A5E", "#BF360C", "#7B1FA2", "#BF360C"]
        COL_W     = [3, 2, 2, 2, 2]
        N_COLS    = len(COLS)
        PANEL_COLS = N_COLS * 2 + 1   # left panel + divider + right panel

        # Persistent tap state — survives board close/reopen
        name_taps  = self.board_name_taps.setdefault(day, {})
        break_taps = self.board_break_taps.setdefault(day, {})

        # ── Fixed header ─────────────────────────────────────────────
        top = tk.Frame(win, bg="#1565C0", height=52)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        tk.Label(top, text="OPD Backroom Board",
                 font=("Helvetica", 18, "bold"),
                 bg="#1565C0", fg="white").pack(side="left", padx=20, pady=8)
        from datetime import date as _date
        today_str = _date.today().strftime("%B %d, %Y")
        tk.Label(top, text=f"{DAY_FULL[day]}  ·  {today_str}",
                 font=("Helvetica", 14),
                 bg="#1565C0", fg="#90CAF9").pack(side="left", padx=4)
        tk.Button(top, text="✕  Close", command=win.destroy,
                  bg="#37474F", fg="white", relief="flat",
                  padx=14, pady=4, font=("Helvetica", 10, "bold"),
                  cursor="hand2").pack(side="right", padx=16, pady=10)
        tk.Button(top, text="🔄  Refresh Board",
                  command=lambda: render(get_call_outs(day)),
                  bg="#1565C0", fg="white", relief="flat",
                  padx=12, pady=4, font=("Helvetica", 9, "bold"),
                  cursor="hand2").pack(side="right", padx=4, pady=10)
        board_exc_var = tk.StringVar(value="")
        board_exc_lbl = tk.Label(top, textvariable=board_exc_var,
                                  font=("Helvetica", 10, "bold"),
                                  bg="#D32F2F", fg="#000000",
                                  padx=10, pady=4)
        if day in self._day_exc_warn_var:
            _cw = self._day_exc_warn_var[day].get()
            if _cw:
                board_exc_var.set(_cw)
                board_exc_lbl.pack(side="left", padx=12, pady=6)

        # ── Fixed legend ─────────────────────────────────────────────
        leg = tk.Frame(win, bg="#263238", height=28)
        leg.pack(fill="x", side="bottom")
        leg.pack_propagate(False)
        for txt, col in [(" 🟡 Break 20min ", "#FFD54F"),
                          (" 🟠 Lunch 60min ", "#FF8A65"),
                          (" — No break ",     "#78909C"),
                          (" Hold break 0.8s to start (green) · Hold 4× to re-stamp ", "#90CAF9"),
                          (" 3 quick taps on name = call-out ", "#EF9A9A")]:
            tk.Label(leg, text=txt, font=("Helvetica", 8),
                     bg="#263238", fg=col).pack(side="left", padx=8, pady=4)

        # ── Body ─────────────────────────────────────────────────────
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, side="top")

        # ── Render function (called on open + every refresh) ─────────
        def render(call_outs):
            import time as _time
            from datetime import datetime
            # Sync board warning with main schedule warning
            if day in self._day_exc_warn_var:
                cur_w = self._day_exc_warn_var[day].get()
                board_exc_var.set(cur_w)
                if cur_w and not board_exc_lbl.winfo_ismapped():
                    board_exc_lbl.pack(side="left", padx=12, pady=6)
                elif not cur_w and board_exc_lbl.winfo_ismapped():
                    board_exc_lbl.pack_forget()

            cur_br = self._last_br_rows.get(day, br_rows)
            cur_ps = self._last_ps_rows.get(day, ps_rows)
            br_vis = [r for r in cur_br if call_outs.get(r["id"], "") != "callout"]
            ps_vis = [r for r in cur_ps if call_outs.get(r["id"], "") != "callout"]

            # Assoc color lookup: id -> (bg, text)  — blank = use defaults
            assoc_colors = {a[0]: (a[4] or "", a[5] or "") for a in get_associates()}

            # Clear body
            for w in body.winfo_children():
                w.destroy()

            # Column configuration
            for ci in range(PANEL_COLS):
                if ci == N_COLS:
                    body.grid_columnconfigure(ci, weight=0, minsize=6)
                else:
                    pci = ci if ci < N_COLS else ci - N_COLS - 1
                    body.grid_columnconfigure(ci, weight=COL_W[pci], uniform="cols")

            # Build flat item list
            items = []
            def add_sec(title, color, rows):
                if not rows: return
                items.append({"type": "sec_hdr", "title": title,
                              "color": color, "count": len(rows)})
                items.append({"type": "col_hdr", "color": color})
                for i in range(0, len(rows), 2):
                    items.append({"type": "pair",
                                  "pair": (rows[i],
                                           rows[i+1] if i+1 < len(rows) else None)})
            add_sec("📦  Backroom Crew",    "#1565C0", br_vis)
            add_sec("🛒  Personal Shoppers","#2E7D32",  ps_vis)

            n = len(items)
            if n == 0: return
            for ri in range(n):
                body.grid_rowconfigure(ri, weight=1, uniform="rows")

            # ── Draw one associate row ────────────────────────────────
            def draw_assoc(row, grid_row, col_offset, row_idx):
                aid = row["id"]
                cust_bg, cust_text = assoc_colors.get(aid, ("", ""))
                row_bg  = cust_bg if cust_bg else ("#263238" if row_idx%2==0 else "#2E3B42")
                name_fg = auto_text_color(cust_bg) if cust_bg else "white"

                # Name cell — triple tap → call-out
                nt  = name_taps.setdefault(aid, {"n": 0, "last": 0.0})
                exc_label = row.get("exceptions", "—")
                exc_suffix = f"  {exc_label}" if exc_label and exc_label != "—" else ""
                nv  = tk.StringVar(value=row["name"] + exc_suffix)
                nl  = tk.Label(body, textvariable=nv,
                               font=("Helvetica", 11, "bold"),
                               bg=row_bg, fg=name_fg,
                               anchor="w", padx=8, cursor="hand2")
                nl.grid(row=grid_row, column=col_offset,
                        sticky="nsew", padx=1, pady=1)

                def _revert_name_color(nt=nt, label_ref={"lbl": None}):
                    """Called after 2s to revert color if no further tap came."""
                    if _time.time() - nt["last"] >= 1.9 and nt["n"] > 0:
                        nt["n"] = 0
                        lbl = label_ref["lbl"]
                        if lbl and lbl.winfo_exists():
                            lbl.config(fg=name_fg)

                def on_name_tap(e, aid=aid, nv=nv, nl=nl,
                                nt=nt, rfg=name_fg, lref={"lbl": None}):
                    lref["lbl"] = nl
                    now = _time.time()
                    if now - nt["last"] > 2.0:
                        nt["n"] = 0
                        if nl.winfo_exists():
                            nl.config(fg=rfg)
                    nt["last"] = now
                    nt["n"] += 1
                    if nt["n"] == 1:
                        nl.config(fg="#FF8A65")
                        nl.after(2100, lambda: _revert_name_color(nt, lref))
                    elif nt["n"] == 2:
                        nl.config(fg="#EF5350")
                        nl.after(2100, lambda: _revert_name_color(nt, lref))
                    elif nt["n"] >= 3:
                        nt["n"] = 0
                        if nl.winfo_exists():
                            nl.config(fg=rfg)
                        self._set_callout(aid, day, "callout", "br")

                nl.bind("<Button-1>", on_name_tap)

                # Shift cell
                tk.Label(body, text=row["shift_display"],
                         font=("Helvetica", 9),
                         bg=row_bg, fg="#78909C",
                         anchor="center").grid(
                    row=grid_row, column=col_offset+1,
                    sticky="nsew", padx=1, pady=1)

                # Break / Lunch cells
                for b_idx, (bkey, bval, bfg, dur) in enumerate([
                    ("break1", row["break1"], "#FFD54F", 20),
                    ("lunch",  row["lunch"],  "#FF8A65", 60),
                    ("break2", row["break2"], "#FFD54F", 20),
                ]):
                    gcol = col_offset + 2 + b_idx

                    if bval == "—":
                        tk.Label(body, text="—",
                                 font=("Helvetica", 11),
                                 bg=row_bg, fg="#546E7A",
                                 anchor="center").grid(
                            row=grid_row, column=gcol,
                            sticky="nsew", padx=1, pady=1)
                        continue

                    # Reuse existing tap state so refresh doesn't reset timers
                    tk_key = (aid, bkey)
                    bt  = break_taps.setdefault(tk_key, {"n": 0, "last": 0.0, "text": bval, "seq": 0})
                    cv  = tk.StringVar(value=bt["text"])
                    # Restore green if already stamped (survives board refresh)
                    lbl_bg = "#1B5E20" if bt["n"] >= 1 else row_bg
                    lbl_fg = "white"   if bt["n"] >= 1 else bfg

                    lbl = tk.Label(body, textvariable=cv,
                                   font=("Helvetica", 13, "bold"),
                                   bg=lbl_bg, fg=lbl_fg,
                                   anchor="center", cursor="hand2")
                    lbl.grid(row=grid_row, column=gcol,
                             sticky="nsew", padx=1, pady=1)

                    # Hold state for press-and-hold detection
                    hold_state = {"job": None}

                    def on_press(e, lbl=lbl, cv=cv, bt=bt,
                                 orig=bval, rbg=row_bg, fg_n=bfg,
                                 hs=hold_state):
                        """Start an 800ms hold timer on mouse/touch press."""
                        def fire():
                            hs["job"] = None
                            now = _time.time()
                            gap = now - bt["last"]

                            if bt["n"] == 0:
                                # First stamp — hold to start
                                bt["n"] = 1
                                bt["last"] = now
                                bt["text"] = datetime.now().strftime("%I:%M %p").lstrip("0")
                                cv.set(f"Started {bt['text']}")
                                if lbl.winfo_exists():
                                    lbl.config(bg="#1B5E20", fg="white")
                            else:
                                # Already stamped — hold 4 times in succession to re-stamp
                                bt["last"] = now
                                if gap > 2.0:
                                    bt["seq"] = 0
                                else:
                                    bt["seq"] = bt.get("seq", 0) + 1
                                    if bt["seq"] >= 3:
                                        bt["seq"] = 0
                                        bt["n"] = 1
                                        bt["text"] = datetime.now().strftime("%I:%M %p").lstrip("0")
                                        cv.set(f"Started {bt['text']}")
                                        if lbl.winfo_exists():
                                            lbl.config(bg="#1B5E20", fg="white")

                        if hs["job"] is not None:
                            try: lbl.after_cancel(hs["job"])
                            except Exception: pass
                        hs["job"] = lbl.after(800, fire)

                    def on_release(e, hs=hold_state):
                        """Cancel the hold if released before 800ms."""
                        if hs["job"] is not None:
                            try: lbl.after_cancel(hs["job"])
                            except Exception: pass
                            hs["job"] = None

                    lbl.bind("<ButtonPress-1>",   on_press)
                    lbl.bind("<ButtonRelease-1>", on_release)

            # ── Render items ─────────────────────────────────────────
            pair_idx = 0
            for ri, item in enumerate(items):
                if item["type"] == "sec_hdr":
                    c = item["color"]
                    f = tk.Frame(body, bg=c)
                    f.grid(row=ri, column=0, columnspan=PANEL_COLS,
                           sticky="nsew", pady=(6, 0))
                    tk.Label(f, text=item["title"],
                             font=("Helvetica", 12, "bold"),
                             bg=c, fg="white", anchor="w", padx=14).pack(
                        side="left", fill="both", expand=True)
                    tk.Label(f, text=f"{item['count']} associates",
                             font=("Helvetica", 10),
                             bg=c, fg="white", anchor="e", padx=14).pack(side="right")

                elif item["type"] == "col_hdr":
                    for po in [0, N_COLS + 1]:
                        for ci, (cn, hbg) in enumerate(zip(COLS, COL_HDR)):
                            tk.Label(body, text=cn,
                                     font=("Helvetica", 9, "bold"),
                                     bg=hbg, fg="white",
                                     anchor="center").grid(
                                row=ri, column=po+ci,
                                sticky="nsew", padx=1, pady=1)
                    tk.Frame(body, bg="#0D1117").grid(
                        row=ri, column=N_COLS, sticky="nsew")

                elif item["type"] == "pair":
                    left, right = item["pair"]
                    tk.Frame(body, bg="#0D1117").grid(
                        row=ri, column=N_COLS,
                        sticky="nsew", padx=1, pady=1)
                    draw_assoc(left,  ri, 0,          pair_idx * 2)
                    if right:
                        draw_assoc(right, ri, N_COLS + 1, pair_idx * 2 + 1)
                    else:
                        tk.Frame(body, bg=BG).grid(
                            row=ri, column=N_COLS + 1,
                            columnspan=N_COLS,
                            sticky="nsew", padx=1, pady=1)
                    pair_idx += 1

        # ── Initial render + auto-refresh ────────────────────────────
        call_outs = get_call_outs(day)
        board_state["last_co"] = dict(call_outs)
        render(call_outs)

        def refresh_check():
            if not board_state["alive"]: return
            cur = get_call_outs(day)
            if cur != board_state["last_co"]:
                board_state["last_co"] = dict(cur)
                render(cur)
            win.after(3000, refresh_check)

        win.after(3000, refresh_check)
    # ── CALL-OUT CONTEXT MENU ────────────────────────────────────────
    def _callout_menu(self, event, day, kind, tv):
        """Right-click on a schedule row → mark or clear call-out."""
        row_id = tv.identify_row(event.y)
        if not row_id: return
        tv.selection_set(row_id)
        name_cell = tv.item(row_id, "values")[0]

        # Strip any existing badge to get clean name
        clean = (name_cell
                 .replace("⚠ ", "").replace("  [CALL-OUT]", "")
                 .strip())

        rows  = self._last_br_rows[day] if kind == "br" else self._last_ps_rows[day]
        match = next((r for r in rows if r["name"] == clean), None)
        if not match: return
        aid = match["id"]

        call_outs = get_call_outs(day)
        current   = call_outs.get(aid, "")

        other_kind = "personal" if kind == "br" else "backroom"
        other_label = "🛒 Move to Personal Shoppers" if kind == "br" else "📦 Move to Backroom"

        menu = tk.Menu(self, tearoff=0)
        if current == "callout":
            menu.add_command(label="✓  Clear call-out",
                             command=lambda: self._set_callout(aid, day, "", kind))
        else:
            menu.add_command(label="⚠  Mark as Call-Out",
                             command=lambda: self._set_callout(aid, day, "callout", kind))
        menu.add_separator()
        menu.add_command(label=other_label,
                         command=lambda: self._transfer_by_id(aid, day, kind))

        # Exceptions management — PS only
        if kind == "ps":
            menu.add_separator()
            ps_rows = self._last_ps_rows.get(day, [])
            row_dict = next((r for r in ps_rows if r["id"] == aid), None)
            cur_exc  = row_dict.get("exceptions", "—") if row_dict else "—"
            if cur_exc and cur_exc != "—":
                menu.add_command(label=f"📋 Exceptions: {cur_exc}  (click to edit)",
                                 command=lambda: self._exceptions_popup(aid, day))
            else:
                menu.add_command(label="📋 Assign Exceptions slot",
                                 command=lambda: self._exceptions_popup(aid, day))

        menu.tk_popup(event.x_root, event.y_root)

    def _exceptions_popup(self, aid, day):
        """Popup to assign exceptions hours — shows full coverage table."""
        import re as _re
        from datetime import datetime as _dt, timedelta as _td

        ps_rows  = self._last_ps_rows.get(day, [])
        row_dict = next((r for r in ps_rows if r["id"] == aid), None)
        if not row_dict: return

        def parse_disp(disp):
            m = _re.match(
                r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[\u2013-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
                disp)
            if not m: return None, None
            def c(h, mn, p):
                h=int(h); mn=int(mn or 0)
                if p=="PM" and h!=12: h+=12
                elif p=="AM" and h==12: h=0
                return _dt(2000,1,1,h,mn)
            return c(m.group(1),m.group(2),m.group(3)), c(m.group(4),m.group(5),m.group(6))

        def fmt_h(dt):
            h12 = dt.hour % 12 or 12
            return f"{h12}{'am' if dt.hour < 12 else 'pm'}"

        def to_h24(s):
            m = _re.match(r"(\d{1,2})(am|pm)", s.lower())
            if not m: return None
            h = int(m.group(1))
            if m.group(2)=="pm" and h!=12: h+=12
            elif m.group(2)=="am" and h==12: h=0
            return h

        def expand_exc(exc_str):
            """Expand 'EX 5-2pm', 'EX @ 10am', 'EX 6-8am,9-11am' to set of h24 ints."""
            hours = set()
            if not exc_str or exc_str == "—": return hours
            body = exc_str.replace("EX","").replace("@","").strip()
            for part in body.split(","):
                part = part.strip()
                # Range: ends with am/pm on the end token only, e.g. "5-2pm" or "6am-8am"
                range_m = _re.match(
                    r"(\d{1,2})(am|pm)?-(\d{1,2})(am|pm)", part.lower())
                if range_m:
                    sn = int(range_m.group(1))
                    s_per = range_m.group(2)   # may be None
                    en = int(range_m.group(3))
                    e_per = range_m.group(4)
                    # Resolve end hour
                    eh = en
                    if e_per == "pm" and en != 12: eh = en + 12
                    elif e_per == "am" and en == 12: eh = 0
                    # Resolve start hour — if no suffix, infer from end
                    if s_per:
                        sh = sn
                        if s_per == "pm" and sn != 12: sh = sn + 12
                        elif s_per == "am" and sn == 12: sh = 0
                    else:
                        # No suffix: assume same period as end, but earlier
                        sh = sn
                        if e_per == "pm" and sn <= en:
                            # e.g. "2-6pm" or "11-12pm" → both pm
                            if sn != 12: sh = sn + 12
                        elif e_per == "pm" and sn > en:
                            # e.g. "5-2pm" → start is am
                            pass   # sh stays as plain int (5 = 5am)
                        elif e_per == "am":
                            pass
                    for hh in range(sh, eh):
                        hours.add(hh)
                else:
                    # Single hour token like "10am"
                    h = to_h24(part)
                    if h is not None: hours.add(h)
            return hours

        # All PS rows sorted by shift start (for coverage table)
        all_ps = sorted(ps_rows, key=lambda r: (
            (parse_disp(r.get("shift_display",""))[0] or _dt(2000,1,1,23,0)).hour,
            r.get("name","")
        ))

        # Build complete hour coverage map across all day
        coverage_map = {}  # h24 -> name (who is assigned)
        for r in all_ps:
            if r.get("exceptions","—") == "—": continue
            for h24 in expand_exc(r["exceptions"]):
                coverage_map[h24] = r["name"]

        # Build hour slots available to this associate
        s_dt, e_dt = parse_disp(row_dict.get("shift_display",""))
        if not s_dt: return
        hour_slots_dt = []
        t = s_dt.replace(minute=0)
        if t < s_dt: t += _td(hours=1)
        while t < e_dt:
            hour_slots_dt.append(t)
            t += _td(hours=1)

        cur_hours_h24 = expand_exc(row_dict.get("exceptions","—"))

        # ── Dialog ────────────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title(f"Exceptions — {row_dict['name']}")
        dlg.configure(bg=CLR_BG)
        dlg.resizable(True, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"Assign Exceptions — {row_dict['name']}",
                 font=("Helvetica",11,"bold"), bg=CLR_BG, fg=CLR_DARK,
                 pady=8).pack(padx=20, anchor="w")

        # Coverage table header
        tbl_frame = tk.LabelFrame(dlg, text="  Current Exceptions Coverage  ",
                                   bg=CLR_BG, fg=CLR_DARK,
                                   font=("Helvetica",9,"bold"),
                                   padx=8, pady=6)
        tbl_frame.pack(fill="x", padx=16, pady=(0,8))

        # Find all hours that need coverage (from first PS shift to last)
        all_starts = [parse_disp(r["shift_display"])[0] for r in ps_rows]
        all_ends   = [parse_disp(r["shift_display"])[1] for r in ps_rows]
        all_starts = [s for s in all_starts if s]
        all_ends   = [e for e in all_ends   if e]
        if all_starts and all_ends:
            day_start_h = min(s.hour for s in all_starts)
            day_end_h   = max(e.hour for e in all_ends)
            t = _dt(2000,1,1,day_start_h,0)
            col = 0
            while t.hour < day_end_h:
                h24  = t.hour
                h_s  = fmt_h(t)
                who  = coverage_map.get(h24, "")
                bg   = "#C8E6C9" if who else "#FFCDD2"
                lbl_txt = h_s + "\n" + (who if who else "Uncovered")
                tk.Label(tbl_frame, text=lbl_txt,
                         bg=bg, fg="#1B5E20" if who else "#B71C1C",
                         font=("Helvetica", 7, "bold"),
                         padx=4, pady=2, relief="solid", bd=1,
                         width=7, anchor="center",
                         wraplength=52).grid(row=0, column=col, padx=2, pady=2)
                col += 1
                t += _td(hours=1)

        # Hour checkboxes for this associate
        tk.Label(dlg, text=f"Select hours for {row_dict['name']}:",
                 font=("Helvetica",9,"bold"), bg=CLR_BG, fg=CLR_DARK).pack(
            padx=16, anchor="w")

        vars_map = {}  # fmt_h string -> BooleanVar
        box_frame = tk.Frame(dlg, bg=CLR_BG)
        box_frame.pack(padx=16, pady=6, fill="x")
        for i, slot_dt in enumerate(hour_slots_dt):
            h24  = slot_dt.hour
            h_s  = fmt_h(slot_dt)
            v    = tk.BooleanVar(value=h24 in cur_hours_h24)
            vars_map[h_s] = (v, h24)
            # Highlight if currently uncovered
            currently_covered = h24 in coverage_map
            cb_fg = "#E65100" if not currently_covered else CLR_DARK
            tk.Checkbutton(box_frame, text=h_s, variable=v,
                           bg=CLR_BG, activebackground=CLR_BG,
                           fg=cb_fg,
                           font=("Helvetica",10,"bold" if not currently_covered else "normal"),
                           cursor="hand2").grid(row=0, column=i, padx=5)

        tk.Label(dlg, text="Orange = currently uncovered hours",
                 bg=CLR_BG, fg="#E65100", font=("Helvetica",7,"italic")).pack(
            padx=16, anchor="w", pady=(0,6))

        def do_save():
            chosen_h24 = sorted(h24 for _, (v, h24) in vars_map.items() if v.get())
            chosen_strs = [fmt_h(_dt(2000,1,1,h,0)) for h in chosen_h24]
            label = format_exc_label(chosen_strs) if chosen_strs else "—"
            row_dict["exceptions"] = label
            self._populate_day(day, self._last_br_rows.get(day,[]),
                                    self._last_ps_rows.get(day,[]))
            self._recompute_exc_warning(day)
            dlg.destroy()

        def do_clear():
            row_dict["exceptions"] = "—"
            self._populate_day(day, self._last_br_rows.get(day,[]),
                                    self._last_ps_rows.get(day,[]))
            self._recompute_exc_warning(day)
            dlg.destroy()

        btn_r = tk.Frame(dlg, bg=CLR_BG)
        btn_r.pack(pady=10)
        tk.Button(btn_r, text="Save", command=do_save,
                  bg=CLR_HEADER_BLUE, fg=CLR_WHITE, relief="flat",
                  padx=14, pady=5, font=("Helvetica",9,"bold"),
                  cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_r, text="Clear EX", command=do_clear,
                  bg="#607D8B", fg=CLR_WHITE, relief="flat",
                  padx=10, pady=5, font=("Helvetica",9),
                  cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_r, text="Cancel", command=dlg.destroy,
                  bg="#E0E0E0", fg="#555", relief="flat",
                  padx=10, pady=5, font=("Helvetica",9),
                  cursor="hand2").pack(side="left", padx=6)

    def _set_callout(self, aid, day, status, kind="br"):
        """Flag a call-out. Board hides them. Always fully regenerate exceptions."""
        set_call_out(aid, day, status)
        # Full exceptions regen — covers ALL hours across ALL active PS associates
        # so nothing falls through even when multiple call-outs stack
        self._regen_exceptions(day)
        self._populate_day(day,
                           self._last_br_rows.get(day, []),
                           self._last_ps_rows.get(day, []))

    # ── COVERAGE THRESHOLD SETTINGS ──────────────────────────────────
    def _open_threshold_settings(self):
        """Dialog to configure coverage colors and backroom cap."""
        global _thresholds, _backroom_max

        dlg = tk.Toplevel(self)
        dlg.title("Schedule Settings")
        dlg.configure(bg=CLR_BG)
        dlg.resizable(True, True)
        dlg.minsize(420, 480)
        dlg.grab_set()

        # ── Title ────────────────────────────────────────────────────
        tk.Label(dlg, text="Schedule Settings",
                 font=("Helvetica", 13, "bold"), bg=CLR_BG, fg=CLR_DARK,
                 pady=10).pack(fill="x", padx=20)

        # ── Backroom cap ─────────────────────────────────────────────
        cap_frame = tk.LabelFrame(dlg, text="  Backroom Capacity  ",
                                   bg=CLR_BG, fg=CLR_DARK,
                                   font=("Helvetica", 9, "bold"),
                                   padx=12, pady=10)
        cap_frame.pack(fill="x", padx=20, pady=(0, 12))
        tk.Label(cap_frame, text="Max associates in backroom at once:",
                 bg=CLR_BG, fg=CLR_DARK, font=("Helvetica", 10)).grid(
                 row=0, column=0, sticky="w")
        cap_var = tk.IntVar(value=_backroom_max)
        tk.Spinbox(cap_frame, from_=1, to=30, textvariable=cap_var,
                   width=6, font=("Helvetica", 11), relief="solid",
                   justify="center").grid(row=0, column=1, padx=12, sticky="w")
        tk.Label(cap_frame,
                 text="Associates beyond the cap auto-move to Personal Shoppers.",
                 bg=CLR_BG, fg=CLR_MUTED, font=("Helvetica", 8)).grid(
                 row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # ── Color thresholds ─────────────────────────────────────────
        thresh_outer = tk.LabelFrame(dlg, text="  Coverage Bar Colors  ",
                                      bg=CLR_BG, fg=CLR_DARK,
                                      font=("Helvetica", 9, "bold"),
                                      padx=12, pady=8)
        thresh_outer.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        tk.Label(thresh_outer,
                 text="Each tier applies when headcount >= Min Count.",
                 bg=CLR_BG, fg=CLR_MUTED, font=("Helvetica", 8)).pack(
                 anchor="w", pady=(0, 6))

        # Column headers
        hdr_frame = tk.Frame(thresh_outer, bg=CLR_BG)
        hdr_frame.pack(fill="x")
        for txt, w in [("Min Count >=", 10), ("Color", 13), ("Preview", 8)]:
            tk.Label(hdr_frame, text=txt, bg=CLR_HEADER_BLUE, fg=CLR_WHITE,
                     font=("Helvetica", 9, "bold"), width=w,
                     pady=4, relief="flat").pack(side="left", padx=1)

        COLOR_HEX = {
            "Red":         "#EF5350",
            "Orange":      "#FF9800",
            "Yellow":      "#FFF176",
            "Light Green": "#A5D6A7",
            "Med Green":   "#66BB6A",
            "Dark Green":  "#2E7D32",
        }
        COLOR_NAMES = list(COLOR_HEX.keys())

        def hex_to_name(h):
            for name, val in COLOR_HEX.items():
                if val.upper() == h.upper():
                    return name
            return "Dark Green"

        # One frame per threshold row — packed vertically, NOT in a grid
        row_vars = []

        def build_threshold_rows(container):
            # Clear existing
            for w in container.winfo_children():
                w.destroy()
            row_vars.clear()
            for i, (mn, col) in enumerate(_thresholds):
                row_bg = CLR_WHITE if i % 2 == 0 else "#F0F4F8"
                rf = tk.Frame(container, bg=row_bg)
                rf.pack(fill="x", pady=1)

                count_var = tk.IntVar(value=mn)
                color_var = tk.StringVar(value=hex_to_name(col))

                tk.Spinbox(rf, from_=0, to=20, textvariable=count_var,
                           width=8, font=("Helvetica", 10), relief="solid",
                           justify="center", bg=row_bg).pack(
                           side="left", padx=12, pady=4)

                cb = ttk.Combobox(rf, textvariable=color_var,
                                   values=COLOR_NAMES, state="readonly",
                                   width=13, font=("Helvetica", 10))
                cb.pack(side="left", padx=8, pady=4)

                swatch = tk.Frame(rf, width=44, height=22,
                                   bg=COLOR_HEX.get(hex_to_name(col), col),
                                   relief="solid", bd=1)
                swatch.pack(side="left", padx=8, pady=4)
                swatch.pack_propagate(False)

                def on_color_change(*_, cv=color_var, sw=swatch):
                    sw.config(bg=COLOR_HEX.get(cv.get(), "#888"))
                color_var.trace_add("write", on_color_change)

                row_vars.append((count_var, color_var))

        rows_container = tk.Frame(thresh_outer, bg=CLR_BG)
        rows_container.pack(fill="both", expand=True)
        build_threshold_rows(rows_container)

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = tk.Frame(dlg, bg=CLR_BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 14))

        def apply_settings():
            global _thresholds, _backroom_max
            old_cap = _backroom_max
            _backroom_max = cap_var.get()
            new_thresh = []
            for count_var, color_var in row_vars:
                new_thresh.append([count_var.get(),
                                    COLOR_HEX.get(color_var.get(), "#888")])
            new_thresh.sort(key=lambda x: x[0])
            _thresholds = new_thresh
            for day in DAYS:
                if day in self._generated:
                    self.day_timelines[day]._render()
                    self.day_ps_timelines[day]._render()
                    # If cap changed, re-run build_schedule so auto assignments rebalance
                    if _backroom_max != old_cap:
                        day_state  = get_day_state(day)
                        assocs     = get_associates()
                        active = [
                            (a_id, n, sh, pf,
                             day_state.get(a_id,{}).get("assignment","auto"),
                             day_state.get(a_id,{}).get("today_shift",""))
                            for a_id, n, sh, pf, *_ in assocs
                            if day_state.get(a_id,{}).get("present")
                        ]
                        br_rows, ps_rows = build_schedule(active)
                        self._last_br_rows[day] = br_rows
                        self._last_ps_rows[day] = ps_rows
                        self._populate_day(day, br_rows, ps_rows)
            dlg.destroy()

        def reset_defaults():
            global _thresholds, _backroom_max
            _thresholds   = [list(t) for t in DEFAULT_THRESHOLDS]
            _backroom_max = 10
            cap_var.set(10)
            build_threshold_rows(rows_container)
            for day in DAYS:
                if day in self._generated:
                    self.day_timelines[day]._render()
                    self.day_ps_timelines[day]._render()

        tk.Button(btn_row, text="Apply", command=apply_settings,
                  bg=CLR_HEADER_BLUE, fg=CLR_WHITE, relief="flat",
                  padx=16, pady=6, font=("Helvetica", 10, "bold"),
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Reset Defaults", command=reset_defaults,
                  bg="#607D8B", fg=CLR_WHITE, relief="flat",
                  padx=12, pady=6, font=("Helvetica", 9),
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Cancel", command=dlg.destroy,
                  bg="#E0E0E0", fg="#555", relief="flat",
                  padx=12, pady=6, font=("Helvetica", 9),
                  cursor="hand2").pack(side="left")

    def _populate_day(self, day, br_rows, ps_rows):
        """Fill both treeviews and schedule deferred timeline redraw."""
        call_outs   = get_call_outs(day)
        callout_ids = {aid for aid, st in call_outs.items() if st == "callout"}

        def fill_tv(tv, rows):
            tv.tag_configure("callout",      background="#FFCDD2", foreground="#B71C1C")
            tv.tag_configure("callout_pref", background="#FFCDD2", foreground="#B71C1C")
            for r in tv.get_children(): tv.delete(r)
            for i, row in enumerate(rows):
                aid    = row["id"]
                status = call_outs.get(aid, "")
                no_br  = row["break1"] == "\u2014" and row["lunch"] == "\u2014"
                if status == "callout":
                    tag  = "callout_pref" if row["pref"] else "callout"
                    name = f"\u26a0 {row['name']}  [CALL-OUT]"
                else:
                    tag  = ("nobreak" if no_br
                            else ("pref" if row["pref"]
                                  else ("even" if i % 2 == 0 else "odd")))
                    name = row["name"]
                exc  = row.get("exceptions", "—")
                tv.insert("", "end",
                          values=(name, row["shift_display"],
                                  row["break1"], row["lunch"], row["break2"],
                                  exc),
                          tags=(tag,))

        fill_tv(self.day_tv_br[day], br_rows)
        fill_tv(self.day_tv_ps[day], ps_rows)

        br_active = [r for r in br_rows if r["id"] not in callout_ids]
        ps_active = [r for r in ps_rows if r["id"] not in callout_ids]

        self._update_day_headcount(day)

        # Defer timeline redraws — cancel any pending then reschedule 50ms out
        # so rapid back-to-back calls collapse into one canvas draw
        if not hasattr(self, "_tl_after_ids"):
            self._tl_after_ids = {}
        for key, cb in [
            (f"br_{day}", lambda d=day, br=br_active, ps=ps_active:
                self.day_timelines[d].update_schedule(br, ps)),
            (f"ps_{day}", lambda d=day, ps=ps_active:
                self.day_ps_timelines[d].update_schedule(ps)),
        ]:
            if key in self._tl_after_ids:
                try: self.after_cancel(self._tl_after_ids[key])
                except: pass
            self._tl_after_ids[key] = self.after(50, cb)

    def _generate_week(self):
        self._save_current_day()
        associates = get_associates()
        any_present = False

        for day in DAYS:
            day_state = get_day_state(day)
            active = []
            for _row in associates:
                aid      = _row[0]; name     = _row[1]
                shift    = _row[2]; pref     = _row[3]
                days_off = _row[6] if len(_row) > 6 else ""
                s = day_state.get(aid, {})

                # Presence: use stored value if set, otherwise auto from days_off
                if "present" in s:
                    present = bool(s["present"])
                else:
                    is_day_off = day in (days_off.split(",") if days_off else [])
                    present    = not is_day_off
                    save_day_state(day, aid, present,
                                   s.get("assignment", "auto"),
                                   s.get("today_shift", "") or shift)

                if not present:
                    continue

                any_present = True
                today_sh = s.get("today_shift", "") or shift
                active.append((aid, name, shift, pref,
                               s.get("assignment", "auto"), today_sh))

            if not active:
                for tv in (self.day_tv_br[day], self.day_tv_ps[day]):
                    for r in tv.get_children(): tv.delete(r)
                continue

            br_rows, ps_rows = build_schedule(active)
            self._last_br_rows[day] = br_rows
            self._last_ps_rows[day] = ps_rows
            self._populate_day(day, br_rows, ps_rows)
            self._generated.add(day)

        if not any_present:
            messagebox.showinfo("Nothing to generate",
                                "No associates are scheduled for any day.\n"
                                "Check that associates have shifts assigned and "
                                "are not marked off for every day.",
                                parent=self)
            return

        self._refresh_day_btn_styles()
        first = next((d for d in DAYS
                      if any(v.get("present")
                             for v in get_day_state(d).values())), "Mon")
        self.nb.select(DAYS.index(first) + 1)
        messagebox.showinfo("Week Generated ✓",
                            "Schedule generated for all days.\n"
                            "Click each day tab to review.", parent=self)
    def _clear_day(self):
        """Clear attendance and schedule for the currently active day only."""
        day = self.active_day
        if day not in DAYS:
            messagebox.showinfo("No day selected",
                                "Switch to a day tab first, then click Clear Day.",
                                parent=self)
            return
        if not messagebox.askyesno(
            "Clear Day",
            f"Clear attendance and schedule for {DAY_FULL[day]}?",
            parent=self
        ):
            return

        # Clear DB state for this day
        con = sqlite3.connect(DB_FILE)
        con.execute("DELETE FROM weekly_schedule WHERE day=?", (day,))
        con.execute("DELETE FROM call_outs WHERE day=?", (day,))
        con.commit(); con.close()

        # Clear in-memory state
        self._pres_vars   = {k: v for k, v in self._pres_vars.items()   if k[0] != day}
        self._shift_vars  = {k: v for k, v in self._shift_vars.items()  if k[0] != day}
        self._generated.discard(day)
        self._last_br_rows[day] = []
        self._last_ps_rows[day] = []
        self.board_break_taps.pop(day, None)
        self.board_name_taps.pop(day, None)

        # Clear treeviews and timelines for this day
        for tv in (self.day_tv_br[day], self.day_tv_ps[day]):
            for r in tv.get_children(): tv.delete(r)
        self.day_timelines[day].clear()
        self.day_ps_timelines[day].clear()
        self.nb.tab(DAYS.index(day) + 1, text=f"  {day}  ")

        # Reset day tab layout back to attendance view
        frame = self.day_frames[day]
        if day in self.day_att_outer:  self.day_att_outer[day].grid()
        if day in self.day_conf_bar:   self.day_conf_bar[day].grid()
        if day in self.day_tables_row: self.day_tables_row[day].grid_remove()
        if day in self.day_tl_row:     self.day_tl_row[day].grid_remove()
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=0)
        frame.grid_rowconfigure(3, weight=0, minsize=0)
        frame.grid_rowconfigure(4, weight=0, minsize=0)

        self._refresh_day_btn_styles()
        # Re-render the attendance panel for this day
        self.after(50, lambda: self._render_attendance_panel(day))

    def _clear_week(self):
        if messagebox.askyesno("Clear Week",
                               "Clear all attendance and assignments for the entire week?",
                               parent=self):
            clear_week()
            clear_call_outs_for_week()
            self._pres_vars.clear()
            self._shift_vars.clear()
            self._generated.clear()
            self.board_break_taps.clear()
            self.board_name_taps.clear()
            for day in DAYS:
                for tv in (self.day_tv_br[day], self.day_tv_ps[day]):
                    for r in tv.get_children(): tv.delete(r)
                self.day_timelines[day].clear()
                self.day_ps_timelines[day].clear()
                self.nb.tab(DAYS.index(day)+1, text=f"  {day}  ")
                # Hide schedule tables + timelines
                if day in self.day_tables_row:
                    self.day_tables_row[day].grid_remove()
                if day in self.day_tl_row:
                    self.day_tl_row[day].grid_remove()
                # Reset row weights so attendance panel fills space
                frame = self.day_frames[day]
                frame.grid_rowconfigure(1, weight=1)
                frame.grid_rowconfigure(2, weight=0)
                frame.grid_rowconfigure(3, weight=0, minsize=0)
                frame.grid_rowconfigure(4, weight=0, minsize=0)
            self._refresh_day_btn_styles()
            self._load_day()
            # Navigate to Monday and fully re-render its attendance panel
            self.active_day = "Mon"
            self.nb.select(1)
            # after(100) ensures tab geometry is fully settled before rendering
            self.after(100, lambda: self._render_attendance_panel("Mon"))


if __name__ == "__main__":
    app = BackroomApp()
    app.mainloop()
