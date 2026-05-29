"""
JGS Bug Triage Dashboard — /jgs-bug-triage

Data fetch strategy (Kerberos throughout):
  1. REST  GET  https://hsdes.intel.com/rest/query/<QUERY_ID>
             → returns list of article dicts (id, title, priority, status, …)
  2. ESService  get_record_by_id  (tenant=ip_hw_graphics, subject=bugeco)
             → enriches each article with tenant-specific fields (tags, root cause)
  3. REST  GET  https://hsdes.intel.com/rest/article/<id>/comment
             → last-comment date + text (fetched lazily via AJAX)

Bugs open > 14 days without root cause → "Notify Arch" button that POSTs
to /jgs-bug-triage/send-arch-email.
"""

import uuid
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from datetime             import datetime
from html                 import unescape
import re

import requests
import urllib3
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
from flask import Blueprint, render_template, jsonify, request
from config import APP_BASE_URL as _APP_BASE_URL

urllib3.disable_warnings()
logger = logging.getLogger(__name__)
bp     = Blueprint('jgs_bug_triage', __name__)

# ── Constants ─────────────────────────────────────────────────────────────────
QUERY_ID        = '14026739770'
HSD_BASE        = 'https://hsdes.intel.com'
HSD_ARTICLE_URL = HSD_BASE + '/appstore/article/#/'
ESSERVICE_URL   = HSD_BASE + '/ws/ESService'
ESSERVICE_HDRS  = {
    'APP':          'HSD-ES Article',
    'Accept':       'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    'Origin':       HSD_BASE,
    'Referer':      HSD_BASE + '/appstore/article-one/',
}
SMTP_SERVER    = 'smtpmail.intel.com'
SW_ARCH_EMAIL  = 'your-swarch-dl@intel.com'   # ← replace with real DL
ESCALATE_DAYS  = 14                            # days before looping in arch
CACHE_TTL      = 300                           # 5-minute cache

