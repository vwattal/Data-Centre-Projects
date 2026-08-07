"""
CRI CCB Dashboard — /cri-ccb

Fetches Open / Strawman / POR / POR-1 HSDs from query 13013902803
(server_platf.feature), looks up their AR child records, and surfaces
per-component AR status (XeKMD, Sysman, XPUM, E2E).
"""
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3
from flask import Blueprint, jsonify, render_template, request
from requests_kerberos import HTTPKerberosAuth, OPTIONAL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
bp = Blueprint("cri_ccb", __name__)

# ── Constants ─────────────────────────────────────────────────────────────────
QUERY_ID      = "13013902803"
HSD_API       = "https://hsdes-api.intel.com/rest/article/{}"
QUERY_API     = "https://hsdes.intel.com/rest/query/{}"
ESSERVICE_URL = "https://hsdes.intel.com/ws/ESService"
HSD_LINK_BASE = "https://hsdes.intel.com/appstore/article-one/#/article/{}"
CACHE_TTL_MIN = 30          # minutes before background data is considered stale
QUERY_PAGE_SIZE = 100

DB_PATH = Path(__file__).parent.parent / "cri_ccb.db"

# Component keyword → column key
COMPONENT_KEYWORDS = {
    "XeKMD":  ["xekmd", "xe-kmd", "xe_kmd", "kmd"],
    "Sysman": ["sysman"],
    "XPUM":   ["xpum", "xpu manager"],
    "E2E":    ["e2e"],
}

SIGN_OFF_VALUES = {"sign_off", "signoff", "signed_off", "sign off"}
WONT_DO_VALUES = {"wont_do", "wont do", "won't do", "won_t_do", "no_action", "wontdo"}
ALLOWED_HSD_STATES = {"open", "strawman", "por", "por1"}
SUMMARY_COMPONENTS = (
    ("KMD", "xekmd"),
    ("XPUM", "xpum"),
    ("Sysman", "sysman"),
)

_bg_refresh_running = False

# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ccb_rows (
            hsd_id      TEXT PRIMARY KEY,
            title       TEXT,
            status      TEXT,
            xekmd       TEXT,
            sysman      TEXT,
            xpum        TEXT,
            e2e         TEXT,
            fetched_at  TEXT,
            xekmd_jira  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ccb_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def _cache_rows() -> list[dict]:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT hsd_id, title, status, xekmd, sysman, xpum, e2e, fetched_at, xekmd_jira "
        "FROM ccb_rows ORDER BY hsd_id"
    ).fetchall()
    conn.close()
    return [
        dict(hsd_id=r[0], title=r[1], status=r[2],
             xekmd=r[3], sysman=r[4], xpum=r[5], e2e=r[6], fetched_at=r[7],
             xekmd_jira=r[8])
        for r in rows
    ]


def _save_rows(rows: list[dict]) -> None:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM ccb_rows")
    for r in rows:
        conn.execute(
            "INSERT INTO ccb_rows VALUES (?,?,?,?,?,?,?,?,?)",
            (r["hsd_id"], r["title"], r["status"],
             r.get("xekmd"), r.get("sysman"), r.get("xpum"), r.get("e2e"),
             datetime.utcnow().isoformat(), r.get("xekmd_jira"))
        )
    conn.execute(
        "INSERT OR REPLACE INTO ccb_meta VALUES ('last_refresh', ?)",
        (datetime.utcnow().isoformat(),)
    )
    conn.commit()
    conn.close()


def _last_refresh() -> str | None:
    try:
        _init_db()
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT value FROM ccb_meta WHERE key='last_refresh'"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _is_stale() -> bool:
    lr = _last_refresh()
    if not lr:
        return True
    try:
        age = datetime.utcnow() - datetime.fromisoformat(lr)
        return age > timedelta(minutes=CACHE_TTL_MIN)
    except Exception:
        return True


def _start_bg_refresh() -> bool:
    """Start a background refresh if one is not already running."""
    global _bg_refresh_running
    if _bg_refresh_running:
        return False

    def _bg() -> None:
        global _bg_refresh_running
        _bg_refresh_running = True
        try:
            rows = build_ccb_data()
            if rows:
                _save_rows(rows)
                logger.info("CCB bg-refresh: %d rows", len(rows))
            else:
                logger.warning("CCB bg-refresh returned 0 rows; leaving cache unchanged")
        except Exception as exc:
            logger.error("CCB bg-refresh error: %s", exc)
        finally:
            _bg_refresh_running = False

    threading.Thread(target=_bg, daemon=True).start()
    return True


