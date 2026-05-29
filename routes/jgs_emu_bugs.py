"""
routes/jgs_emu_bugs.py
======================
Flask Blueprint for the JGS Emulation Bugs Dashboard (/jgs-emu-bugs).

Data flow
---------
1.  Kerberos-authenticated REST call to HSD query 14027480453
      GET https://hsdes.intel.com/rest/query/<QUERY_ID>
      → list of article dicts (id, title, priority, status, …)
2.  Per-article enrichment
      GET https://hsdes.intel.com/rest/article/<id>
      → full tenant fields (tags, root cause, closed_date, …)
3.  Per-article component history
      GET https://hsdes.intel.com/rest/article/<id>/history
      → builds a component-change "trail" to show how ownership moved
4.  Results are cached in memory for CACHE_TTL seconds.

Routes
------
GET  /jgs-emu-bugs              Main dashboard (renders jgs_emu_bugs.html)
POST /jgs-emu-bugs/refresh      Force-invalidate cache and re-fetch
GET  /jgs-emu-bugs/debug        JSON dump of first article for debugging
GET  /jgs-emu-bugs/teams-webhook   Return current Power Automate URL (masked)
POST /jgs-emu-bugs/teams-webhook   Save / clear the Power Automate URL
POST /jgs-emu-bugs/notify-mention  Send a Teams DM to each @mentioned user

Teams DM (@mention) flow
------------------------
When a user types @username in a Comments cell and clicks away, the
frontend JS calls POST /jgs-emu-bugs/notify-mention with:
  { bug_id, bug_title, note, mentions: ["username1", ...] }

The server POSTs once per mentioned user to the Power Automate HTTP-trigger
URL stored in webhook_config.json:
  { "to": "username@intel.com", "bug_id": "...", "bug_title": "...",
    "note": "...", "hsd_url": "...", "dash_url": "..." }

The Power Automate flow (configured by the user once at flow.microsoft.com)
sends a native 1-on-1 Teams DM from the user's account.

Config file
-----------
webhook_config.json  (same directory as this file) — stores the Power
Automate URL. A one-time migration from the parent app's user_data.json
runs on first import.
"""

import time
import logging
import json
from datetime import datetime
from html import unescape
from pathlib import Path
import re

import requests
import urllib3
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
from flask import Blueprint, render_template, jsonify, request

urllib3.disable_warnings()
logger = logging.getLogger(__name__)

# Blueprint registers routes under /jgs-emu-bugs
bp = Blueprint('jgs_emu_bugs', __name__)

# ── Config file (webhook URL lives here, not in the parent app's user_data.json)
_PKG_DIR     = Path(__file__).parent
_CONFIG_FILE = _PKG_DIR / 'webhook_config.json'

# ── One-time migration: if teams_webhook_url exists in the parent app's
#    user_data.json, pull it into our own config file and remove it there.
def _migrate_webhook_url():
    if _CONFIG_FILE.exists():
        return
    cfg = {}
    parent_ud = _PKG_DIR.parent / 'user_data.json'
    if parent_ud.exists():
        try:
            ud = json.loads(parent_ud.read_text())
            if ud.get('teams_webhook_url'):
                cfg['teams_webhook_url'] = ud.pop('teams_webhook_url')
                parent_ud.write_text(json.dumps(ud, indent=2))
                logger.info('Migrated teams_webhook_url to routes/webhook_config.json')
        except Exception as exc:
            logger.warning('Webhook URL migration failed: %s', exc)  # noqa: F841
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

_migrate_webhook_url()


def _get_teams_webhook() -> str:
    try:
        return json.loads(_CONFIG_FILE.read_text()).get('teams_webhook_url', '')
    except Exception:
        return ''


def _get_extra_ids() -> list[str]:
    """Return any article IDs that should always appear, regardless of query results."""
    try:
        raw = json.loads(_CONFIG_FILE.read_text()).get('extra_article_ids', [])
        return [str(i).strip() for i in raw if str(i).strip().isdigit()]
    except Exception:
        return []


# ── Constants ─────────────────────────────────────────────────────────────────
QUERY_ID        = '14027480453'
HSD_BASE        = 'https://hsdes.intel.com'
HSD_ARTICLE_URL = HSD_BASE + '/appstore/article/#/'
ESCALATE_DAYS   = 14          # weeks before SW Arch escalation button lights up
CACHE_TTL       = 300         # seconds between live HSD re-fetches
DASH_URL        = 'http://10.88.27.190:8888/jgs-emu-bugs'

