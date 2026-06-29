import re
import tkinter as tk
from datetime import datetime, timedelta

from ..constants import CLR_BG, CLR_DARK, CLR_HEADER_BLUE, CLR_WHITE, CLR_MUTED
from ..scheduling import format_exc_label


def open_exceptions_popup(app, aid, day):
    """Popup to assign exceptions hours — shows full coverage table."""
    ps_rows  = app._last_ps_rows.get(day, [])
    row_dict = next((r for r in ps_rows if r["id"] == aid), None)
    if not row_dict:
        return

    def parse_disp(disp):
        m = re.match(
            r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
            disp)
        if not m:
            return None, None
        def c(h, mn, p):
            h = int(h); mn = int(mn or 0)
            if p == "PM" and h != 12: h += 12
            elif p == "AM" and h == 12: h = 0
            return datetime(2000, 1, 1, h, mn)
        return c(m.group(1), m.group(2), m.group(3)), c(m.group(4), m.group(5), m.group(6))

    def fmt_h(dt):
        h12 = dt.hour % 12 or 12
        return f"{h12}{'am' if dt.hour < 12 else 'pm'}"

    def to_h24(s):
        m = re.match(r"(\d{1,2})(am|pm)", s.lower())
        if not m:
            return None
        h = int(m.group(1))
        if m.group(2) == "pm" and h != 12: h += 12
        elif m.group(2) == "am" and h == 12: h = 0
        return h

    def expand_exc(exc_str):
        """Expand exception label string to a set of h24 ints."""
        hours = set()
        if not exc_str or exc_str == "—":
            return hours
        body = exc_str.replace("EX", "").replace("@", "").strip()
        for part in body.split(","):
            part = part.strip()
            range_m = re.match(r"(\d{1,2})(am|pm)?-(\d{1,2})(am|pm)", part.lower())
            if range_m:
                sn    = int(range_m.group(1))
                s_per = range_m.group(2)
                en    = int(range_m.group(3))
                e_per = range_m.group(4)
                eh = en
                if e_per == "pm" and en != 12: eh = en + 12
                elif e_per == "am" and en == 12: eh = 0
                if s_per:
                    sh = sn
                    if s_per == "pm" and sn != 12: sh = sn + 12
                    elif s_per == "am" and sn == 12: sh = 0
                else:
                    sh = sn
                    if e_per == "pm" and sn <= en:
                        if sn != 12: sh = sn + 12
                    elif e_per == "pm" and sn > en:
                        pass
                    elif e_per == "am":
                        pass
                for hh in range(sh, eh):
                    hours.add(hh)
            else:
                h = to_h24(part)
                if h is not None:
                    hours.add(h)
        return hours

    all_ps = sorted(ps_rows, key=lambda r: (
        (parse_disp(r.get("shift_display", ""))[0] or datetime(2000, 1, 1, 23, 0)).hour,
        r.get("name", "")
    ))

    coverage_map = {}
    for r in all_ps:
        if r.get("exceptions", "—") == "—":
            continue
        for h24 in expand_exc(r["exceptions"]):
            coverage_map[h24] = r["name"]

    s_dt, e_dt = parse_disp(row_dict.get("shift_display", ""))
    if not s_dt:
        return
    hour_slots_dt = []
    t = s_dt.replace(minute=0)
    if t < s_dt:
        t += timedelta(hours=1)
    while t < e_dt:
        hour_slots_dt.append(t)
        t += timedelta(hours=1)

    cur_hours_h24 = expand_exc(row_dict.get("exceptions", "—"))

    # ── Dialog ────────────────────────────────────────────────────────────
    dlg = tk.Toplevel(app)
    dlg.title(f"Exceptions — {row_dict['name']}")
    dlg.configure(bg=CLR_BG)
    dlg.resizable(True, False)
    dlg.grab_set()

    tk.Label(dlg, text=f"Assign Exceptions — {row_dict['name']}",
             font=("Helvetica", 11, "bold"), bg=CLR_BG, fg=CLR_DARK,
             pady=8).pack(padx=20, anchor="w")

    tbl_frame = tk.LabelFrame(dlg, text="  Current Exceptions Coverage  ",
                               bg=CLR_BG, fg=CLR_DARK,
                               font=("Helvetica", 9, "bold"),
                               padx=8, pady=6)
    tbl_frame.pack(fill="x", padx=16, pady=(0, 8))

    all_starts = [parse_disp(r["shift_display"])[0] for r in ps_rows]
    all_ends   = [parse_disp(r["shift_display"])[1] for r in ps_rows]
    all_starts = [s for s in all_starts if s]
    all_ends   = [e for e in all_ends   if e]
    if all_starts and all_ends:
        day_start_h = min(s.hour for s in all_starts)
        day_end_h   = max(e.hour for e in all_ends)
        t = datetime(2000, 1, 1, day_start_h, 0)
        col = 0
        while t.hour < day_end_h:
            h24     = t.hour
            h_s     = fmt_h(t)
            who     = coverage_map.get(h24, "")
            bg      = "#C8E6C9" if who else "#FFCDD2"
            lbl_txt = h_s + "\n" + (who if who else "Uncovered")
            tk.Label(tbl_frame, text=lbl_txt,
                     bg=bg, fg="#1B5E20" if who else "#B71C1C",
                     font=("Helvetica", 7, "bold"),
                     padx=4, pady=2, relief="solid", bd=1,
                     width=7, anchor="center",
                     wraplength=52).grid(row=0, column=col, padx=2, pady=2)
            col += 1
            t += timedelta(hours=1)

    tk.Label(dlg, text=f"Select hours for {row_dict['name']}:",
             font=("Helvetica", 9, "bold"), bg=CLR_BG, fg=CLR_DARK).pack(padx=16, anchor="w")

    vars_map  = {}
    box_frame = tk.Frame(dlg, bg=CLR_BG)
    box_frame.pack(padx=16, pady=6, fill="x")
    for i, slot_dt in enumerate(hour_slots_dt):
        h24 = slot_dt.hour
        h_s = fmt_h(slot_dt)
        v   = tk.BooleanVar(value=h24 in cur_hours_h24)
        vars_map[h_s] = (v, h24)
        currently_covered = h24 in coverage_map
        cb_fg = "#E65100" if not currently_covered else CLR_DARK
        tk.Checkbutton(box_frame, text=h_s, variable=v,
                       bg=CLR_BG, activebackground=CLR_BG,
                       fg=cb_fg,
                       font=("Helvetica", 10, "bold" if not currently_covered else "normal"),
                       cursor="hand2").grid(row=0, column=i, padx=5)

    tk.Label(dlg, text="Orange = currently uncovered hours",
             bg=CLR_BG, fg="#E65100", font=("Helvetica", 7, "italic")).pack(
        padx=16, anchor="w", pady=(0, 6))

    def do_save():
        chosen_h24  = sorted(h24 for _, (v, h24) in vars_map.items() if v.get())
        chosen_strs = [fmt_h(datetime(2000, 1, 1, h, 0)) for h in chosen_h24]
        label = format_exc_label(chosen_strs) if chosen_strs else "—"
        row_dict["exceptions"] = label
        app._populate_day(day, app._last_br_rows.get(day, []),
                              app._last_ps_rows.get(day, []))
        app._recompute_exc_warning(day)
        dlg.destroy()

    def do_clear():
        row_dict["exceptions"] = "—"
        app._populate_day(day, app._last_br_rows.get(day, []),
                              app._last_ps_rows.get(day, []))
        app._recompute_exc_warning(day)
        dlg.destroy()

    btn_r = tk.Frame(dlg, bg=CLR_BG)
    btn_r.pack(pady=10)
    tk.Button(btn_r, text="Save", command=do_save,
              bg=CLR_HEADER_BLUE, fg=CLR_WHITE, relief="flat",
              padx=14, pady=5, font=("Helvetica", 9, "bold"),
              cursor="hand2").pack(side="left", padx=6)
    tk.Button(btn_r, text="Clear EX", command=do_clear,
              bg="#607D8B", fg=CLR_WHITE, relief="flat",
              padx=10, pady=5, font=("Helvetica", 9),
              cursor="hand2").pack(side="left", padx=6)
    tk.Button(btn_r, text="Cancel", command=dlg.destroy,
              bg="#E0E0E0", fg="#555", relief="flat",
              padx=10, pady=5, font=("Helvetica", 9),
              cursor="hand2").pack(side="left", padx=6)