# ── HSD fetch helpers ─────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.auth    = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify  = False
    return s


def _fetch_query_articles(session: requests.Session) -> list[dict]:
    articles: list[dict] = []
    start_at = 1

    while True:
        resp = session.get(
            QUERY_API.format(QUERY_ID),
            params={"start_at": start_at, "max_results": QUERY_PAGE_SIZE},
            timeout=30,
        )
        resp.raise_for_status()

        raw = resp.json()
        if isinstance(raw, dict):
            batch = raw.get("data", [])
            total = raw.get("total")
        else:
            batch = raw
            total = None

        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected CCB query response shape: {type(raw).__name__}")

        articles.extend(batch)
        logger.info("CCB query page: fetched %d rows (total so far %d)", len(batch), len(articles))

        if not batch:
            break
        if total is not None and len(articles) >= int(total):
            break
        if len(batch) < QUERY_PAGE_SIZE:
            break

        start_at += len(batch)

    return articles


def _esservice_related(session: requests.Session, hsd_id: str) -> list[dict]:
    """Return list of child records (any subject) for hsd_id."""
    payload = {
        "requests": [{
            "api_client":   "HSD-ES Article",
            "tran_id":      str(uuid.uuid4()).upper(),
            "command":      "get_related_records",
            "command_args": {
                "id":      hsd_id,
                "tenant":  "server_platf",
                "subject": "feature",
            },
            "var_args":  [],
            "copy_args": [],
        }]
    }
    headers = {
        "APP":          "HSD-ES Article",
        "Accept":       "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin":       "https://hsdes.intel.com",
        "Referer":      "https://hsdes.intel.com/appstore/article-one/",
    }
    try:
        resp = session.post(ESSERVICE_URL, headers=headers,
                            json=payload, timeout=30)
        if resp.status_code == 200:
            return (resp.json()
                        .get("responses", [{}])[0]
                        .get("result_table", []))
    except Exception as exc:
        logger.warning("ESService error for %s: %s", hsd_id, exc)
    return []


def _ar_detail(session: requests.Session, ar_id: str) -> dict | None:
    """Fetch title + status + tag for a single AR record."""
    try:
        resp = session.get(HSD_API.format(ar_id), timeout=20)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                return {"title":  data[0].get("title", ""),
                        "status": str(data[0].get("status", "")).lower(),
                        "tag":    data[0].get("tag", "") or ""}
    except Exception as exc:
        logger.warning("AR detail error for %s: %s", ar_id, exc)
    return None


def _extract_jira(tag: str) -> str | None:
    """Pull the first Jira-style key (ABC-1234) from a tag string."""
    import re
    m = re.search(r'[A-Z][A-Z0-9]+-\d+', tag or "")
    return m.group(0) if m else None


def _normalize_status_key(status: str) -> str:
    return "".join(ch for ch in str(status or "").strip().lower() if ch.isalnum())


def _is_target_hsd_status(status: str) -> bool:
    return _normalize_status_key(status) in ALLOWED_HSD_STATES


def _display_hsd_status(status: str) -> str:
    key = _normalize_status_key(status)
    if key == "por1":
        return "POR-1"
    if key == "por":
        return "POR"
    if key == "open":
        return "Open"
    if key == "strawman":
        return "Strawman"
    return str(status or "Unknown").strip() or "Unknown"


def _component_for_ar(ar_title: str) -> str | None:
    """Return the component key matching ar_title, or None."""
    tl = ar_title.lower()
    for comp, keywords in COMPONENT_KEYWORDS.items():
        if any(kw in tl for kw in keywords):
            return comp
    return None


def _ar_value(ar_status: str) -> str:
    """Map raw AR status → display value."""
    normalized = _normalize_status_key(ar_status)
    if normalized in {_normalize_status_key(v) for v in SIGN_OFF_VALUES}:
        return "Signed off"
    if normalized in {_normalize_status_key(v) for v in WONT_DO_VALUES}:
        return "Won't Do"
    return "Pending"


