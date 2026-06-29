import tkinter as tk
from tkinter import ttk

from .. import constants as _constants
from ..constants import (
    DAYS, DEFAULT_THRESHOLDS,
    CLR_BG, CLR_DARK, CLR_MUTED, CLR_WHITE,
    CLR_HEADER_BLUE,
)
from ..database import get_day_state, get_associates, save_setting
from ..scheduling import build_schedule


COLOR_HEX = {
    "Red":         "#EF5350",
    "Orange":      "#FF9800",
    "Yellow":      "#FFF176",
    "Light Green": "#A5D6A7",
    "Med Green":   "#66BB6A",
    "Dark Green":  "#2E7D32",
}
COLOR_NAMES = list(COLOR_HEX.keys())


def _hex_to_name(h):
    for name, val in COLOR_HEX.items():
        if val.upper() == h.upper():
            return name
    return "Dark Green"


def open_threshold_settings(app):
    """Dialog to configure coverage colors and backroom cap."""
    dlg = tk.Toplevel(app)
    dlg.title("Schedule Settings")
    dlg.configure(bg=CLR_BG)
    dlg.resizable(True, True)
    dlg.minsize(420, 480)
    dlg.grab_set()

    tk.Label(dlg, text="Schedule Settings",
             font=("Helvetica", 13, "bold"), bg=CLR_BG, fg=CLR_DARK,
             pady=10).pack(fill="x", padx=20)

    # ── Backroom cap ─────────────────────────────────────────────────────
    cap_frame = tk.LabelFrame(dlg, text="  Backroom Capacity  ",
                               bg=CLR_BG, fg=CLR_DARK,
                               font=("Helvetica", 9, "bold"),
                               padx=12, pady=10)
    cap_frame.pack(fill="x", padx=20, pady=(0, 12))
    tk.Label(cap_frame, text="Max associates in backroom at once:",
             bg=CLR_BG, fg=CLR_DARK, font=("Helvetica", 10)).grid(
             row=0, column=0, sticky="w")
    cap_var = tk.IntVar(value=_constants._backroom_max)
    tk.Spinbox(cap_frame, from_=1, to=30, textvariable=cap_var,
               width=6, font=("Helvetica", 11), relief="solid",
               justify="center").grid(row=0, column=1, padx=12, sticky="w")
    tk.Label(cap_frame,
             text="Associates beyond the cap auto-move to Personal Shoppers.",
             bg=CLR_BG, fg=CLR_MUTED, font=("Helvetica", 8)).grid(
             row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ── Color thresholds ─────────────────────────────────────────────────
    thresh_outer = tk.LabelFrame(dlg, text="  Coverage Bar Colors  ",
                                  bg=CLR_BG, fg=CLR_DARK,
                                  font=("Helvetica", 9, "bold"),
                                  padx=12, pady=8)
    thresh_outer.pack(fill="both", expand=True, padx=20, pady=(0, 8))

    tk.Label(thresh_outer,
             text="Each tier applies when headcount >= Min Count.",
             bg=CLR_BG, fg=CLR_MUTED, font=("Helvetica", 8)).pack(
             anchor="w", pady=(0, 6))

    hdr_frame = tk.Frame(thresh_outer, bg=CLR_BG)
    hdr_frame.pack(fill="x")
    for txt, w in [("Min Count >=", 10), ("Color", 13), ("Preview", 8)]:
        tk.Label(hdr_frame, text=txt, bg=CLR_HEADER_BLUE, fg=CLR_WHITE,
                 font=("Helvetica", 9, "bold"), width=w,
                 pady=4, relief="flat").pack(side="left", padx=1)

    row_vars = []

    def build_threshold_rows(container):
        for w in container.winfo_children():
            w.destroy()
        row_vars.clear()
        for i, (mn, col) in enumerate(_constants._thresholds):
            row_bg   = CLR_WHITE if i % 2 == 0 else "#F0F4F8"
            rf       = tk.Frame(container, bg=row_bg)
            rf.pack(fill="x", pady=1)
            count_var = tk.IntVar(value=mn)
            color_var = tk.StringVar(value=_hex_to_name(col))
            tk.Spinbox(rf, from_=0, to=20, textvariable=count_var,
                       width=8, font=("Helvetica", 10), relief="solid",
                       justify="center", bg=row_bg).pack(side="left", padx=12, pady=4)
            cb = ttk.Combobox(rf, textvariable=color_var,
                               values=COLOR_NAMES, state="readonly",
                               width=13, font=("Helvetica", 10))
            cb.pack(side="left", padx=8, pady=4)
            swatch = tk.Frame(rf, width=44, height=22,
                               bg=COLOR_HEX.get(_hex_to_name(col), col),
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

    # ── Buttons ──────────────────────────────────────────────────────────
    btn_row = tk.Frame(dlg, bg=CLR_BG)
    btn_row.pack(fill="x", padx=20, pady=(0, 14))

    def apply_settings():
        old_cap = _constants._backroom_max
        _constants._backroom_max = cap_var.get()
        new_thresh = []
        for count_var, color_var in row_vars:
            new_thresh.append([count_var.get(), COLOR_HEX.get(color_var.get(), "#888")])
        new_thresh.sort(key=lambda x: x[0])
        _constants._thresholds = new_thresh
        save_setting("backroom_max", _constants._backroom_max)
        save_setting("thresholds", _constants._thresholds)
        for day in DAYS:
            if day in app._generated:
                app.day_timelines[day]._render()
                app.day_ps_timelines[day]._render()
                if _constants._backroom_max != old_cap:
                    day_state = get_day_state(day)
                    assocs    = get_associates()
                    active = [
                        (a_id, n, sh, pf,
                         day_state.get(a_id, {}).get("assignment", "auto"),
                         day_state.get(a_id, {}).get("today_shift", ""))
                        for a_id, n, sh, pf, *_ in assocs
                        if day_state.get(a_id, {}).get("present")
                    ]
                    br_rows, ps_rows = build_schedule(active)
                    app._last_br_rows[day] = br_rows
                    app._last_ps_rows[day] = ps_rows
                    app._populate_day(day, br_rows, ps_rows)
        dlg.destroy()

    def reset_defaults():
        _constants._thresholds   = [list(t) for t in DEFAULT_THRESHOLDS]
        _constants._backroom_max = 10
        save_setting("backroom_max", 10)
        save_setting("thresholds", _constants._thresholds)
        cap_var.set(10)
        build_threshold_rows(rows_container)
        for day in DAYS:
            if day in app._generated:
                app.day_timelines[day]._render()
                app.day_ps_timelines[day]._render()

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
