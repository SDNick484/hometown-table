from datetime import datetime

import pytest

import hours


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


@pytest.mark.parametrize("spec,when,expected", [
    ("Mo-Fr 11:00-21:00", "2026-11-13 20:59", True),
    ("Mo-Fr 11:00-21:00", "2026-11-13 21:00", False),
    ("Mo-Fr 11:00-21:00", "2026-11-14 12:00", False),
    ("Tu-Su 11:00-14:30,17:00-21:00", "2026-11-14 15:00", False),
    ("Tu-Su 11:00-14:30,17:00-21:00", "2026-11-14 17:00", True),
    ("Fr,Sa 17:00-01:00", "2026-11-15 00:30", True),
    ("Fr,Sa 17:00-01:00", "2026-11-15 01:00", False),
    ("Fr,Sa 17:00-01:00", "2026-11-14 23:00", True),
    ("24/7", "2026-11-14 04:00", True),
    ("Mo off; Tu-Su 10:00-20:00", "2026-11-16 12:00", False),
    ("Mo-Su 10:00-20:00; Mo off", "2026-11-16 12:00", False),
    ("Mo-Su 10:00-20:00; Mo off", "2026-11-17 12:00", True),
    ("Mo-Fr 11:00-21:00; PH off", "2026-11-13 12:00", None),
    ("sunrise-sunset", "2026-11-14 12:00", None),
    ("Mo-Fr 11:00-21:00; Dec off", "2026-11-13 12:00", None),
    ("Mo-Fr 25:00-26:00", "2026-11-13 12:00", None),
    ("Mo-Fr 11:00-11:00", "2026-11-13 12:00", None),
    ("Sa-Mo 10:00-12:00", "2026-11-16 11:00", True),
    ("10:00-12:00", "2026-11-14 11:00", True),
    (None, "2026-11-14 12:00", None),
    ("", "2026-11-14 12:00", None),
])
def test_open_at(spec, when, expected):
    assert hours.open_at(spec, dt(when)) is expected


def test_closes_at():
    assert hours.closes_at("Mo-Su 11:00-21:00", dt("2026-11-14 18:00")) == "9 pm"


def test_closes_at_variants():
    assert hours.closes_at("Mo-Su 11:00-21:30", dt("2026-11-14 18:00")) == "9:30 pm"
    assert hours.closes_at("Mo-Su 11:00-21:00", dt("2026-11-14 22:00")) is None
    assert hours.closes_at("24/7", dt("2026-11-14 22:00")) is None
    assert hours.closes_at("Sa 17:00-01:00", dt("2026-11-14 18:00")) == "1 am"
    assert hours.closes_at("Mo-Su 10:00-24:00", dt("2026-11-14 18:00")) == "12 am"
    assert hours.closes_at("Mo-Su 10:00-12:00", dt("2026-11-14 11:00")) == "12 pm"
    assert hours.closes_at(None, dt("2026-11-14 11:00")) is None
    assert hours.closes_at("PH off", dt("2026-11-14 11:00")) is None


def test_parse_splits_past_midnight():
    assert hours.parse("Fr 17:00-01:00") == [(4, 1020, 1440), (5, 0, 60)]
    assert hours.parse("Sa 17:00-01:00") == [(5, 1020, 1440), (6, 0, 60)]
    assert hours.parse("Su 17:00-01:00") == [(0, 0, 60), (6, 1020, 1440)]
    assert hours.parse("Mo 11:00-14:30,17:00-21:00") == [(0, 660, 870), (0, 1020, 1260)]
    assert len(hours.parse("24/7")) == 7
    assert hours.parse("Mo off") == []
    assert hours.parse("nonsense") is None
    assert hours.parse(None) is None
    assert hours.parse("  ") is None
