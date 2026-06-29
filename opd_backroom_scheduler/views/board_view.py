import tkinter as tk
import time as _time
from datetime import datetime, date as _date

from ..constants import DAY_FULL
from ..database import get_associates, get_call_outs
from ..utils import auto_text_color


def open_board_view(app, day):
    """Full-screen no-scroll board for a vertical touchscreen monitor."""
    br_rows = app._last_br_rows.get(day, [])
    ps_rows = app._last_ps_rows.get(day, [])
    if not br_rows and not ps_rows:
        tk.messagebox.showinfo("No Schedule",
                               "Generate the schedule first before viewing the board.",
                               parent=app)
        return

    # ── Window ───────────────────────────────────────────────────────────
    win = tk.Toplevel(app)
    win.title(f"OPD Board — {DAY_FULL[day]}")
    BG = "#1A1F2E"
    win.configure(bg=BG)
    win.state("zoomed")
    win.update_idletasks()

    board_state = {"last_co": {}, "alive": True}
    win.protocol("WM_DELETE_WINDOW",
                 lambda: (board_state.update({"alive": False}), win.destroy()))

    # ── Layout constants ─────────────────────────────────────────────────
    COLS       = ["Name", "Shift", "Break 1", "Lunch", "Break 2"]
    COL_HDR    = ["#1C3A5E", "#1C3A5E", "#BF360C", "#7B1FA2", "#BF360C"]
    COL_W      = [3, 2, 2, 2, 2]
    N_COLS     = len(COLS)
    PANEL_COLS = N_COLS * 2 + 1

    name_taps  = app.board_name_taps.setdefault(day, {})
    break_taps = app.board_break_taps.setdefault(day, {})

    # ── Fixed header ─────────────────────────────────────────────────────
    top = tk.Frame(win, bg="#1565C0", height=52)
    top.pack(fill="x", side="top")
    top.pack_propagate(False)
    tk.Label(top, text="OPD Backroom Board",
             font=("Helvetica", 18, "bold"),
             bg="#1565C0", fg="white").pack(side="left", padx=20, pady=8)
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
    if day in app._day_exc_warn_var:
        _cw = app._day_exc_warn_var[day].get()
        if _cw:
            board_exc_var.set(_cw)
            board_exc_lbl.pack(side="left", padx=12, pady=6)

    # ── Fixed legend ─────────────────────────────────────────────────────
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

    # ── Body ─────────────────────────────────────────────────────────────
    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, side="top")

    def render(call_outs):
        # Sync board warning with main schedule warning
        if day in app._day_exc_warn_var:
            cur_w = app._day_exc_warn_var[day].get()
            board_exc_var.set(cur_w)
            if cur_w and not board_exc_lbl.winfo_ismapped():
                board_exc_lbl.pack(side="left", padx=12, pady=6)
            elif not cur_w and board_exc_lbl.winfo_ismapped():
                board_exc_lbl.pack_forget()

        cur_br = app._last_br_rows.get(day, br_rows)
        cur_ps = app._last_ps_rows.get(day, ps_rows)
        br_vis = [r for r in cur_br if call_outs.get(r["id"], "") != "callout"]
        ps_vis = [r for r in cur_ps if call_outs.get(r["id"], "") != "callout"]

        assoc_colors = {a[0]: (a[4] or "", a[5] or "") for a in get_associates()}

        for w in body.winfo_children():
            w.destroy()

        for ci in range(PANEL_COLS):
            if ci == N_COLS:
                body.grid_columnconfigure(ci, weight=0, minsize=6)
            else:
                pci = ci if ci < N_COLS else ci - N_COLS - 1
                body.grid_columnconfigure(ci, weight=COL_W[pci], uniform="cols")

        items = []
        def add_sec(title, color, rows):
            if not rows: return
            items.append({"type": "sec_hdr", "title": title,
                          "color": color, "count": len(rows)})
            items.append({"type": "col_hdr", "color": color})
            for i in range(0, len(rows), 2):
                items.append({"type": "pair",
                              "pair": (rows[i], rows[i+1] if i+1 < len(rows) else None)})
        add_sec("📦  Backroom Crew",    "#1565C0", br_vis)
        add_sec("🛒  Personal Shoppers","#2E7D32",  ps_vis)

        n = len(items)
        if n == 0: return
        for ri in range(n):
            body.grid_rowconfigure(ri, weight=1, uniform="rows")

        def draw_assoc(row, grid_row, col_offset, row_idx):
            aid = row["id"]
            cust_bg, cust_text = assoc_colors.get(aid, ("", ""))
            row_bg  = cust_bg if cust_bg else ("#263238" if row_idx%2==0 else "#2E3B42")
            name_fg = auto_text_color(cust_bg) if cust_bg else "white"

            nt  = name_taps.setdefault(aid, {"n": 0, "last": 0.0})
            exc_label  = row.get("exceptions", "—")
            exc_suffix = f"  {exc_label}" if exc_label and exc_label != "—" else ""
            nv  = tk.StringVar(value=row["name"] + exc_suffix)
            nl  = tk.Label(body, textvariable=nv,
                           font=("Helvetica", 11, "bold"),
                           bg=row_bg, fg=name_fg,
                           anchor="w", padx=8, cursor="hand2")
            nl.grid(row=grid_row, column=col_offset, sticky="nsew", padx=1, pady=1)

            def _revert_name_color(nt=nt, label_ref={"lbl": None}):
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
                    app._set_callout(aid, day, "callout", "br")

            nl.bind("<Button-1>", on_name_tap)

            tk.Label(body, text=row["shift_display"],
                     font=("Helvetica", 9),
                     bg=row_bg, fg="#78909C",
                     anchor="center").grid(
                row=grid_row, column=col_offset+1,
                sticky="nsew", padx=1, pady=1)

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
                        row=grid_row, column=gcol, sticky="nsew", padx=1, pady=1)
                    continue

                tk_key  = (aid, bkey)
                bt      = break_taps.setdefault(tk_key, {"n": 0, "last": 0.0, "text": bval, "seq": 0})
                cv      = tk.StringVar(value=bt["text"])
                lbl_bg  = "#1B5E20" if bt["n"] >= 1 else row_bg
                lbl_fg  = "white"   if bt["n"] >= 1 else bfg

                lbl = tk.Label(body, textvariable=cv,
                               font=("Helvetica", 13, "bold"),
                               bg=lbl_bg, fg=lbl_fg,
                               anchor="center", cursor="hand2")
                lbl.grid(row=grid_row, column=gcol, sticky="nsew", padx=1, pady=1)

                hold_state = {"job": None}

                def on_press(e, lbl=lbl, cv=cv, bt=bt,
                             orig=bval, rbg=row_bg, fg_n=bfg,
                             hs=hold_state):
                    def fire():
                        hs["job"] = None
                        now = _time.time()
                        gap = now - bt["last"]
                        if bt["n"] == 0:
                            bt["n"] = 1
                            bt["last"] = now
                            bt["text"] = datetime.now().strftime("%I:%M %p").lstrip("0")
                            cv.set(f"Started {bt['text']}")
                            if lbl.winfo_exists():
                                lbl.config(bg="#1B5E20", fg="white")
                        else:
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
                    if hs["job"] is not None:
                        try: lbl.after_cancel(hs["job"])
                        except Exception: pass
                        hs["job"] = None

                lbl.bind("<ButtonPress-1>",   on_press)
                lbl.bind("<ButtonRelease-1>", on_release)

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
                            row=ri, column=po+ci, sticky="nsew", padx=1, pady=1)
                tk.Frame(body, bg="#0D1117").grid(row=ri, column=N_COLS, sticky="nsew")

            elif item["type"] == "pair":
                left, right = item["pair"]
                tk.Frame(body, bg="#0D1117").grid(
                    row=ri, column=N_COLS, sticky="nsew", padx=1, pady=1)
                draw_assoc(left,  ri, 0,          pair_idx * 2)
                if right:
                    draw_assoc(right, ri, N_COLS + 1, pair_idx * 2 + 1)
                else:
                    tk.Frame(body, bg=BG).grid(
                        row=ri, column=N_COLS + 1,
                        columnspan=N_COLS, sticky="nsew", padx=1, pady=1)
                pair_idx += 1

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
