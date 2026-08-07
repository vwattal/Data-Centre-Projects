"""
CRI CCB KMD Status — /cri-ccb-kmd

KMD-only view of CRI CCB HSDs in Open / Strawman / POR / POR-1 states.
Columns: HSD ID, Title, Status, XeKMD (Pending/Won't Do/Sign Off),
         Impact outside core KMD (editable, shared), Scoping plan ETA (editable, shared),
         Effort estimate (editable, shared), Jira (from AR tag field).
"""
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3
from flask import Blueprint, jsonify, render_template, request
from requests_kerberos import HTTPKerberosAuth, OPTIONAL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
bp = Blueprint("cri_ccb_kmd", __name__)

# ── Constants ─────────────────────────────────────────────────────────────────
QUERY_ID      = "13013902803"
HSD_API       = "https://hsdes-api.intel.com/rest/article/{}"
QUERY_API     = "https://hsdes.intel.com/rest/query/{}"
ESSERVICE_URL = "https://hsdes.intel.com/ws/ESService"
HSD_LINK_BASE = "https://hsdes.intel.com/appstore/article-one/#/article/{}"
JIRA_BASE     = "https://jira.devtools.intel.com/browse/{}"
CACHE_TTL_MIN = 30
QUERY_PAGE_SIZE = 100

KMD_KEYWORDS    = ["xekmd", "xe-kmd", "xe_kmd", "kmd"]
WONT_DO_VALUES  = {"wont_do", "wont do", "won't do", "won_t_do", "no_action", "wontdo"}
SIGN_OFF_VALUES = {"sign_off", "signoff", "signed_off", "sign off", "signed off"}
ALLOWED_HSD_STATES = {"open", "strawman", "por", "por1"}

DB_PATH = Path(__file__).parent.parent / "cri_ccb_kmd.db"

# ── DB ────────────────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kmd_cache (
                hsd_id      TEXT PRIMARY KEY,
                hsd_title   TEXT,
                hsd_status  TEXT,
                ar_id       TEXT,
                ar_title    TEXT,
                ar_status   TEXT,
                kmd_status  TEXT,
                kmd_jira    TEXT,
                fetched_at  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kmd_edits (
                hsd_id          TEXT PRIMARY KEY,
                impact_outside  TEXT NOT NULL DEFAULT '',
                scoping_eta     TEXT NOT NULL DEFAULT '',
                effort_estimate TEXT NOT NULL DEFAULT '',
                scoping_owner   TEXT NOT NULL DEFAULT '',
                milestone_commit TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migrate existing DBs that pre-date these columns
        for col, defval in [("scoping_owner", ""), ("milestone_commit", "")]:
            try:
                conn.execute(f"ALTER TABLE kmd_edits ADD COLUMN {col} TEXT NOT NULL DEFAULT '{defval}'")
            except Exception:
                pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kmd_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Migrate existing DBs that pre-date newly added columns
        def _ensure_column(table: str, col: str, col_def: str) -> None:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                logger.info("KMD DB migration: added %s.%s", table, col)
            except Exception:
                pass  # column already exists

        _ensure_column("kmd_cache", "hsd_title", "TEXT")
        _ensure_column("kmd_cache", "hsd_status", "TEXT")
        _ensure_column("kmd_cache", "kmd_jira", "TEXT")
        _ensure_column("kmd_cache", "fetched_at", "TEXT")
        _ensure_column("kmd_edits", "updated_at", "TEXT NOT NULL DEFAULT ''")


_init_db()

_bg_refresh_running = False


def _start_bg_refresh() -> bool:
    """Start a background refresh if not already running.
    Returns True when a new refresh thread is started.
    """
    global _bg_refresh_running
    if _bg_refresh_running:
        return False

    def _bg():
        global _bg_refresh_running
        _bg_refresh_running = True
        try:
            data = build_kmd_data()
            if data:
                _save_cache(data)
                logger.info("KMD CCB bg-refresh: %d rows", len(data))
            else:
                logger.warning("KMD CCB bg-refresh returned 0 rows")
        except Exception as exc:
            logger.error("KMD CCB bg-refresh error: %s", exc)
        finally:
            _bg_refresh_running = False

    threading.Thread(target=_bg, daemon=True).start()
    return True


def _load_cache() -> list[dict]:
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT hsd_id, hsd_title, hsd_status, ar_id, ar_title, ar_status, kmd_status, kmd_jira, fetched_at "
                "FROM kmd_cache ORDER BY hsd_id"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as exc:
        # Handle older DB schema deployments gracefully.
        logger.warning("KMD cache load failed, retrying after migration: %s", exc)
        _init_db()
        with _db() as conn:
            rows = conn.execute(
                "SELECT hsd_id, hsd_title, hsd_status, ar_id, ar_title, ar_status, kmd_status, kmd_jira, fetched_at "
                "FROM kmd_cache ORDER BY hsd_id"
            ).fetchall()
        return [dict(r) for r in rows]


def _save_cache(rows: list[dict]) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM kmd_cache")
        for r in rows:
            conn.execute(
                "INSERT INTO kmd_cache (hsd_id, hsd_title, hsd_status, ar_id, ar_title, ar_status, kmd_status, kmd_jira, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["hsd_id"], r["hsd_title"], r.get("hsd_status"), r.get("ar_id"), r.get("ar_title"),
                 r.get("ar_status"), r.get("kmd_status"), r.get("kmd_jira"),
                 datetime.utcnow().isoformat())
            )
        conn.execute(
            "INSERT OR REPLACE INTO kmd_meta VALUES ('last_refresh', ?)",
            (datetime.utcnow().isoformat(),)
        )


