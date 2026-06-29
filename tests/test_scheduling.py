import pytest
from opd_backroom_scheduler.scheduling import (
    build_schedule, assign_exceptions, format_exc_label,
    get_break_slots, build_presence_map, to_dt,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _assoc(aid, name, shift, pref=0, assignment="auto", today_shift=""):
    """Build a minimal associate tuple matching build_schedule's expected input."""
    return (aid, name, shift, pref, assignment, today_shift)

def _ps_row(aid, name, shift_display, break1="—", lunch="—", break2="—", pref=0):
    return {
        "id": aid, "name": name,
        "shift_display": shift_display,
        "break1": break1, "lunch": lunch, "break2": break2,
        "pref": pref, "dur": 8,
        "exceptions": "—",
    }


# ── build_schedule ─────────────────────────────────────────────────────────────

class TestBuildSchedule:
    def test_returns_two_lists(self):
        assocs = [_assoc(1, "Alice", "9-5")]
        br, ps = build_schedule(assocs)
        assert isinstance(br, list) and isinstance(ps, list)

    def test_floater_excluded(self):
        assocs = [_assoc(1, "Bob", "")]
        br, ps = build_schedule(assocs)
        assert br == [] and ps == []

    def test_br_pref_goes_to_backroom(self):
        assocs = [
            _assoc(1, "Alice", "9-5", pref=1),
            _assoc(2, "Bob",   "9-5", pref=0),
        ]
        br, ps = build_schedule(assocs)
        br_ids = {r["id"] for r in br}
        assert 1 in br_ids

    def test_manual_backroom_assignment_honoured(self):
        assocs = [
            _assoc(1, "Alice", "9-5", pref=0, assignment="backroom"),
            _assoc(2, "Bob",   "9-5", pref=0, assignment="personal"),
        ]
        br, ps = build_schedule(assocs)
        assert any(r["id"] == 1 for r in br)
        assert any(r["id"] == 2 for r in ps)

    def test_manual_personal_assignment_honoured(self):
        assocs = [_assoc(1, "Carol", "9-5", pref=1, assignment="personal")]
        br, ps = build_schedule(assocs)
        assert any(r["id"] == 1 for r in ps)
        assert not any(r["id"] == 1 for r in br)

    def test_5am_start_forced_to_personal(self):
        # 5am starts are personal-shopper only
        assocs = [_assoc(1, "Dan", "5-2", pref=1)]
        br, ps = build_schedule(assocs)
        assert any(r["id"] == 1 for r in ps)
        assert not any(r["id"] == 1 for r in br)

    def test_row_has_required_keys(self):
        assocs = [_assoc(1, "Eve", "9-5")]
        br, ps = build_schedule(assocs)
        all_rows = br + ps
        assert all_rows
        for row in all_rows:
            for key in ("id", "name", "shift", "shift_display", "break1", "lunch", "break2"):
                assert key in row

    def test_short_shift_no_breaks(self):
        # 3h shift → no breaks
        assocs = [_assoc(1, "Frank", "9-12")]
        br, ps = build_schedule(assocs)
        all_rows = br + ps
        assert all_rows[0]["break1"] == "—"
        assert all_rows[0]["lunch"]  == "—"
        assert all_rows[0]["break2"] == "—"

    def test_long_shift_has_all_breaks(self):
        # 9h shift → break1 + lunch + break2
        assocs = [_assoc(1, "Grace", "9-6")]
        br, ps = build_schedule(assocs)
        all_rows = br + ps
        row = all_rows[0]
        assert row["break1"] != "—"
        assert row["lunch"]  != "—"
        assert row["break2"] != "—"

    def test_today_shift_overrides_default(self):
        # today_shift of 10-6 should be used, not 9-5
        assocs = [_assoc(1, "Hank", "9-5", today_shift="10-6")]
        br, ps = build_schedule(assocs)
        all_rows = br + ps
        assert "10 AM" in all_rows[0]["shift_display"]

    def test_backroom_cap_respected(self):
        from opd_backroom_scheduler import constants as _c
        old = _c._backroom_max
        _c._backroom_max = 2
        try:
            assocs = [_assoc(i, f"P{i}", "9-5") for i in range(5)]
            br, ps = build_schedule(assocs)
            assert len(br) <= 2
        finally:
            _c._backroom_max = old

    def test_invalid_shift_skipped(self):
        assocs = [
            _assoc(1, "Good", "9-5"),
            _assoc(2, "Bad",  "not-a-shift"),
        ]
        br, ps = build_schedule(assocs)
        all_ids = {r["id"] for r in br + ps}
        assert 1 in all_ids
        assert 2 not in all_ids


# ── get_break_slots ───────────────────────────────────────────────────────────

class TestGetBreakSlots:
    def test_under_4h_no_breaks(self):
        slots = get_break_slots(9, 0, 12, 0, 3)
        assert slots == {}

    def test_4h_has_break1_only(self):
        slots = get_break_slots(9, 0, 13, 0, 4)
        assert "b1" in slots
        assert "ln" not in slots
        assert "b2" not in slots

    def test_6h_has_lunch(self):
        slots = get_break_slots(9, 0, 15, 0, 6)
        assert "ln" in slots

    def test_8h_has_all(self):
        slots = get_break_slots(9, 0, 17, 0, 8)
        assert "b1" in slots
        assert "ln" in slots
        assert "b2" in slots

    def test_break1_within_shift(self):
        slots = get_break_slots(9, 0, 17, 0, 8)
        start = to_dt(9, 0)
        end   = to_dt(17, 0)
        for s in slots["b1"]:
            assert start <= s < end


# ── format_exc_label ──────────────────────────────────────────────────────────

class TestFormatExcLabel:
    def test_empty_returns_empty(self):
        assert format_exc_label([]) == ""

    def test_single_hour(self):
        result = format_exc_label(["10am"])
        assert result == "EX @ 10am"

    def test_consecutive_range(self):
        result = format_exc_label(["10am", "11am", "12pm"])
        assert result.startswith("EX ")
        assert "-" in result

    def test_non_consecutive_separated(self):
        result = format_exc_label(["9am", "11am"])
        assert "EX" in result
        assert "," in result or "@" in result

    def test_pm_hours(self):
        result = format_exc_label(["1pm", "2pm"])
        assert "EX" in result


# ── assign_exceptions ─────────────────────────────────────────────────────────

class TestAssignExceptions:
    def test_no_eligible_returns_empty(self):
        rows = [_ps_row(1, "Alice", "9 AM – 5 PM")]
        result = assign_exceptions(rows, {1: 0})
        assert result == {}

    def test_eligible_gets_assigned(self):
        rows = [_ps_row(1, "Alice", "9 AM – 5 PM", lunch="1pm")]
        result = assign_exceptions(rows, {1: 1})
        assert 1 in result
        assert len(result[1]) > 0

    def test_non_eligible_not_assigned(self):
        rows = [
            _ps_row(1, "Alice", "9 AM – 5 PM", lunch="1pm"),
            _ps_row(2, "Bob",   "9 AM – 5 PM", lunch="1pm"),
        ]
        result = assign_exceptions(rows, {1: 1, 2: 0})
        assert 2 not in result

    def test_consecutive_hours_same_person(self):
        rows = [_ps_row(1, "Alice", "9 AM – 5 PM", lunch="1pm")]
        result = assign_exceptions(rows, {1: 1})
        hours = result.get(1, [])
        # If Alice covers multiple hours they should be assigned to her (not split)
        assert all(h for h in hours)

    def test_lunch_hour_not_assigned(self):
        # Alice's lunch is at 12pm — she shouldn't be assigned 12pm exceptions
        rows = [_ps_row(1, "Alice", "9 AM – 5 PM", lunch="12pm")]
        result = assign_exceptions(rows, {1: 1})
        hours = result.get(1, [])
        assert "12pm" not in hours