_PROXIES = {
    'http':  'http://proxy-chain.intel.com:911',
    'https': 'http://proxy-chain.intel.com:912',
}

# ── Component mapping ─────────────────────────────────────────────────────────
# First matching rule wins as the primary component label.
_COMP_RULES: list[tuple[str, str]] = [
    ('emulation',       'Emulation Model'),
    ('fulsim',          'XeSim'),
    ('val_only',        'Val Team'),
    ('linux_kmd',       'KMD'),
    ('igc_compute',     'IGC'),
    ('pisa_finalizer',  'PISA'),
    ('pisa_finaliser',  'PISA'),
    ('compute_test',    'Test'),
    ('test.driver',     'Test'),
    ('compute_umd',     'Compute Dev'),
    ('coral',           'Coral'),
]

_EMU_CACHE: dict = {'data': None, 'ts': 0}


# ─────────────────────────────────────────────────────────────────────────────
# HSD HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _new_session() -> requests.Session:
    s = requests.Session()
    s.auth   = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify = False
    return s


# Keys that HSD REST might use for the articles list in a query response
_QUERY_LIST_KEYS = ('data', 'articles', 'results', 'items')


def _extract_batch(data) -> list[dict] | None:
    """Return the list of article dicts from a REST query response payload."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in _QUERY_LIST_KEYS:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return None


def _safe_article_id(raw) -> str:
    """Normalise an article ID that may arrive as int, float, or string."""
    if raw is None:
        return ''
    s = str(raw).strip()
    # Handle scientific / float notation: '1.8044355233e10' → '18044355233'
    try:
        s = str(int(float(s)))
    except (ValueError, TypeError):
        pass
    return s if s.isdigit() else ''


def _rest_query(session: requests.Session) -> list[dict]:
    all_articles: list[dict] = []
    total_reported: int | None = None
    start = 0
    page  = 100
    MAX_PAGES = 50  # safety guard – 50 × 100 = 5 000 articles

    for _page_num in range(MAX_PAGES):
        url    = f'{HSD_BASE}/rest/query/{QUERY_ID}'
        params = {'start_at': start, 'count': page}
        try:
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code != 200:
                logger.error('REST query HTTP %s: %s', resp.status_code, resp.text[:300])
                break
            data = resp.json()
            if isinstance(data, dict) and total_reported is None:
                try:
                    total_reported = int(data.get('total', 0)) or None
                except (TypeError, ValueError):
                    pass
            batch = _extract_batch(data)
            if batch is None:
                logger.error('Unexpected REST query shape (no list found): %s', str(data)[:300])
                break
            all_articles.extend(batch)
            logger.debug('REST query page start=%d got %d articles', start, len(batch))
            if not batch:
                break
            start += page
        except Exception as exc:
            logger.error('REST query failed at start=%d: %s', start, exc)
            break

    if total_reported is not None and len(all_articles) != total_reported:
        logger.warning(
            'REST query total mismatch: HSD reported %d but we fetched %d',
            total_reported, len(all_articles),
        )
    logger.info('REST query fetched %d articles (HSD total=%s)', len(all_articles), total_reported)
    return all_articles


def _rest_article(session: requests.Session, article_id: str) -> dict:
    url = f'{HSD_BASE}/rest/article/{article_id}'
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 200:
            data  = resp.json()
            items = data.get('data', []) if isinstance(data, dict) else []
            return items[0] if items else {}
    except Exception as exc:
        logger.debug('Full article fetch for %s: %s', article_id, exc)
    return {}


def _rest_history(session: requests.Session, article_id: str) -> list[dict]:
    url = f'{HSD_BASE}/rest/article/{article_id}/history'
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get('data', [])
            return sorted(items, key=lambda e: int(e.get('rev', 0)))
    except Exception as exc:
        logger.debug('History fetch for %s: %s', article_id, exc)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

_DATE_FMTS = (
    ('%Y-%m-%d %H:%M:%S',  19),
    ('%Y-%m-%dT%H:%M:%S',  19),
    ('%Y-%m-%d %H:%M',     16),
    ('%Y-%m-%d',           10),
    ('%m/%d/%Y %H:%M:%S',  19),
    ('%m/%d/%Y',           10),
)


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt, length in _DATE_FMTS:
        try:
            return datetime.strptime(s[:length], fmt)
        except (ValueError, TypeError):
            pass
    return None


def _strip_html(text: str) -> str:
    if not text:
        return ''
    clean = re.sub(r'<[^>]+>', ' ', str(text))
    clean = unescape(clean)
    return ' '.join(clean.split())


def _parse_comments_str(raw: str) -> list[dict]:
    if not raw:
        return []
    blocks = re.split(r'\n?\+\+\+\+', str(raw))
    result = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines  = block.split('\n', 1)
        header = lines[0].strip()
        text   = lines[1].strip() if len(lines) > 1 else ''
        # HSD comment header: "{rev} {username} {YYYY-MM-DD HH:MM:SS}" (date may be absent)
        m = re.match(
            r'\d+\s+(\S+)\s+(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?)',
            header
        )
        if m:
            username = m.group(1)
            date_str = m.group(2).strip()
        else:
            m2 = re.match(r'\d+\s+(\S+)', header)
            username = m2.group(1) if m2 else (header.split()[-1] if header.split() else '?')
            date_str = ''
        result.append({'username': username, 'text': text, 'date': date_str})
    return result


def _priority_rank(pri: str) -> int:
    p = str(pri).lower().strip()
    for val, rank in (
        ('showstopper', 1), ('critical', 1), ('1-', 1), ('blocker', 1),
        ('2-', 2), ('high', 2),
        ('3-', 3), ('medium', 3),
        ('4-', 4), ('low', 4),
    ):
        if val in p:
            return rank
    return 5


def _priority_label(pri: str) -> str:
    rank = _priority_rank(pri)
    return {1: 'P1 – Showstopper', 2: 'P2 – High',
            3: 'P3 – Medium',      4: 'P4 – Low'}.get(rank, pri or 'Unknown')


def _is_closed(bug: dict) -> bool:
    s = (bug.get('status') or '').lower()
    return 'complet' in s or 'reject' in s


def _is_rejected(bug: dict) -> bool:
    s = (bug.get('status') or '').lower()
    return 'reject' in s


def _is_jgs_critical(rec: dict) -> bool:
    for field in ('tag', 'tags', 'ip_hw_graphics.bugeco.tag'):
        raw = str(rec.get(field, '')).lower()
        if raw and ('jgs_critical' in raw or 'jgs critical' in raw):
            return True
    return False


def _comp_label(raw: str) -> str:
    r = (raw or '').lower().strip()
    if not r or r in ('none', 'null'):
        return 'Other'
    for pattern, label in _COMP_RULES:
        if pattern in r:
            return label
    last = r.rsplit('.', 1)[-1]
    return last.replace('_', ' ').title() if last else 'Other'


def _extract_comp_trail(history: list[dict]) -> list[str]:
    trail: list[str] = []
    for entry in history:
        raw = str(entry.get('component') or '').strip()
        if not raw or raw in ('None', 'null'):
            continue
        if len(raw.split('.')) < 3:
            continue
        label = _comp_label(raw)
        if not trail or trail[-1] != label:
            trail.append(label)
    return trail or ['Other']


def _extract_closed_date(full_art: dict, updated_raw: str) -> str:
    d = str(full_art.get('closed_date') or '').strip()
    if d and d not in ('None', 'null', ''):
        return d[:10]
    d = str(full_art.get('ip_hw_graphics.bugeco.fixed_date') or '').strip()
    if d and d not in ('None', 'null', ''):
        return d[:10]
    return updated_raw[:10] if updated_raw else '—'


_RE_COMMENT_INSERT = re.compile(r'^comments\s+\S+\s+insert$', re.IGNORECASE)


def _last_comment_date_from_history(history: list[dict]) -> str:
    """Scan history in reverse and return the updated_date of the last comment-insert entry."""
    for entry in reversed(history):
        reason = str(entry.get('updated_reason') or '').strip()
        if _RE_COMMENT_INSERT.match(reason):
            d = str(entry.get('updated_date') or '').strip()
            if d and d not in ('None', 'null', ''):
                return d[:16]   # 'YYYY-MM-DD HH:MM'
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# Core data fetch  (in-memory cache, rehydrated on first request or after TTL)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_bugs(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _EMU_CACHE['data'] is not None and now - _EMU_CACHE['ts'] < CACHE_TTL:
        return _EMU_CACHE['data']

    session      = _new_session()
    raw_articles = _rest_query(session)

    if not raw_articles:
        logger.error('REST query returned 0 articles for query %s', QUERY_ID)
        return _EMU_CACHE.get('data') or []

def _build_bug_record(session: requests.Session, article_id: str, art: dict) -> dict | None:
    """Fetch full details for one article and return a normalised bug dict."""
    full = _rest_article(session, article_id)

    def _g(*keys):
        for k in keys:
            v = full.get(k) or art.get(k)
            if v is not None and str(v).strip() not in ('', 'None', 'null'):
                return str(v).strip()
        return ''

    title       = _g('title')
    status      = _g('status')
    priority    = _g('priority')
    owner       = _g('owner', 'assigned_to', 'submitted_by')
    filed_raw   = _g('submitted_date', 'bugeco.open_date')
    updated_raw = _g('updated_date')
    root_cause  = _g('bugeco.root_cause', 'ip_hw_graphics.bugeco.root_cause')
    single_comp = _g('component')

    history    = _rest_history(session, article_id)
    comp_trail = _extract_comp_trail(history) if history else [_comp_label(single_comp)]

    parsed_cmts = _parse_comments_str(_g('comments'))
    if parsed_cmts:
        lc = parsed_cmts[-1]
        last_comment_author = lc['username']
        last_comment_text   = _strip_html(lc['text'])[:500]
        last_comment_date   = _last_comment_date_from_history(history)
    else:
        last_comment_author = ''
        last_comment_text   = ''
        last_comment_date   = ''

    filed_dt    = _parse_dt(filed_raw)
    filed_str   = filed_raw[:10] if filed_raw else '—'
    closed_date = _extract_closed_date(full, updated_raw)

    closed_dt = _parse_dt(closed_date) if closed_date and closed_date != '—' else None
    end_dt    = closed_dt if (('complet' in status.lower() or 'reject' in status.lower()) and closed_dt) else datetime.now()
    age_days  = (end_dt - filed_dt).days if filed_dt else 0
    age_weeks = age_days // 7

    needs_arch = age_days >= ESCALATE_DAYS and not root_cause

    return {
        'id':                  article_id,
        'title':               title,
        'filed_date':          filed_str,
        'filed_dt':            filed_dt,
        'status':              status,
        'priority':            priority,
        'priority_rank':       _priority_rank(priority),
        'priority_label':      _priority_label(priority),
        'owner':               owner,
        'root_cause':          root_cause,
        'last_comment_date':   last_comment_date,
        'last_comment_text':   last_comment_text,
        'last_comment_author': last_comment_author,
        'comment_count':       len(parsed_cmts),
        'age_weeks':           age_weeks,
        'needs_arch':          needs_arch,
        'is_jgs_critical':     _is_jgs_critical({**art, **full}),
        'primary_comp':        comp_trail[-1],
        'comp_trail':          comp_trail,
        'closed_date':         closed_date,
        'hsd_url':             HSD_ARTICLE_URL + article_id,
    }


def _fetch_bugs(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _EMU_CACHE['data'] is not None and now - _EMU_CACHE['ts'] < CACHE_TTL:
        return _EMU_CACHE['data']

    session      = _new_session()
    raw_articles = _rest_query(session)

    if not raw_articles:
        logger.error('REST query returned 0 articles for query %s', QUERY_ID)
        return _EMU_CACHE.get('data') or []

    bugs: list[dict] = []
    seen_ids: set[str] = set()

    for art in raw_articles:
        # HSD may use 'id', 'artf_id', or 'article_id'; value may be int/float/str
        article_id = (
            _safe_article_id(art.get('id')) or
            _safe_article_id(art.get('artf_id')) or
            _safe_article_id(art.get('article_id'))
        )
        if not article_id:
            logger.warning('Skipping article with no usable id field; keys present: %s', list(art.keys()))
            continue

        rec = _build_bug_record(session, article_id, art)
        if rec:
            bugs.append(rec)
            seen_ids.add(article_id)

    # Always include extra IDs from config (covers articles missing from the query)
    for extra_id in _get_extra_ids():
        if extra_id in seen_ids:
            continue
        logger.info('Fetching extra article %s (not in query)', extra_id)
        rec = _build_bug_record(session, extra_id, {})
        if rec:
            bugs.append(rec)
            seen_ids.add(extra_id)
        else:
            logger.warning('Extra article %s could not be fetched', extra_id)

    bugs.sort(key=lambda b: (
        0 if b['is_jgs_critical'] else 1,
        b['priority_rank'],
        b['filed_dt'] or datetime.max,
    ))

    _EMU_CACHE['data'] = bugs
    _EMU_CACHE['ts']   = now
    logger.info('Emulation bugs cache populated: %d bugs', len(bugs))
    return bugs


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/jgs-emu-bugs')
def jgs_emu_bugs():
    bugs = _fetch_bugs()

    closed_bugs    = [b for b in bugs if _is_closed(b)]
    completed_bugs = [b for b in closed_bugs if not _is_rejected(b)]
    rejected_bugs  = [b for b in closed_bugs if _is_rejected(b)]
    open_bugs      = [b for b in bugs if not _is_closed(b)]

    jgs_critical = [b for b in open_bugs if b['is_jgs_critical']]
    non_critical  = [b for b in open_bugs if not b['is_jgs_critical']]

    p1_bugs    = [b for b in non_critical if b['priority_rank'] == 1]
    p2_bugs    = [b for b in non_critical if b['priority_rank'] == 2]
    p3_bugs    = [b for b in non_critical if b['priority_rank'] == 3]
    other_bugs = [b for b in non_critical if b['priority_rank'] >= 4]

    return render_template(
        'jgs_emu_bugs.html',
        jgs_critical     = jgs_critical,
        p1_bugs          = p1_bugs,
        p2_bugs          = p2_bugs,
        p3_bugs          = p3_bugs,
        other_bugs       = other_bugs,
        completed_bugs   = completed_bugs,
        rejected_bugs    = rejected_bugs,
        total_bugs       = len(bugs),
        open_count       = len(open_bugs),
        closed_count     = len(closed_bugs),
        needs_arch_count = sum(1 for b in open_bugs if b['needs_arch']),
        escalate_weeks   = ESCALATE_DAYS // 7,
        last_updated     = datetime.now().strftime('%Y-%m-%d %H:%M'),
    )


@bp.route('/jgs-emu-bugs/teams-webhook', methods=['GET', 'POST'])
def teams_webhook_config():
    """GET: return current Power Automate flow URL (masked). POST: save/clear it."""
    cfg = json.loads(_CONFIG_FILE.read_text()) if _CONFIG_FILE.exists() else {}
    if request.method == 'POST':
        body = request.get_json(force=True)
        url  = str(body.get('url', '')).strip()
        if url and not url.startswith('https://'):
            return jsonify({'error': 'URL must start with https://'}), 400
        cfg['teams_webhook_url'] = url
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        logger.info('Teams Power Automate URL %s', 'cleared' if not url else 'updated')
        return jsonify({'status': 'saved'})
    raw    = cfg.get('teams_webhook_url', '')
    masked = (raw[:50] + '…') if len(raw) > 50 else raw
    return jsonify({'configured': bool(raw), 'masked': masked})


@bp.route('/jgs-emu-bugs/notify-mention', methods=['POST'])
def notify_mention():
    """Send a Teams DM to each @mentioned user via a Power Automate HTTP flow."""
    body      = request.get_json(force=True)
    mentions  = [str(u).strip().lower() for u in body.get('mentions', []) if str(u).strip()]
    bug_id    = str(body.get('bug_id',    '')).strip()
    bug_title = str(body.get('bug_title', '')).strip()
    note      = str(body.get('note',      '')).strip()

    if not mentions or not bug_id:
        return jsonify({'error': 'mentions and bug_id required'}), 400

    flow_url = _get_teams_webhook()
    if not flow_url:
        return jsonify({'error': 'no_webhook',
                        'message': 'Power Automate flow not configured — click 💬 DM Setup in the header.'}), 400

    hsd_link = HSD_ARTICLE_URL + bug_id
    sent, errors = [], []

    for username in mentions[:10]:
        payload = {
            'to':        f'{username}@intel.com',
            'bug_id':    bug_id,
            'bug_title': bug_title,
            'note':      note,
            'hsd_url':   hsd_link,
            'dash_url':  DASH_URL,
        }
        try:
            resp = requests.post(flow_url, json=payload, proxies=_PROXIES, timeout=15)
            if resp.status_code in (200, 202):
                sent.append(username)
                logger.info('Teams DM sent to %s for HSD %s', username, bug_id)
            else:
                logger.error('Flow returned %s for %s: %s', resp.status_code, username, resp.text[:200])
                errors.append({'user': username, 'error': f'HTTP {resp.status_code}'})
        except Exception as exc:
            logger.error('Flow POST failed for %s HSD %s: %s', username, bug_id, exc)
            errors.append({'user': username, 'error': str(exc)})

    return jsonify({'sent': sent, 'errors': errors})


@bp.route('/jgs-emu-bugs/refresh', methods=['POST'])
def refresh_emu():
    global _EMU_CACHE
    _EMU_CACHE = {'data': None, 'ts': 0}
    bugs = _fetch_bugs(force=True)
    return jsonify({'status': 'refreshed', 'count': len(bugs)})


@bp.route('/jgs-emu-bugs/debug')
def debug_emu():
    session = _new_session()
    out: dict = {'query_id': QUERY_ID}
    url = f'{HSD_BASE}/rest/query/{QUERY_ID}'
    try:
        resp = session.get(url, params={'start_at': 0, 'count': 2}, timeout=30)
        out['rest_status'] = resp.status_code
        raw = resp.json() if resp.status_code == 200 else resp.text[:500]
        out['total']        = raw.get('total') if isinstance(raw, dict) else '?'
        articles            = raw.get('data', []) if isinstance(raw, dict) else []
        out['query_sample'] = articles[0] if articles else {}
        if articles:
            first_id = str(articles[0].get('id', ''))
            out['full_article']          = _rest_article(session, first_id)
            parsed                       = _parse_comments_str(articles[0].get('comments', ''))
            out['comments_parsed_count'] = len(parsed)
            out['last_comment']          = parsed[-1] if parsed else {}
    except Exception as exc:
        out['error'] = str(exc)
    return jsonify(out)


@bp.route('/jgs-emu-bugs/lookup/<article_id>')
def lookup_article(article_id: str):
    """
    Diagnostic endpoint — check why a specific HSD article might not appear on the dashboard.
    GET /jgs-emu-bugs/lookup/18044355233
    """
    if not article_id.isdigit():
        return jsonify({'error': 'article_id must be numeric'}), 400

    result: dict = {'article_id': article_id}

    # 1. Is it already in the in-memory cache?
    cached = _EMU_CACHE.get('data')
    if cached:
        match = next((b for b in cached if b['id'] == article_id), None)
        if match:
            section = (
                'rejected'    if _is_rejected(match) else
                'completed'   if _is_closed(match)   else
                'jgs_critical' if match['is_jgs_critical'] else
                f"p{match['priority_rank']}_bugs"
            )
            return jsonify({
                'found_in_cache': True,
                'section': section,
                'bug': match,
            })
        result['found_in_cache'] = False
        result['cache_size'] = len(cached)
    else:
        result['cache_status'] = 'empty — not yet populated'

    # 2. Fetch the article directly to show its raw data
    session = _new_session()
    raw_article = _rest_article(session, article_id)
    result['direct_fetch'] = {
        'status':   raw_article.get('status'),
        'title':    raw_article.get('title'),
        'priority': raw_article.get('priority'),
        'owner':    raw_article.get('owner') or raw_article.get('assigned_to'),
        'raw_keys': sorted(raw_article.keys()),
    }

    # 3. Check the query for the article (small scan of first 200 results)
    in_query_scan = False
    scan_url = f'{HSD_BASE}/rest/query/{QUERY_ID}'
    for start in range(0, 1000, 100):
        try:
            resp = session.get(scan_url, params={'start_at': start, 'count': 100}, timeout=60)
            if resp.status_code != 200:
                result['query_scan_error'] = f'HTTP {resp.status_code}'
                break
            data    = resp.json()
            batch   = data.get('data', data) if isinstance(data, dict) else data
            if not isinstance(batch, list):
                break
            ids_in_batch = [
                _safe_article_id(a.get('id')) or
                _safe_article_id(a.get('artf_id')) or
                _safe_article_id(a.get('article_id'))
                for a in batch
            ]
            if article_id in ids_in_batch:
                in_query_scan = True
                result['found_in_query'] = True
                result['query_position'] = start + ids_in_batch.index(article_id)
                result['raw_query_record'] = batch[ids_in_batch.index(article_id)]
                break
            if len(batch) < 100:
                result['found_in_query'] = False
                result['total_scanned']  = start + len(batch)
                break
        except Exception as exc:
            result['query_scan_error'] = str(exc)
            break

    if in_query_scan:
        result['diagnosis'] = (
            'Article IS in the query. Trigger a cache refresh '
            '(POST /jgs-emu-bugs/refresh) and check the dashboard again.'
        )
    elif not raw_article:
        result['diagnosis'] = 'Could not fetch the article directly — check Kerberos auth.'
    else:
        result['diagnosis'] = (
            'Article is NOT returned by the HSD query. '
            'Check that it passes the query filters in HSD (status, tags, tenant, etc.).'
        )

    return jsonify(result)
