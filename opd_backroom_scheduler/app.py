import tkinter as tk
from tkinter import ttk, messagebox
import re
from datetime import datetime, timedelta

from . import constants as _constants
from .constants import (
    DAYS, DAY_FULL, SHIFT_SUGGESTIONS,
    CLR_HEADER_BLUE, CLR_BLUE_LIGHT, CLR_GOLD, CLR_GOLD_DARK,
    CLR_BG, CLR_WHITE, CLR_DARK, CLR_MUTED,
    CLR_PERSONAL, CLR_BACKROOM, CLR_GRAY_ROW, CLR_GRAY_TEXT,
    CLR_BACKROOM_BTN, CLR_PERSONAL_BTN,
)
from .utils import auto_text_color, get_coverage_color, parse_shift, shift_display, shift_sort_key
from .database import (
    init_db, get_associates, add_associate, update_associate, delete_associate,
    get_day_state, save_day_state, clear_day, clear_week, get_week_summary,
    get_call_outs, set_call_out, clear_call_outs_for_week, reset_associate_attendance,
    load_settings, save_setting,
)
from .scheduling import build_schedule, assign_exceptions, format_exc_label
from .widgets import TimelineCanvas, PSTimelineCanvas
from .views.board_view import open_board_view
from .views.exceptions_popup import open_exceptions_popup
from .views.settings_popup import open_threshold_settings

class BackroomApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPD Backroom Auto-Scheduler")
        self.configure(bg=CLR_BG)
        self.minsize(1200, 720)
        self.resizable(True, True)
        init_db()
        self._load_settings()
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

    def _load_settings(self):
        """Restore persisted thresholds and backroom cap from DB."""
        stored = load_settings()
        if "thresholds" in stored:
            _constants._thresholds = stored["thresholds"]
        if "backroom_max" in stored:
            _constants._backroom_max = stored["backroom_max"]

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
        ps_rows = self._last_ps_rows.get(day, [])
        covered = set()
        for r in ps_rows:
            exc = r.get("exceptions", "—")
            if not exc or exc == "—": continue
            body = exc.replace("EX", "").replace("@", "").strip()
            for part in body.split(","):
                part = part.strip()
                rng = re.match(r"(\d{1,2})(am|pm)?-(\d{1,2})(am|pm)", part.lower())
                if rng:
                    sn = int(rng.group(1)); s_per = rng.group(2)
                    en = int(rng.group(3)); e_per = rng.group(4)
                    eh = en + (12 if e_per == "pm" and en != 12 else 0)
                    sh = sn + (12 if s_per == "pm" and sn != 12 else 0) if s_per else (sn + (12 if e_per == "pm" and sn <= en and sn != 12 else 0))
                    for h in range(sh, eh): covered.add(h)
                else:
                    sm2 = re.match(r"(\d{1,2})(am|pm)", part.lower())
                    if sm2:
                        h = int(sm2.group(1))
                        if sm2.group(2) == "pm" and h != 12: h += 12
                        elif sm2.group(2) == "am" and h == 12: h = 0
                        covered.add(h)

        def _pd(d):
            m = re.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)", d)
            if not m: return None, None
            def c(h, mn, p):
                h = int(h); mn = int(mn or 0)
                if p == "PM" and h != 12: h += 12
                elif p == "AM" and h == 12: h = 0
                return datetime(2000, 1, 1, h, mn)
            return c(m.group(1), m.group(2), m.group(3)), c(m.group(4), m.group(5), m.group(6))

        sts = [_pd(r["shift_display"])[0] for r in ps_rows]
        ens = [_pd(r["shift_display"])[1] for r in ps_rows]
        sts = [s for s in sts if s]; ens = [e for e in ens if e]
        unc = []
        if sts and ens:
            for h in range(min(s.hour for s in sts), max(e.hour for e in ens)):
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
                for h_str in hours:
                    m = re.match(r"(\d{1,2})(am|pm)", h_str.lower())
                    if m:
                        h = int(m.group(1))
                        if m.group(2) == "pm" and h != 12: h += 12
                        elif m.group(2) == "am" and h == 12: h = 0
                        covered_hours.add(h)

        def _pd(disp):
            m = re.match(
                r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)",
                disp)
            if not m: return None, None
            def c(h, mn, p):
                h = int(h); mn = int(mn or 0)
                if p == "PM" and h != 12: h += 12
                elif p == "AM" and h == 12: h = 0
                return datetime(2000, 1, 1, h, mn)
            return c(m.group(1), m.group(2), m.group(3)), c(m.group(4), m.group(5), m.group(6))

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
            m = re.match(r'(\d{1,2})(?::(\d{2}))? (AM|PM)', disp)
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
        open_board_view(self, day)
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
        open_exceptions_popup(self, aid, day)
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
        open_threshold_settings(self)
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
        clear_day(day)

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

