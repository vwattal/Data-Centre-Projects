"""
JGS Upstreaming Plan — /jgs-upstreaming

Fetches issues from Jira filter 452639, builds a cumulative WW trend graph,
and renders jgs_upstreaming_plan.html.
"""
import json
import re
import logging
import time
import requests
import urllib3
from collections import Counter
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, current_app

from config import JIRA_TOKEN_PATH, CACHE_TTL
from utils.helpers import current_ww_info

urllib3.disable_warnings()
logger = logging.getLogger(__name__)
bp = Blueprint('jgs_upstream', __name__)

_FILTER_ID = '452639'
_CACHE: dict = {'data': None, 'ts': 0}
_CLOSED_STATUSES = {'done', 'closed', 'resolved', 'verified', 'cancelled', 'rejected'}


# ── Jira fetch ────────────────────────────────────────────────────────────────

def _fetch_issues() -> list[dict]:
    now = time.time()
    if _CACHE['data'] is not None and now - _CACHE['ts'] < CACHE_TTL:
        return _CACHE['data']

    try:
        token = JIRA_TOKEN_PATH.read_text().strip()
    except Exception:
        return []

    hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    url  = 'https://jira.devtools.intel.com/rest/api/2/search'
    all_issues, start, page = [], 0, 100
    while True:
        params = {
            'jql':        f'filter={_FILTER_ID} ORDER BY component ASC, key ASC',
            'maxResults': page,
            'startAt':    start,
            'fields':     'summary,assignee,status,components,customfield_34504,timetracking',
        }
        try:
            resp = requests.get(url, headers=hdrs, params=params, verify=False, timeout=20)
            if resp.status_code != 200:
                break
            batch = resp.json().get('issues', [])
            all_issues.extend(batch)
            if len(batch) < page:
                break
            start += page
        except Exception:
            break

    def _norm_ww(raw):
        if not raw:
            return ''
        # Jira may return a dict or a plain string
        if isinstance(raw, dict):
            raw = raw.get('value') or raw.get('id') or ''
        m = re.match(r'(\d{2}[Ww]{2}\d+)', str(raw).strip())
        return m.group(1).upper() if m else ''

    def _ww_sort(ww):
        m = re.match(r'(\d+)[Ww]{2}(\d+)', ww)
        return (int(m.group(1)) * 100 + int(m.group(2))) if m else 9999

    result = []
    for iss in all_issues:
        f         = iss['fields']
        raw_comps = [c['name'] for c in (f.get('components') or [])]
        comp_list = raw_comps if raw_comps else ['Unassigned']
        raw_comp  = ', '.join(comp_list)
        ww        = _norm_ww(f.get('customfield_34504'))
        result.append({
            'key':            iss['key'],
            'title':          f.get('summary', ''),
            'assignee':       (f.get('assignee') or {}).get('displayName', 'Unassigned'),
            'ww':             ww,
            'ww_sort':        _ww_sort(ww) if ww else 9999,
            'component':      raw_comp,
            'raw_comp':       raw_comp,
            'comp_list':      comp_list,
            'status':         (f.get('status') or {}).get('name', ''),
            'estimated_time': (f.get('timetracking') or {}).get('originalEstimate', ''),
        })

    result.sort(key=lambda x: (x['component'], x['ww_sort'], x['key']))
    _CACHE['data'] = result
    _CACHE['ts']   = now
    return result


# ── Graph helper ──────────────────────────────────────────────────────────────

def _build_graph(issues: list) -> tuple[list, list]:
    counts: Counter = Counter()
    for iss in issues:
        if iss['ww'] and iss['ww_sort'] != 9999:
            counts[iss['ww']] += 1
    if not counts:
        return [], []

    def _ww_int(ww):
        m = re.match(r'(\d+)[Ww]{2}(\d+)', ww)
        return (int(m.group(1)) * 100 + int(m.group(2))) if m else None

    def _next_ww(wi):
        yy, wk = divmod(wi, 100)
        wk += 1
        if wk > 52:
            wk, yy = 1, yy + 1
        return yy * 100 + wk

    sorted_wws = sorted(counts.keys(), key=lambda w: _ww_int(w) or 9999)
    min_wi = _ww_int(sorted_wws[0])
    max_wi = _ww_int(sorted_wws[-1])
    # Extend to cover milestones
    for ms in (2703, 2712, 2724, 2736, 2806):
        if ms > max_wi:
            max_wi = ms
            break

    labels, cumulative, running = [], [], 0
    wi = min_wi
    while wi <= max_wi:
        yy, wk = divmod(wi, 100)
        lbl = f'{yy}WW{wk:02d}'
        running += counts.get(lbl, 0)
        labels.append(lbl)
        cumulative.append(running)
        wi = _next_ww(wi)
    return labels, cumulative


# ── Route ─────────────────────────────────────────────────────────────────────

@bp.route('/jgs-upstreaming')
def jgs_upstreaming():
    issues = _fetch_issues()
    current_ww_label, current_ww_int, _ = current_ww_info()

    graph_labels, graph_data = _build_graph(issues)

    all_wws = [i['ww_sort'] for i in issues if i['ww_sort'] != 9999]
    comp_closure_ww: dict = {}
    if all_wws:
        yy, wk = divmod(max(all_wws), 100)
        comp_closure_ww['All'] = f'{yy}WW{wk:02d}'

    components = sorted({c for i in issues for c in i['comp_list']})
    statuses   = sorted({i['status']   for i in issues if i['status']})
    assignees  = sorted({i['assignee'] for i in issues if i['assignee'] and i['assignee'] != 'Unassigned'})
    def _ww_key(ww):
        m = re.match(r'(\d+)[Ww]{2}(\d+)', ww)
        return int(m.group(1)) * 100 + int(m.group(2)) if m else 9999
    ww_list = sorted({i['ww'] for i in issues if i['ww']}, key=_ww_key)

    pending_issues = sorted(
        [i for i in issues
         if i['ww_sort'] != 9999 and i['ww_sort'] <= current_ww_int
         and i['status'].strip().lower() not in _CLOSED_STATUSES],
        key=lambda x: (x['ww_sort'], x['component'], x['key']),
    )

    try:
        ud = json.loads((Path(current_app.root_path) / 'user_data.json').read_text())
    except Exception:
        ud = {}
    up_comments = ud.get('jgs_up_comments', {})

    return render_template(
        'jgs_upstreaming_plan.html',
        issues=issues,
        graph_labels=graph_labels,
        graph_data=graph_data,
        comp_closure_ww=comp_closure_ww,
        filter_id=_FILTER_ID,
        current_ww_label=current_ww_label,
        current_ww_int=current_ww_int,
        components=components,
        statuses=statuses,
        assignees=assignees,
        ww_list=ww_list,
        up_comments=up_comments,
        pending_issues=pending_issues,
    )


@bp.route('/jgs-upstreaming/save-comment', methods=['POST'])
def jgs_upstreaming_save_comment():
    body    = request.get_json(silent=True) or {}
    key     = str(body.get('key', ''))[:20]
    comment = str(body.get('comment', ''))[:500]
    if not key:
        return jsonify({'error': 'missing key'}), 400
    try:
        fp = Path(current_app.root_path) / 'user_data.json'
        ud = json.loads(fp.read_text()) if fp.exists() else {}
        ud.setdefault('jgs_up_comments', {})[key] = comment
        fp.write_text(json.dumps(ud, indent=2))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/jgs-upstreaming/refresh', methods=['POST'])
def jgs_upstreaming_refresh():
    _CACHE['data'] = None
    _CACHE['ts']   = 0
    return jsonify({'ok': True})
