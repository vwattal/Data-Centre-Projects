"""
CRI CCB Dashboard — /cri-ccb

Fetches strawman HSDs from query 13013902803 (server_platf.feature),
looks up their AR child records, and surfaces per-component AR status
(XeKMD, Sysman, XPUM, E2E).
"""
import json
import logging
import sqlite3
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

DB_PATH = Path(__file__).parent.parent / "cri_ccb.db"

# Component keyword → column key
COMPONENT_KEYWORDS = {
    "XeKMD":  ["xekmd", "xe-kmd", "xe_kmd", "kmd"],
    "Sysman": ["sysman"],
    "XPUM":   ["xpum", "xpu manager"],
    "E2E":    ["e2e"],
}

SIGN_OFF_VALUES = {"sign_off", "signoff", "signed_off", "sign off"}

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
            fetched_at  TEXT
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
        "SELECT hsd_id, title, status, xekmd, sysman, xpum, e2e, fetched_at "
        "FROM ccb_rows ORDER BY hsd_id"
    ).fetchall()
    conn.close()
    return [
        dict(hsd_id=r[0], title=r[1], status=r[2],
             xekmd=r[3], sysman=r[4], xpum=r[5], e2e=r[6], fetched_at=r[7])
        for r in rows
    ]


def _save_rows(rows: list[dict]) -> None:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM ccb_rows")
    for r in rows:
        conn.execute(
            "INSERT INTO ccb_rows VALUES (?,?,?,?,?,?,?,?)",
            (r["hsd_id"], r["title"], r["status"],
             r.get("xekmd"), r.get("sysman"), r.get("xpum"), r.get("e2e"),
             datetime.utcnow().isoformat())
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


# ── HSD fetch helpers ─────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.auth    = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify  = False
    return s


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
    """Fetch title + status for a single AR record."""
    try:
        resp = session.get(HSD_API.format(ar_id), timeout=20)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                return {"title": data[0].get("title", ""),
                        "status": str(data[0].get("status", "")).lower()}
    except Exception as exc:
        logger.warning("AR detail error for %s: %s", ar_id, exc)
    return None


def _component_for_ar(ar_title: str) -> str | None:
    """Return the component key matching ar_title, or None."""
    tl = ar_title.lower()
    for comp, keywords in COMPONENT_KEYWORDS.items():
        if any(kw in tl for kw in keywords):
            return comp
    return None


def _ar_value(ar_status: str) -> str:
    """Map raw AR status → display value."""
    if ar_status.replace(" ", "_") in SIGN_OFF_VALUES or ar_status in SIGN_OFF_VALUES:
        return "Signed off"
    return "Pending"


# ── Main data builder ─────────────────────────────────────────────────────────

def build_ccb_data(session: requests.Session | None = None) -> list[dict]:
    """
    Fetches strawman HSDs, resolves AR children, and returns table rows.
    Rows that have no matching component AR are excluded.
    """
    if session is None:
        session = _make_session()

    # 1. Fetch query
    try:
        resp = session.get(QUERY_API.format(QUERY_ID), timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        articles = raw.get("data", raw) if isinstance(raw, dict) else raw
    except Exception as exc:
        logger.error("Failed to fetch CCB query: %s", exc)
        return []

    # 2. Filter strawman
    strawman = [
        a for a in articles
        if str(a.get("status", "")).lower() == "strawman"
    ]
    logger.info("CCB: %d strawman HSDs found", len(strawman))

    rows = []
    for hsd in strawman:
        hsd_id    = str(hsd.get("id", ""))
        hsd_title = hsd.get("title", "")

        # 3. Fetch AR children
        related = _esservice_related(session, hsd_id)
        ar_records = [r for r in related if r.get("subject") == "ar"]

        if not ar_records:
            continue

        comp_vals: dict[str, str] = {}   # e.g. {"XeKMD": "Signed off", ...}

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

        if not comp_vals:
            continue   # No matching component AR → skip

        rows.append({
            "hsd_id": hsd_id,
            "title":  hsd_title,
            "status": "Strawman",
            "xekmd":  comp_vals.get("XeKMD"),
            "sysman": comp_vals.get("Sysman"),
            "xpum":   comp_vals.get("XPUM"),
            "e2e":    comp_vals.get("E2E"),
        })

    logger.info("CCB: %d rows with matching component ARs", len(rows))
    return rows


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/cri-ccb")
def cri_ccb():
    rows   = _cache_rows()
    stale  = _is_stale()
    last   = _last_refresh()
    last_fmt = ""
    if last:
        try:
            dt = datetime.fromisoformat(last)
            last_fmt = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            last_fmt = last
    return render_template(
        "cri_ccb.html",
        rows=rows,
        hsd_link_base=HSD_LINK_BASE,
        last_refresh=last_fmt,
        is_stale=stale,
    )


@bp.route("/cri-ccb/refresh", methods=["POST"])
def cri_ccb_refresh():
    """Force a live re-fetch and update the cache."""
    try:
        rows = build_ccb_data()
        _save_rows(rows)
        return jsonify({"status": "ok", "count": len(rows)})
    except Exception as exc:
        logger.error("CCB refresh error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
