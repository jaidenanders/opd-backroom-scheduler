import re
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
from .utils import get_coverage_color
from .constants import CLR_MUTED

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
        if not s or s == "—": return None
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", s.lower())
        if not m: return None
        h = int(m.group(1)); mins = int(m.group(2) or 0); per = m.group(3)
        if per == "pm" and h != 12: h += 12
        elif per == "am" and h == 12: h = 0
        return datetime(2000, 1, 1, h, mins)

    def _parse_shift_disp(self, s):
        m = re.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)", s)
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
        def fmt(dt):
            h12 = dt.hour%12 or 12; p="am" if dt.hour<12 else "pm"
            return f"{h12}:{dt.minute:02d}{p}" if dt.minute else f"{h12}{p}"
        t_end = t + timedelta(minutes=20)
        n = self._coverage.get(t, 0)
        present  = self._tick_names.get(t, [])
        on_break = self._absent_n.get(t, [])
        lines = [f"{fmt(t)} – {fmt(t_end)}  |  {n} in backroom"]
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
        if not s or s == "—": return None
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", s.lower())
        if not m: return None
        h = int(m.group(1)); mins = int(m.group(2) or 0); per = m.group(3)
        if per == "pm" and h != 12: h += 12
        elif per == "am" and h == 12: h = 0
        return datetime(2000, 1, 1, h, mins)

    def _parse_shift_disp(self, s):
        m = re.match(r"(\d{1,2})(?::(\d{2}))? (AM|PM)\s*[–-]\s*(\d{1,2})(?::(\d{2}))? (AM|PM)", s)
        if not m: return None, None
        def cvt(h, mn, p):
            h = int(h); mn = int(mn or 0)
            if p == "PM" and h != 12: h += 12
            elif p == "AM" and h == 12: h = 0
            return datetime(2000, 1, 1, h, mn)
        return cvt(m.group(1),m.group(2),m.group(3)), cvt(m.group(4),m.group(5),m.group(6))

    def _render(self):
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