def _load_edits() -> dict:
    """Return {hsd_id: {impact_outside, scoping_eta, effort_estimate, scoping_owner, milestone_commit}}."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT hsd_id, impact_outside, scoping_eta, effort_estimate, scoping_owner, milestone_commit FROM kmd_edits"
        ).fetchall()
    return {r["hsd_id"]: dict(r) for r in rows}


def _save_edit(hsd_id: str, field: str, value: str) -> None:
    allowed = {"impact_outside", "scoping_eta", "effort_estimate", "scoping_owner", "milestone_commit"}
    if field not in allowed:
        raise ValueError(f"Unknown field: {field}")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with _db() as conn:
        conn.execute(
            f"""INSERT INTO kmd_edits (hsd_id, {field}, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(hsd_id) DO UPDATE SET {field}=excluded.{field},
                updated_at=excluded.updated_at""",
            (hsd_id, value, now)
        )


def _last_refresh() -> str | None:
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT value FROM kmd_meta WHERE key='last_refresh'"
            ).fetchone()
        return row["value"] if row else None
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
    s.auth   = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify = False
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
            raise RuntimeError(f"Unexpected KMD query response shape: {type(raw).__name__}")

        articles.extend(batch)
        logger.info("KMD query page: fetched %d rows (total so far %d)", len(batch), len(articles))

        if not batch:
            break
        if total is not None and len(articles) >= int(total):
            break
        if len(batch) < QUERY_PAGE_SIZE:
            break

        start_at += len(batch)

    return articles


def _esservice_related(session: requests.Session, hsd_id: str) -> list[dict]:
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
            "var_args": [], "copy_args": [],
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
        resp = session.post(ESSERVICE_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("responses", [{}])[0].get("result_table", [])
    except Exception as exc:
        logger.warning("ESService error for %s: %s", hsd_id, exc)
    return []


def _ar_detail(session: requests.Session, ar_id: str) -> dict | None:
    """Fetch title, status, and tag for a single AR record."""
    try:
        resp = session.get(HSD_API.format(ar_id), timeout=20)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                record = data[0]
                # tag may be under 'tag', 'server_platf.ar.tag', or similar
                tag = (record.get("tag")
                       or record.get("server_platf.ar.tag")
                       or record.get("tags", "")
                       or "")
                return {
                    "title":  record.get("title", ""),
                    "status": str(record.get("status", "")).lower().strip(),
                    "tag":    str(tag).strip(),
                }
    except Exception as exc:
        logger.warning("AR detail error for %s: %s", ar_id, exc)
    return None


def _is_kmd_ar(ar_title: str) -> bool:
    tl = ar_title.lower()
    return any(kw in tl for kw in KMD_KEYWORDS)


def _normalize_status_key(status: str) -> str:
    s = str(status or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


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


def _is_ar_subject(subject: str) -> bool:
    return str(subject or "").strip().lower() == "ar"


def _kmd_status(ar_status: str) -> str:
    s = ar_status.lower().replace("-", "_").replace(" ", "_")
    if s in {v.replace(" ", "_").replace("'", "") for v in WONT_DO_VALUES}:
        return "Won't Do"
    if s in {v.replace(" ", "_").replace("'", "") for v in SIGN_OFF_VALUES}:
        return "Sign Off"
    return "Pending"


def _extract_jira(tag: str) -> str | None:
    """Pull the first Jira-style key (ABC-1234) from a tag string."""
    import re
    m = re.search(r'[A-Z][A-Z0-9]+-\d+', tag or "")
    return m.group(0) if m else None


# ── Main data builder ─────────────────────────────────────────────────────────

def build_kmd_data(session: requests.Session | None = None) -> list[dict]:
    if session is None:
        session = _make_session()

    try:
        articles = _fetch_query_articles(session)
    except Exception as exc:
        logger.error("Failed to fetch CCB query: %s", exc)
        raise RuntimeError(f"Failed to fetch CCB query: {exc}") from exc

    target_hsds = [a for a in articles if _is_target_hsd_status(a.get("status", ""))]
    logger.info("KMD CCB: %d Open/Strawman/POR/POR-1 HSDs", len(target_hsds))

    rows = []
    for hsd in target_hsds:
        hsd_id    = str(hsd.get("id", ""))
        hsd_title = hsd.get("title", "")
        hsd_status = _display_hsd_status(hsd.get("status", ""))

        # Get related stubs — titles are already in the response, no extra call needed
        related = _esservice_related(session, hsd_id)

        ar_stubs = [r for r in related if _is_ar_subject(r.get("subject"))]
        if not ar_stubs:
            continue

        chosen = None
        for stub in ar_stubs:
            ar_id = str(stub.get("id", ""))
            if not ar_id:
                continue

            stub_title = str(stub.get("title") or "").strip()
            if stub_title and _is_kmd_ar(stub_title):
                detail = _ar_detail(session, ar_id)
                chosen = (stub, detail)
                break

            # Some related stubs do not include a useful AR title; fallback to AR detail title.
            detail = _ar_detail(session, ar_id)
            if detail and _is_kmd_ar(detail.get("title", "")):
                chosen = (stub, detail)
                break

        if not chosen:
            continue

        stub, detail = chosen
        ar_id = str(stub.get("id", ""))
        ar_title = (detail or {}).get("title") or stub.get("title", "")

        if detail:
            ar_status = detail["status"]
            kmd_jira = _extract_jira(detail.get("tag", ""))
        else:
            ar_status = str(stub.get("status", "")).lower().strip()
            kmd_jira = _extract_jira(str(stub.get("tag") or ""))

        rows.append({
            "hsd_id":     hsd_id,
            "hsd_title":  hsd_title,
            "hsd_status": hsd_status,
            "ar_id":      ar_id,
            "ar_title":   ar_title,
            "ar_status":  ar_status,
            "kmd_status": _kmd_status(ar_status),
            "kmd_jira":   kmd_jira,
        })

    logger.info("KMD CCB: %d rows with KMD ARs", len(rows))
    return rows


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/cri-ccb-kmd")
def cri_ccb_kmd():
    rows  = _load_cache()
    stale = _is_stale()
    no_cache = not rows   # True only when there is literally nothing to show

    # Auto-trigger a background live fetch if cache is empty or stale
    if not rows or stale:
        _start_bg_refresh()

    edits = _load_edits()

    # Keep saved edits visible even when their HSD is missing from live/cache rows.
    existing_hsd_ids = {str(r.get("hsd_id", "")) for r in rows}
    for hsd_id in sorted(edits.keys()):
        if hsd_id in existing_hsd_ids:
            continue
        rows.append({
            "hsd_id": hsd_id,
            "hsd_title": "(saved local entry; HSD not found in current live Open/Strawman/POR/POR-1 KMD set)",
            "hsd_status": "Local",
            "ar_id": "",
            "ar_title": "",
            "ar_status": "",
            "kmd_status": "N/A",
            "kmd_jira": None,
            "local_only": True,
        })

    for r in rows:
        r["hsd_status"] = _display_hsd_status(r.get("hsd_status", ""))
        ed = edits.get(r["hsd_id"], {})
        r["impact_outside"]   = ed.get("impact_outside",   "")
        r["scoping_eta"]      = ed.get("scoping_eta",      "")
        r["effort_estimate"]  = ed.get("effort_estimate",  "")
        r["scoping_owner"]    = ed.get("scoping_owner",    "")
        r["milestone_commit"] = ed.get("milestone_commit", "")

    last = _last_refresh()
    last_fmt = ""
    if last:
        try:
            last_fmt = datetime.fromisoformat(last).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            last_fmt = last

    return render_template(
        "cri_ccb_kmd.html",
        rows=rows,
        hsd_link_base=HSD_LINK_BASE,
        jira_base=JIRA_BASE,
        last_refresh=last_fmt,
        is_stale=stale,
        is_loading=_bg_refresh_running,
        no_cache=no_cache,
    )


@bp.route("/cri-ccb-kmd/edits-ts")
def cri_ccb_kmd_edits_ts():
    """Return the latest kmd_edits updated_at for polling."""
    with _db() as conn:
        row = conn.execute(
            "SELECT updated_at FROM kmd_edits ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return jsonify({"ts": row["updated_at"] if row else None})


@bp.route("/cri-ccb-kmd/refresh", methods=["POST"])
def cri_ccb_kmd_refresh():
    started = _start_bg_refresh()
    if started:
        return jsonify({"status": "ok", "queued": True, "message": "Refresh started in background"})
    return jsonify({"status": "ok", "queued": False, "message": "Refresh already running"})


@bp.route("/cri-ccb-kmd/save", methods=["POST"])
def cri_ccb_kmd_save():
    """Save an editable cell (shared across all users via SQLite)."""
    try:
        data  = request.get_json(force=True)
        hsd_id = str(data.get("hsd_id", "")).strip()
        field  = str(data.get("field",  "")).strip()
        value  = str(data.get("value",  "")).strip()
        if not hsd_id or not field:
            return jsonify({"ok": False, "error": "missing params"}), 400
        _save_edit(hsd_id, field, value)
        return jsonify({"ok": True})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("KMD CCB save error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
