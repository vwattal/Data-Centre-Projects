"""
routes/jgs_allbugs_sw.py
========================
Flask Blueprint for the JGS All Bugs (SW) Dashboard (/jgs-allbugs-sw).

Data source: HSD query 14027983107
Same display logic as jgs_emu_bugs — JGS Critical first, then P1/P2/P3/Other.
Shared helper functions are imported from jgs_emu_bugs to avoid duplication.
"""

import time
import logging
from datetime import datetime

from flask import Blueprint, render_template, jsonify

from routes.jgs_emu_bugs import (
    _new_session,
    _build_bug_record,
    _is_closed,
    _is_rejected,
    _is_jgs_critical,
    _safe_article_id,
    _extract_batch,
    HSD_BASE,
    ESCALATE_DAYS,
)

logger = logging.getLogger(__name__)
bp = Blueprint('jgs_allbugs_sw', __name__)

SW_QUERY_ID = '14027983107'
CACHE_TTL   = 300   # seconds

_SW_CACHE: dict = {'data': None, 'ts': 0}


# ── Query fetch ───────────────────────────────────────────────────────────────

def _rest_query_sw(session) -> list[dict]:
    all_articles: list[dict] = []
    start, page = 0, 100
    for _ in range(50):
        try:
            resp = session.get(
                f'{HSD_BASE}/rest/query/{SW_QUERY_ID}',
                params={'start_at': start, 'count': page},
                timeout=60,
            )
            if resp.status_code != 200:
                logger.error('SW bugs query HTTP %s', resp.status_code)
                break
            batch = _extract_batch(resp.json())
            if not batch:
                break
            all_articles.extend(batch)
            if len(batch) < page:
                break
            start += page
        except Exception as exc:
            logger.error('SW bugs query failed at start=%d: %s', start, exc)
            break
    logger.info('SW bugs query: %d articles fetched', len(all_articles))
    return all_articles


# ── Cache + data builder ──────────────────────────────────────────────────────

def _fetch_sw_bugs(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _SW_CACHE['data'] is not None and now - _SW_CACHE['ts'] < CACHE_TTL:
        return _SW_CACHE['data']

    session = _new_session()
    raw     = _rest_query_sw(session)
    if not raw:
        logger.error('SW bugs query returned 0 articles')
        return _SW_CACHE.get('data') or []

    bugs: list[dict] = []
    seen: set[str]   = set()

    for art in raw:
        aid = (
            _safe_article_id(art.get('id')) or
            _safe_article_id(art.get('artf_id')) or
            _safe_article_id(art.get('article_id'))
        )
        if not aid or aid in seen:
            continue
        rec = _build_bug_record(session, aid, art)
        if rec:
            bugs.append(rec)
            seen.add(aid)

    bugs.sort(key=lambda b: (
        0 if b['is_jgs_critical'] else 1,
        b['priority_rank'],
        b['filed_dt'] or datetime.max,
    ))

    _SW_CACHE['data'] = bugs
    _SW_CACHE['ts']   = now
    logger.info('SW bugs cache populated: %d bugs', len(bugs))
    return bugs


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route('/jgs-allbugs-sw')
def jgs_allbugs_sw():
    bugs = _fetch_sw_bugs()

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
        'jgs_allbugs_sw.html',
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
        needs_arch_count = sum(1 for b in open_bugs if b.get('needs_arch')),
        escalate_weeks   = ESCALATE_DAYS // 7,
        last_updated     = datetime.now().strftime('%Y-%m-%d %H:%M'),
    )


@bp.route('/jgs-allbugs-sw/refresh', methods=['POST'])
def refresh_sw():
    global _SW_CACHE
    _SW_CACHE = {'data': None, 'ts': 0}
    bugs = _fetch_sw_bugs(force=True)
    return jsonify({'status': 'refreshed', 'count': len(bugs)})