# ── Component mapping ─────────────────────────────────────────────────────────
# Ordered: first match wins as the PRIMARY section key.
# A bug can also carry secondary component badges for display.
_COMP_RULES: list[tuple[str, str]] = [
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
_COMP_ORDER = ['XeSim', 'KMD', 'IGC', 'PISA', 'Test', 'Compute Dev', 'Val Team', 'Other']


def _map_components(component_affected: str) -> tuple[str, list[str]]:
    """
    Returns (primary_section, [badge1, badge2, …]).
    primary_section is the first rule that matches (for table grouping).
    badges = all matching rule labels (deduplicated, ordered).
    """
    ca = (component_affected or '').lower()
    matched: list[str] = []
    for pattern, label in _COMP_RULES:
        if pattern in ca and label not in matched:
            matched.append(label)
    primary = matched[0] if matched else 'Other'
    return primary, matched or ['Other']

_CACHE: dict = {'data': None, 'ts': 0}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP / ESService helpers
# ─────────────────────────────────────────────────────────────────────────────

def _new_session() -> requests.Session:
    s        = requests.Session()
    s.auth   = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify = False
    return s


def _esservice(session: requests.Session, command: str, args: dict):
    """Single ESService call; returns result_table list or None."""
    payload = {
        'requests': [{
            'api_client':   'HSD-ES Article',
            'tran_id':      str(uuid.uuid4()).upper(),
            'command':      command,
            'command_args': args,
            'var_args':     [],
            'copy_args':    [],
        }]
    }
    try:
        resp = session.post(ESSERVICE_URL, headers=ESSERVICE_HDRS, json=payload, timeout=45)
        if resp.status_code == 200:
            data = resp.json()
            if 'responses' in data:
                r = data['responses'][0]
                if r.get('status') == 'success':
                    return r.get('result_table', [])
                else:
                    logger.warning('ESService %s status=%s msg=%s',
                                   command, r.get('status'), r.get('message', ''))
        else:
            logger.warning('ESService %s HTTP %s: %s',
                           command, resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error('ESService %s failed: %s', command, exc)
    return None


def _rest_query(session: requests.Session) -> list[dict]:
    """
    Fetch article list from HSD saved-query REST endpoint.
    Returns list of dicts, each containing at minimum {'id': '...', ...}.
    Handles pagination automatically.
    """
    all_articles: list[dict] = []
    start = 0
    page  = 100
    while True:
        url = f'{HSD_BASE}/rest/query/{QUERY_ID}'
        params = {'start_at': start, 'count': page}
        try:
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code != 200:
                logger.error('REST query HTTP %s: %s', resp.status_code, resp.text[:300])
                break
            data  = resp.json()
            batch = data.get('data', data) if isinstance(data, dict) else data
            if not isinstance(batch, list):
                logger.error('Unexpected REST query shape: %s', str(data)[:200])
                break
            all_articles.extend(batch)
            logger.info('REST query: fetched %d (total so far %d)', len(batch), len(all_articles))
            if not batch:
                break
            start += page
        except Exception as exc:
            logger.error('REST query failed: %s', exc)
            break
    return all_articles


def _rest_article(session: requests.Session, article_id: str) -> dict:
    """Fetch full article fields from /rest/article/<id> (tags, description, root_cause)."""
    url = f'{HSD_BASE}/rest/article/{article_id}'
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('data', []) if isinstance(data, dict) else []
            return items[0] if items else {}
    except Exception as exc:
        logger.debug('Full article fetch for %s: %s', article_id, exc)
    return {}


def _rest_history(session: requests.Session, article_id: str) -> list[dict]:
    """Fetch revision history from /rest/article/<id>/history.
    Returns entries sorted oldest-first (ascending rev number)."""
    url = f'{HSD_BASE}/rest/article/{article_id}/history'
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get('data', [])
            return sorted(items, key=lambda e: int(e.get('rev', 0)))
    except Exception as exc:
        logger.debug('History fetch for %s: %s', article_id, exc)
    return []


def _comp_label(raw: str) -> str:
    """Map a single HSD component value to a short display label.
    Falls back to a cleaned version of the last dotted segment."""
    r = (raw or '').lower().strip()
    if not r or r in ('none', 'null'):
        return 'Other'
    for pattern, label in _COMP_RULES:
        if pattern in r:
            return label
    last = r.rsplit('.', 1)[-1]
    return last.replace('_', ' ').title() if last else 'Other'


def _extract_comp_trail(history: list[dict]) -> list[str]:
    """Walk history entries (oldest-first) and return the ordered, deduplicated
    list of component labels, e.g. ['KMD', 'Compute Dev', 'App Issue'].

    Parent-only routing nodes (e.g. 'ip.sw_only' with no subcategory — less
    than 3 dotted segments) are skipped; they are transitional HSD routing
    artefacts, not real assignments.
    """
    trail: list[str] = []
    for entry in history:
        raw = str(entry.get('component') or '').strip()
        if not raw or raw in ('None', 'null'):
            continue
        # Skip parent-only placeholder entries like 'ip.sw_only' (< 3 segments)
        if len(raw.split('.')) < 3:
            continue
        label = _comp_label(raw)
        if not trail or trail[-1] != label:
            trail.append(label)
    return trail or ['Other']


def _extract_closed_date(full_art: dict, updated_raw: str) -> str:
    """Return the closed_date from the full article, falling back to updated_date."""
    d = str(full_art.get('closed_date') or '').strip()
    if d and d not in ('None', 'null', ''):
        return d[:10]
    d = str(full_art.get('ip_hw_graphics.bugeco.fixed_date') or '').strip()
    if d and d not in ('None', 'null', ''):
        return d[:10]
    return updated_raw[:10] if updated_raw else '—'


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

# (fmt, actual rendered length in chars)
_DATE_FMTS = (
    ('%Y-%m-%d %H:%M:%S',   19),
    ('%Y-%m-%dT%H:%M:%S',   19),
    ('%Y-%m-%d %H:%M',      16),
    ('%Y-%m-%d',            10),
    ('%m/%d/%Y %H:%M:%S',   19),
    ('%m/%d/%Y',            10),
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
    """
    Parse HSD embedded comments string.
    Format: '++++<comment_id> <username>\\n<text>\\n++++<id2> <user2>\\n<text2>…'
    Returns list of {'username': ..., 'text': ...} in order (last = most recent).
    """
    if not raw:
        return []
    # Split on ++++  (may or may not have leading newline)
    blocks = re.split(r'\n?\+\+\+\+', str(raw))
    result = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split('\n', 1)
        header = lines[0].strip()
        text   = lines[1].strip() if len(lines) > 1 else ''
        m = re.match(r'\d+\s+(\S+)', header)
        username = m.group(1) if m else header.split()[-1] if header.split() else '?'
        result.append({'username': username, 'text': text})
    return result


def _priority_rank(pri: str) -> int:
    p = str(pri).lower().strip()
    for val, rank in (
        ('showstopper', 1), ('critical', 1), ('1-', 1),
        ('blocker', 1),
        ('2-', 2), ('high', 2),
        ('3-', 3), ('medium', 3),
        ('4-', 4), ('low', 4),
    ):
        if val in p:
            return rank
    return 5


def _priority_label(pri: str) -> str:
    rank = _priority_rank(pri)
    return {1: 'P1 – Showstopper', 2: 'P2 – High', 3: 'P3 – Medium', 4: 'P4 – Low'}.get(rank, pri or 'Unknown')


def _is_closed(bug: dict) -> bool:
    """Return True when the bug status indicates complete or rejected."""
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


def _build_summary(full_art: dict, owner: str) -> str:
    """
    Build a concise triage summary from description + root cause + AR owner.
    """
    parts = []

    # Problem statement from description
    desc = _strip_html(full_art.get('description', ''))
    if desc:
        parts.append(desc[:250])

    # Root cause classification
    root_cause = str(full_art.get('bugeco.root_cause') or '').strip()
    if root_cause and root_cause not in ('', 'None', 'null'):
        parts.append(f'Root cause: {root_cause}')

    # Status reason (e.g. open.root_caused)
    status_reason = str(full_art.get('status_reason') or '').strip()
    if status_reason:
        parts.append(f'State: {status_reason.replace(".", " → ")}')

    # Fixed by
    fixed_by = str(full_art.get('ip_hw_graphics.bugeco.fixed_by') or '').strip()
    if fixed_by:
        parts.append(f'Fixed by: {fixed_by}')

    # AR owner
    if owner:
        parts.append(f'AR: {owner}')

    return ' · '.join(parts) if parts else '—'


# ─────────────────────────────────────────────────────────────────────────────
# Core data fetch
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_bugs(force: bool = False) -> list[dict]:
    """
    Return cached bug list; re-fetches from HSD when TTL expires or forced.

    Step 1: REST GET /rest/query/<QUERY_ID>
            → basic fields: id, title, priority, status, owner,
              submitted_date, updated_date, comments (raw string)
    Step 2: REST GET /rest/article/<id>
            → full article: description, bugeco.root_cause, tag, status_reason,
              ip_hw_graphics.bugeco.fixed_by
    Comments parsed inline from the 'comments' string already in query response.
    updated_date = date of last comment/edit.
    """
    now = time.time()
    if not force and _CACHE['data'] is not None and now - _CACHE['ts'] < CACHE_TTL:
        return _CACHE['data']

    session      = _new_session()
    raw_articles = _rest_query(session)

    if not raw_articles:
        logger.error('REST query returned 0 articles for query %s — '
                     'check Kerberos ticket (kinit)', QUERY_ID)
        return _CACHE.get('data') or []

    logger.info('Processing %d articles from query %s', len(raw_articles), QUERY_ID)
    bugs: list[dict] = []

    for art in raw_articles:
        article_id = str(art.get('id') or '').strip()
        if not article_id or not article_id.isdigit():
            continue

        # ── Full article fetch: tags, description, root cause ─────────────────
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

        # ── Component trail from history ───────────────────────────────────────
        history    = _rest_history(session, article_id)
        comp_trail = _extract_comp_trail(history) if history else [_comp_label(single_comp)]
        primary_comp = comp_trail[-1]

        # ── Parse embedded comments string ─────────────────────────────────────
        parsed_cmts = _parse_comments_str(_g('comments'))
        if parsed_cmts:
            lc = parsed_cmts[-1]
            last_comment_author = lc['username']
            last_comment_text   = _strip_html(lc['text'])[:500]
        else:
            last_comment_author = ''
            last_comment_text   = ''
        last_comment_date = updated_raw[:16] if updated_raw else ''

        # ── Age ────────────────────────────────────────────────────────────────
        filed_dt    = _parse_dt(filed_raw)
        filed_str   = filed_raw[:10] if filed_raw else '—'
        closed_date = _extract_closed_date(full, updated_raw)

        # For closed bugs measure lifespan filed→closed; for open bugs use today
        closed_dt   = _parse_dt(closed_date) if closed_date and closed_date != '—' else None
        end_dt      = closed_dt if (('complet' in status.lower() or 'reject' in status.lower()) and closed_dt) else datetime.now()
        age_days    = (end_dt - filed_dt).days if filed_dt else 0
        age_weeks   = age_days // 7

        needs_arch  = age_days >= ESCALATE_DAYS and not root_cause

        bugs.append({
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
            'primary_comp':        primary_comp,
            'comp_trail':          comp_trail,
            'closed_date':         closed_date,
            'hsd_url':             HSD_ARTICLE_URL + article_id,
        })

    bugs.sort(key=lambda b: (
        0 if b['is_jgs_critical'] else 1,
        b['priority_rank'],
        b['filed_dt'] or datetime.max,
    ))

    _CACHE['data'] = bugs
    _CACHE['ts']   = now
    logger.info('Bug triage cache populated: %d bugs', len(bugs))
    return bugs


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/jgs-bug-triage')
def jgs_bug_triage():
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

    total_bugs       = len(bugs)
    open_count       = len(open_bugs)
    closed_count     = len(closed_bugs)
    needs_arch_count = sum(1 for b in open_bugs if b['needs_arch'])
    last_updated     = datetime.now().strftime('%Y-%m-%d %H:%M')

    return render_template(
        'jgs_bug_triage.html',
        jgs_critical     = jgs_critical,
        p1_bugs          = p1_bugs,
        p2_bugs          = p2_bugs,
        p3_bugs          = p3_bugs,
        other_bugs       = other_bugs,
        completed_bugs   = completed_bugs,
        rejected_bugs    = rejected_bugs,
        total_bugs       = total_bugs,
        open_count       = open_count,
        closed_count     = closed_count,
        needs_arch_count = needs_arch_count,
        escalate_weeks   = ESCALATE_DAYS // 7,
        last_updated     = last_updated,
    )


@bp.route('/jgs-bug-triage/refresh', methods=['POST'])
def refresh_triage():
    global _CACHE
    _CACHE = {'data': None, 'ts': 0}
    bugs = _fetch_bugs(force=True)
    return jsonify({'status': 'refreshed', 'count': len(bugs)})


@bp.route('/jgs-bug-triage/debug')
def debug_triage():
    """Diagnostic — shows raw first article from query + full article fields."""
    session = _new_session()
    out: dict = {'query_id': QUERY_ID}
    url = f'{HSD_BASE}/rest/query/{QUERY_ID}'
    try:
        resp = session.get(url, params={'start_at': 0, 'count': 2}, timeout=30)
        out['rest_status'] = resp.status_code
        raw = resp.json() if resp.status_code == 200 else resp.text[:500]
        out['total'] = raw.get('total') if isinstance(raw, dict) else '?'
        articles = raw.get('data', []) if isinstance(raw, dict) else []
        out['query_sample'] = articles[0] if articles else {}
        if articles:
            first_id = str(articles[0].get('id', ''))
            out['full_article'] = _rest_article(session, first_id)
            parsed = _parse_comments_str(articles[0].get('comments', ''))
            out['comments_parsed_count'] = len(parsed)
            out['last_comment'] = parsed[-1] if parsed else {}
    except Exception as exc:
        out['error'] = str(exc)
    return jsonify(out)


@bp.route('/jgs-bug-triage/send-arch-email', methods=['POST'])
def send_arch_email():
    """Send an escalation e-mail to the SW Architecture team for a specific bug."""
    body      = request.get_json(force=True)
    bug_id    = str(body.get('bug_id',    '')).strip()
    bug_title = str(body.get('bug_title', '')).strip()
    age_weeks = int(body.get('age_weeks', 0))

    if not bug_id:
        return jsonify({'error': 'bug_id is required'}), 400

    hsd_link   = HSD_ARTICLE_URL + bug_id
    dash_link  = _APP_BASE_URL + '/jgs-bug-triage'
    subject    = (
        f'[Action Required] HSD {bug_id} – root cause needed '
        f'({age_weeks} weeks open)'
    )
    html_body  = f"""\
<html><body style="font-family:Arial,sans-serif;font-size:14px;">
<p>Hi SW Architecture Team,</p>
<p>
  Bug <a href="{hsd_link}"><strong>{bug_id}</strong></a>
  &mdash; <em>{bug_title}</em>
  has been open for <strong>{age_weeks}&nbsp;weeks</strong>
  without a root cause being identified.
</p>
<p>
  Per the JGS triage policy, bugs open longer than {ESCALATE_DAYS // 7}&nbsp;weeks
  without a root cause are escalated for architecture review.
</p>
<p>Please review and provide your analysis at your earliest convenience.</p>
<p>
  <a href="{dash_link}">View full JGS Bug Triage Dashboard</a>
</p>
<p>Thanks,<br/>JGS PM Automation</p>
</body></html>
"""

    try:
        msg              = MIMEMultipart('alternative')
        msg['Subject']   = subject
        msg['From']      = 'jgs-pm-automation@intel.com'
        msg['To']        = SW_ARCH_EMAIL
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(SMTP_SERVER, timeout=10) as smtp:
            smtp.send_message(msg)

        logger.info('Arch escalation email sent for HSD %s', bug_id)
        return jsonify({'status': 'sent', 'bug_id': bug_id, 'to': SW_ARCH_EMAIL})

    except Exception as exc:
        logger.error('Email send failed for HSD %s: %s', bug_id, exc)
        return jsonify({'error': str(exc)}), 500
