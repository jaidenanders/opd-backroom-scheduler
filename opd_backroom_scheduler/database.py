import json
import sqlite3
from .constants import DB_FILE

# ── CONNECTION HELPER ─────────────────────────────────────────────────────────

def _connect():
    con = sqlite3.connect(DB_FILE)
    con.execute("PRAGMA foreign_keys = ON")
    return con

# ── SCHEMA ────────────────────────────────────────────────────────────────────

def init_db():
    con = _connect()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS associates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            shift TEXT NOT NULL DEFAULT '',
            backroom_pref INTEGER NOT NULL DEFAULT 0,
            color_bg TEXT NOT NULL DEFAULT '',
            color_text TEXT NOT NULL DEFAULT '',
            days_off TEXT NOT NULL DEFAULT '')""")
        try:
            con.execute("ALTER TABLE associates ADD COLUMN can_exceptions INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        for col, default in [("color_bg", "''"), ("color_text", "''"), ("days_off", "''")]:
            try:
                con.execute(f"ALTER TABLE associates ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
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
        con.execute("""CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL)""")
        con.commit()
    finally:
        con.close()

# ── SETTINGS PERSISTENCE ──────────────────────────────────────────────────────

def load_settings():
    """Return {key: value} for all persisted app settings."""
    con = _connect()
    try:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        return {k: json.loads(v) for k, v in rows}
    finally:
        con.close()

def save_setting(key, value):
    """Persist a single setting. value must be JSON-serialisable."""
    con = _connect()
    try:
        con.execute(
            "INSERT INTO app_settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value))
        )
        con.commit()
    finally:
        con.close()

# ── ASSOCIATES ────────────────────────────────────────────────────────────────

_associates_cache = None

def _invalidate_cache():
    global _associates_cache
    _associates_cache = None

def get_associates():
    global _associates_cache
    if _associates_cache is not None:
        return _associates_cache
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id,name,shift,backroom_pref,color_bg,color_text,days_off,can_exceptions "
            "FROM associates ORDER BY name"
        ).fetchall()
        _associates_cache = rows
        return rows
    finally:
        con.close()

def add_associate(name, shift, pref, color_bg="", color_text="", days_off="", can_exc=0):
    con = _connect()
    try:
        con.execute(
            "INSERT INTO associates(name,shift,backroom_pref,color_bg,color_text,days_off,can_exceptions) "
            "VALUES(?,?,?,?,?,?,?)",
            (name.strip(), shift, pref, color_bg, color_text, days_off, int(can_exc))
        )
        con.commit()
    finally:
        con.close()
    _invalidate_cache()

def update_associate(aid, name, shift, pref, color_bg="", color_text="", days_off="", can_exc=0):
    con = _connect()
    try:
        con.execute(
            "UPDATE associates SET name=?,shift=?,backroom_pref=?,color_bg=?,color_text=?,"
            "days_off=?,can_exceptions=? WHERE id=?",
            (name.strip(), shift, pref, color_bg, color_text, days_off, int(can_exc), aid)
        )
        con.commit()
    finally:
        con.close()
    _invalidate_cache()

def delete_associate(aid):
    con = _connect()
    try:
        con.execute("DELETE FROM associates WHERE id=?", (aid,))
        con.commit()
    finally:
        con.close()
    _invalidate_cache()

# ── SCHEDULE STATE ────────────────────────────────────────────────────────────

def get_day_state(day):
    """Return {aid: {present, assignment, today_shift}} for a day."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT associate_id,present,assignment,today_shift "
            "FROM weekly_schedule WHERE day=?", (day,)
        ).fetchall()
        return {r[0]: {"present": bool(r[1]), "assignment": r[2], "today_shift": r[3]}
                for r in rows}
    finally:
        con.close()

def save_day_state(day, aid, present, assignment, today_shift):
    con = _connect()
    try:
        con.execute(
            """INSERT INTO weekly_schedule(associate_id,day,present,assignment,today_shift)
               VALUES(?,?,?,?,?)
               ON CONFLICT(associate_id,day) DO UPDATE SET
               present=excluded.present,
               assignment=excluded.assignment,
               today_shift=excluded.today_shift""",
            (aid, day, int(present), assignment, today_shift)
        )
        con.commit()
    finally:
        con.close()

def clear_day(day):
    con = _connect()
    try:
        con.execute("DELETE FROM weekly_schedule WHERE day=?", (day,))
        con.execute("DELETE FROM call_outs WHERE day=?", (day,))
        con.commit()
    finally:
        con.close()

def clear_week():
    con = _connect()
    try:
        con.execute("DELETE FROM weekly_schedule")
        con.commit()
    finally:
        con.close()

def get_week_summary():
    """Return {day: present_count} for tab badge counts."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT day, COUNT(*) FROM weekly_schedule WHERE present=1 GROUP BY day"
        ).fetchall()
        return dict(rows)
    finally:
        con.close()

# ── CALL-OUTS ─────────────────────────────────────────────────────────────────

def get_call_outs(day):
    """Return {aid: status} for a day. status = 'callout'."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT associate_id, status FROM call_outs WHERE day=?", (day,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        con.close()

def set_call_out(aid, day, status):
    """Set or clear a call-out. status='' clears it."""
    con = _connect()
    try:
        if status:
            con.execute(
                """INSERT INTO call_outs(associate_id,day,status) VALUES(?,?,?)
                   ON CONFLICT(associate_id,day) DO UPDATE SET status=excluded.status""",
                (aid, day, status)
            )
        else:
            con.execute("DELETE FROM call_outs WHERE associate_id=? AND day=?", (aid, day))
        con.commit()
    finally:
        con.close()

def clear_call_outs_for_week():
    con = _connect()
    try:
        con.execute("DELETE FROM call_outs")
        con.commit()
    finally:
        con.close()

def reset_associate_attendance(aid):
    """Delete stored attendance for this associate so auto-logic re-derives from days_off."""
    con = _connect()
    try:
        con.execute("DELETE FROM weekly_schedule WHERE associate_id=?", (aid,))
        con.commit()
    finally:
        con.close()
