"""Opening-hours parser for the common `opening_hours` subset (spec 7.2).

Supported: day ranges and lists (`Mo-Fr`, `Sa,Su`), several time ranges (`11:00-14:30,17:00-21:00`),
times past midnight (`17:00-01:00`), `off`, `24/7`, and rules separated by `;`. A later rule replaces
the earlier ones for the days it names. Anything else (holidays, months, week numbers, sunrise) makes
the whole spec unknown: `parse` returns None and the other functions return None.
`when` is a naive datetime already in America/Los_Angeles local time.
"""
import re

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME = r"(\d{1,2}):(\d{2})"
_RANGE_RE = re.compile(rf"^{_TIME}-{_TIME}$")
_DAY_RE = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")


def _minutes(h, m):
    h, m = int(h), int(m)
    if m > 59 or h > 24 or (h == 24 and m):
        return None
    return h * 60 + m


def _days(token):
    out = set()
    for part in token.split(","):
        m = _DAY_RE.match(part)
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        d = a
        while True:
            out.add(d)
            if d == b:
                break
            d = (d + 1) % 7
    return out


def _ranges(token):
    out = []
    for part in token.split(","):
        m = _RANGE_RE.match(part)
        if not m:
            return None
        start, end = _minutes(*m.group(1, 2)), _minutes(*m.group(3, 4))
        if start is None or end is None or start == end:
            return None
        out.append((start, end))
    return out


def _parse_rule(rule, own):
    """Apply one rule to `own` (weekday -> [(start, end)], end may pass 1440). False if unsupported."""
    rule = " ".join(rule.split())
    if rule == "24/7":
        for d in range(7):
            own[d] = [(0, 1440)]
        return True
    days, _, rest = rule.partition(" ")
    if not rest or not days[0].isalpha():  # no day selector: "11:00-21:00" means every day
        days, rest = "Mo-Su", rule
    selected = _days(days)
    if selected is None:
        return False
    if rest == "off":
        for d in selected:
            own[d] = []
        return True
    ranges = _ranges(rest)
    if ranges is None:
        return False
    for d in selected:
        own[d] = list(ranges)
    return True


def parse(spec):
    """The spec as sorted (weekday 0=Mo, start minute, end minute) tuples; ranges past midnight are
    split onto the next day. None when the spec is empty or outside the supported subset."""
    if not spec or not spec.strip():
        return None
    own = {d: [] for d in range(7)}
    rules = [r for r in spec.split(";") if r.strip()]
    if not rules:
        return None
    for rule in rules:
        if not _parse_rule(rule.strip(), own):
            return None
    out = []
    for d, ranges in own.items():
        for start, end in ranges:
            if end <= start:  # past midnight
                out.append((d, start, 1440))
                out.append(((d + 1) % 7, 0, end))
            else:
                out.append((d, start, end))
    return sorted(out)


def open_at(spec, when):
    """True or False, or None when the hours are unknown or unparseable."""
    parsed = parse(spec)
    if parsed is None:
        return None
    wd, minute = when.weekday(), when.hour * 60 + when.minute
    return any(d == wd and s <= minute < e for d, s, e in parsed)


def _fmt(minute):
    h, m = divmod(minute % 1440, 60)
    h12 = h % 12 or 12
    suffix = "am" if h < 12 else "pm"
    return f"{h12} {suffix}" if not m else f"{h12}:{m:02d} {suffix}"


def closes_at(spec, when):
    """When the place closes if it is open at `when` ("9 pm", "9:30 pm"); else None."""
    parsed = parse(spec)
    if parsed is None or not open_at(spec, when):
        return None
    wd, minute = when.weekday(), when.hour * 60 + when.minute
    for _ in range(8):  # follow back-to-back ranges across midnight; give up after a week
        end = next((e for d, s, e in parsed if d == wd and s <= minute < e), None)
        if end is None:
            return None
        nxt = (wd + 1) % 7
        if end < 1440 or not any(d == nxt and s == 0 for d, s, e in parsed):
            return _fmt(end)
        wd, minute = nxt, 0
    return None
