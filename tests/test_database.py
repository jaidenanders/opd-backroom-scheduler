import os
import sqlite3
import pytest
from opd_backroom_scheduler import constants as _c


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point DB_FILE to a temp file for each test; clean up after."""
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(_c, "DB_FILE", db)
    # Re-patch the module-level DB_FILE used by database functions
    import opd_backroom_scheduler.database as db_mod
    monkeypatch.setattr(db_mod, "_associates_cache", None)
    monkeypatch.setattr(_c, "DB_FILE", db)
    # Patch the constant imported directly in database.py
    import importlib
    from opd_backroom_scheduler import database
    database.DB_FILE = db          # module attr used by _connect()
    # Re-use the patched constant
    from opd_backroom_scheduler.database import init_db
    init_db()
    yield
    database.DB_FILE = _c.DB_FILE  # restore (monkeypatch also handles this)


from opd_backroom_scheduler.database import (
    get_associates, add_associate, update_associate, delete_associate,
    get_day_state, save_day_state, clear_day, clear_week,
    get_call_outs, set_call_out, clear_call_outs_for_week,
    reset_associate_attendance, get_week_summary,
    load_settings, save_setting,
)


# ── Associates ────────────────────────────────────────────────────────────────

class TestAssociates:
    def test_empty_on_fresh_db(self):
        assert get_associates() == []

    def test_add_and_retrieve(self):
        add_associate("Alice", "9-5", 1)
        rows = get_associates()
        assert len(rows) == 1
        assert rows[0][1] == "Alice"

    def test_update_associate(self):
        add_associate("Bob", "9-5", 0)
        aid = get_associates()[0][0]
        update_associate(aid, "Bob", "8-4", 1)
        rows = get_associates()
        assert rows[0][2] == "8-4"
        assert rows[0][3] == 1

    def test_delete_associate(self):
        add_associate("Carol", "9-5", 0)
        aid = get_associates()[0][0]
        delete_associate(aid)
        assert get_associates() == []

    def test_name_must_be_unique(self):
        add_associate("Dan", "9-5", 0)
        with pytest.raises(Exception):
            add_associate("Dan", "10-6", 0)

    def test_cache_invalidated_on_write(self):
        add_associate("Eve", "9-5", 0)
        first = get_associates()
        add_associate("Frank", "9-5", 0)
        second = get_associates()
        assert len(second) == len(first) + 1


# ── Day State ─────────────────────────────────────────────────────────────────

class TestDayState:
    def test_empty_before_save(self):
        assert get_day_state("Mon") == {}

    def test_save_and_retrieve(self):
        add_associate("Alice", "9-5", 0)
        aid = get_associates()[0][0]
        save_day_state("Mon", aid, True, "auto", "9-5")
        state = get_day_state("Mon")
        assert aid in state
        assert state[aid]["present"] is True
        assert state[aid]["assignment"] == "auto"
        assert state[aid]["today_shift"] == "9-5"

    def test_upsert_updates_existing(self):
        add_associate("Bob", "9-5", 0)
        aid = get_associates()[0][0]
        save_day_state("Tue", aid, True, "auto", "")
        save_day_state("Tue", aid, False, "personal", "10-6")
        state = get_day_state("Tue")
        assert state[aid]["present"] is False
        assert state[aid]["assignment"] == "personal"

    def test_clear_day_removes_state(self):
        add_associate("Carol", "9-5", 0)
        aid = get_associates()[0][0]
        save_day_state("Wed", aid, True, "auto", "")
        clear_day("Wed")
        assert get_day_state("Wed") == {}

    def test_clear_week_removes_all(self):
        add_associate("Dan", "9-5", 0)
        aid = get_associates()[0][0]
        for day in ("Mon", "Tue", "Wed"):
            save_day_state(day, aid, True, "auto", "")
        clear_week()
        for day in ("Mon", "Tue", "Wed"):
            assert get_day_state(day) == {}

    def test_reset_associate_attendance(self):
        add_associate("Eve", "9-5", 0)
        aid = get_associates()[0][0]
        save_day_state("Mon", aid, True, "auto", "")
        reset_associate_attendance(aid)
        assert get_day_state("Mon") == {}

    def test_week_summary(self):
        add_associate("Frank", "9-5", 0)
        aid = get_associates()[0][0]
        save_day_state("Mon", aid, True, "auto", "")
        save_day_state("Tue", aid, False, "auto", "")
        summary = get_week_summary()
        assert summary.get("Mon") == 1
        assert "Tue" not in summary


# ── Call-Outs ─────────────────────────────────────────────────────────────────

class TestCallOuts:
    def test_empty_before_set(self):
        assert get_call_outs("Mon") == {}

    def test_set_callout(self):
        add_associate("Alice", "9-5", 0)
        aid = get_associates()[0][0]
        set_call_out(aid, "Mon", "callout")
        co = get_call_outs("Mon")
        assert co[aid] == "callout"

    def test_clear_callout(self):
        add_associate("Bob", "9-5", 0)
        aid = get_associates()[0][0]
        set_call_out(aid, "Mon", "callout")
        set_call_out(aid, "Mon", "")
        assert get_call_outs("Mon") == {}

    def test_clear_day_removes_callouts(self):
        add_associate("Carol", "9-5", 0)
        aid = get_associates()[0][0]
        set_call_out(aid, "Wed", "callout")
        clear_day("Wed")
        assert get_call_outs("Wed") == {}

    def test_clear_week_callouts(self):
        add_associate("Dan", "9-5", 0)
        aid = get_associates()[0][0]
        set_call_out(aid, "Mon", "callout")
        set_call_out(aid, "Fri", "callout")
        clear_call_outs_for_week()
        assert get_call_outs("Mon") == {}
        assert get_call_outs("Fri") == {}


# ── Settings ──────────────────────────────────────────────────────────────────

class TestSettings:
    def test_empty_on_fresh_db(self):
        assert load_settings() == {}

    def test_save_and_load(self):
        save_setting("backroom_max", 5)
        assert load_settings()["backroom_max"] == 5

    def test_overwrite(self):
        save_setting("backroom_max", 5)
        save_setting("backroom_max", 12)
        assert load_settings()["backroom_max"] == 12

    def test_complex_value(self):
        thresholds = [[0, "#EF5350"], [3, "#66BB6A"]]
        save_setting("thresholds", thresholds)
        assert load_settings()["thresholds"] == thresholds

    def test_multiple_keys(self):
        save_setting("backroom_max", 8)
        save_setting("thresholds", [[0, "#red"]])
        stored = load_settings()
        assert "backroom_max" in stored
        assert "thresholds" in stored