def _build_component_summary(rows: list[dict]) -> list[dict]:
    summary = []
    for label, key in SUMMARY_COMPONENTS:
        component_rows = [r for r in rows if r.get(key)]
        values = [r.get(key) for r in component_rows]
        summary.append({
            "component": label,
            "total": len(values),
            "open": sum(1 for row in component_rows if row.get("status") == "Open"),
            "scoped": sum(1 for value in values if value == "Signed off"),
            "wont_do": sum(1 for value in values if value == "Won't Do"),
            "pending": sum(1 for value in values if value == "Pending"),
        })
    return summary


# ── Main data builder ─────────────────────────────────────────────────────────

def build_ccb_data(session: requests.Session | None = None) -> list[dict]:
    """
    Fetches Open / Strawman / POR / POR-1 HSDs, resolves AR children,
    and returns table rows.
    HSDs remain visible even when no matching component AR is found.
    """
    if session is None:
        session = _make_session()

    # 1. Fetch query
    try:
        articles = _fetch_query_articles(session)
    except Exception as exc:
        logger.error("Failed to fetch CCB query: %s", exc)
        raise RuntimeError(f"Failed to fetch CCB query: {exc}") from exc

    # 2. Filter target HSD states
    target_hsds = [a for a in articles if _is_target_hsd_status(a.get("status", ""))]
    logger.info("CCB: %d Open/Strawman/POR/POR-1 HSDs found", len(target_hsds))

    rows = []
    for hsd in target_hsds:
        hsd_id    = str(hsd.get("id", ""))
        hsd_title = hsd.get("title", "")
        hsd_status = _display_hsd_status(hsd.get("status", ""))

        # 3. Fetch AR children
        related = _esservice_related(session, hsd_id)
        ar_records = [r for r in related if r.get("subject") == "ar"]

        comp_vals: dict[str, str] = {}
        comp_jiras: dict[str, str] = {}

        for ar_stub in ar_records:
            ar_id  = str(ar_stub.get("id", ""))
            detail = _ar_detail(session, ar_id)
            if not detail:
                continue
            comp = _component_for_ar(detail["title"])
            if not comp:
                continue
            val = _ar_value(detail["status"])
            # If two ARs map to the same component, Signed off wins over Pending
            existing = comp_vals.get(comp)
            if existing is None or (existing == "Pending" and val == "Signed off"):
                comp_vals[comp] = val
                jira = _extract_jira(detail.get("tag", ""))
                if jira:
                    comp_jiras[comp] = jira

        rows.append({
            "hsd_id":     hsd_id,
            "title":      hsd_title,
            "status":     hsd_status,
            "xekmd":      comp_vals.get("XeKMD"),
            "sysman":     comp_vals.get("Sysman"),
            "xpum":       comp_vals.get("XPUM"),
            "e2e":        comp_vals.get("E2E"),
            "xekmd_jira": comp_jiras.get("XeKMD"),
        })

    logger.info("CCB: %d rows in target HSD states", len(rows))
    return rows


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/cri-ccb")
def cri_ccb():
    rows   = _cache_rows()
    stale  = _is_stale()

    if not rows or stale:
        _start_bg_refresh()

    last   = _last_refresh()
    last_fmt = ""
    if last:
        try:
            dt = datetime.fromisoformat(last)
            last_fmt = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            last_fmt = last
    component_summary = _build_component_summary(rows)
    return render_template(
        "cri_ccb.html",
        rows=rows,
        component_summary=component_summary,
        hsd_link_base=HSD_LINK_BASE,
        last_refresh=last_fmt,
        is_stale=stale,
        is_loading=_bg_refresh_running,
    )


@bp.route("/cri-ccb/refresh-status")
def cri_ccb_refresh_status():
    return jsonify({
        "is_running": _bg_refresh_running,
        "last_refresh": _last_refresh(),
        "row_count": len(_cache_rows()),
    })


@bp.route("/cri-ccb/refresh", methods=["POST"])
def cri_ccb_refresh():
    """Force a live re-fetch and update the cache."""
    started = _start_bg_refresh()
    if started:
        return jsonify({"status": "ok", "queued": True, "message": "Refresh started in background"})
    return jsonify({"status": "ok", "queued": False, "message": "Refresh already running"})
