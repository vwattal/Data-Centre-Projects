"""
Shared helper utilities: work-week parsing, Excel cell colour checks,
JIRA key extraction, and current-WW calculation.
"""
from datetime import datetime, timedelta
import re


def parse_ww(ww_str) -> datetime | None:
    """Parse a work-week string (e.g. '26WW12') into a datetime."""
    if not ww_str or str(ww_str).strip() in ('', '??'):
        return None

    ww_str = str(ww_str).strip()
    match = re.match(r'(?:(\d{2}))?[Ww]{2}(\d{1,2})', ww_str, re.IGNORECASE)
    if not match:
        return None

    year_suffix = match.group(1) or '26'
    week_num    = int(match.group(2))
    year        = 2000 + int(year_suffix)

    jan4         = datetime(year, 1, 4)
    week1_start  = jan4 - timedelta(days=jan4.weekday())
    return week1_start + timedelta(weeks=week_num - 1)


def is_cell_green(cell) -> bool:
    """Return True if an openpyxl cell has a 'done' green fill.

    Handles two common Excel green styles:
      - Theme 9 (tint ≈ 0)  — applied via Excel theme colour picker
      - RGB FF00B050         — Excel's standard green fill (direct colour)
    """
    if not cell.fill or cell.fill.patternType != 'solid':
        return False
    fg = cell.fill.fgColor
    # Theme-based green (theme 9, tint ≈ 0)
    if fg.type == 'theme' and getattr(fg, 'theme', None) == 9:
        return abs(getattr(fg, 'tint', 0.0)) < 0.1
    # RGB-based green: FF00B050 (dark green) or FF92D050 (light/lime green) — both used as "done"
    if fg.type == 'rgb':
        rgb = str(getattr(fg, 'rgb', '') or '').upper()
        return rgb in ('FF00B050', 'FF92D050')
    return False


def extract_jira_keys(text: str) -> list[str]:
    """Extract VLK / SFW / CORAL / ARCFW JIRA keys from freeform text."""
    if not text:
        return []
    patterns = [r'VLK-\d+', r'SFW-\d+', r'CORAL-\d+', r'ARCFW-\d+']
    jiras = []
    for pattern in patterns:
        jiras.extend(re.findall(pattern, str(text), re.IGNORECASE))
    return list(set(jiras))


def current_ww_info() -> tuple[str, int, datetime]:
    """
    Return (current_ww_label, current_ww_int, current_ww_date) for *today*.

    current_ww_label  e.g. '26WW12'
    current_ww_int    e.g.  2612
    current_ww_date   datetime of the Monday that starts that WW
    """
    now          = datetime.now()
    jan4         = datetime(now.year, 1, 4)
    week1_start  = jan4 - timedelta(days=jan4.weekday())
    ww_num       = (now - week1_start).days // 7 + 1
    label        = f"{str(now.year)[-2:]}WW{ww_num:02d}"
    ww_int       = int(str(now.year)[-2:]) * 100 + ww_num
    return label, ww_int, parse_ww(label)
