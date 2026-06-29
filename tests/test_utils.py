import pytest
from opd_backroom_scheduler.utils import parse_shift, shift_display, auto_text_color, shift_sort_key


class TestParseShift:
    def test_simple_whole_hours(self):
        r = parse_shift("9-5")
        assert r == (9, 0, 17, 0, 8.0)

    def test_shift_with_minutes(self):
        r = parse_shift("8:30-2:30")
        assert r == (8, 30, 14, 30, 6.0)

    def test_early_morning_start(self):
        # 5am start — stays as-is (not auto-bumped to PM)
        r = parse_shift("5-2")
        assert r[0] == 5

    def test_auto_pm_bump(self):
        # 1-4 are ambiguous AM starts; get bumped to PM
        r = parse_shift("1-9")
        assert r[0] == 13

    def test_returns_none_for_blank(self):
        assert parse_shift("") is None
        assert parse_shift("  ") is None

    def test_returns_none_for_na(self):
        assert parse_shift("N/A") is None
        assert parse_shift("NONE") is None
        assert parse_shift("-") is None

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_shift("9am-5pm")

    def test_invalid_minutes_raises(self):
        with pytest.raises(ValueError):
            parse_shift("9:99-5")

    def test_duration_in_result(self):
        r = parse_shift("9-5")
        assert r[4] == 8.0

    def test_fractional_duration(self):
        r = parse_shift("9-1:30")
        assert r[4] == pytest.approx(4.5)

    def test_overnight_shift_rejected(self):
        # 9pm to 2am would be negative / invalid — should raise or return None
        with pytest.raises(ValueError):
            parse_shift("21-2")

    def test_em_dash_separator(self):
        r = parse_shift("9–5")
        assert r is not None
        assert r[0] == 9

    def test_none_input(self):
        assert parse_shift(None) is None


class TestShiftDisplay:
    def test_whole_hours(self):
        assert shift_display(9, 0, 17, 0) == "9 AM – 5 PM"

    def test_with_minutes(self):
        assert shift_display(8, 30, 14, 30) == "8:30 AM – 2:30 PM"

    def test_noon(self):
        assert shift_display(12, 0, 20, 0) == "12 PM – 8 PM"

    def test_midnight_hour(self):
        assert shift_display(0, 0, 8, 0) == "12 AM – 8 AM"


class TestAutoTextColor:
    def test_dark_bg_returns_white(self):
        assert auto_text_color("#000000") == "#FFFFFF"
        assert auto_text_color("#1565C0") == "#FFFFFF"

    def test_light_bg_returns_black(self):
        assert auto_text_color("#FFFFFF") == "#000000"
        assert auto_text_color("#FFF176") == "#000000"

    def test_empty_returns_black(self):
        assert auto_text_color("") == "#000000"
        assert auto_text_color(None) == "#000000"

    def test_malformed_returns_black(self):
        assert auto_text_color("#XYZ") == "#000000"


class TestShiftSortKey:
    def test_valid_shift_sorts_first(self):
        assert shift_sort_key("9-5")[0] == 0

    def test_invalid_shift_sorts_last(self):
        assert shift_sort_key("bad")[0] == 1
        assert shift_sort_key("")[0] == 1

    def test_earlier_start_lower_key(self):
        assert shift_sort_key("7-3") < shift_sort_key("9-5")
