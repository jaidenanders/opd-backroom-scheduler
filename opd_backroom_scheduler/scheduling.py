import re
from datetime import datetime, timedelta
from collections import defaultdict
from . import constants as _constants
from .utils import parse_shift, shift_display

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
    BACKROOM_MAX = _constants._backroom_max
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
            r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
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
    if not hours_list:
        return ""

    def to_h(s):
        m = re.match(r"(\d{1,2})(am|pm)", s.lower())
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
