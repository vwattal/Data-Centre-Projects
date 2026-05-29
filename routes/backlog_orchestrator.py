"""
Backlog Orchestrator — /backlog-orchestrator, /api/backlog, /api/orchestrate, /api/rank

Fetches and sorts JIRA issues via the BacklogManager, and optionally
re-ranks them in JIRA's agile board order.
"""
import logging
import re
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, render_template, request, jsonify

from config import BACKLOG_TOKEN_PATH

urllib3.disable_warnings()
logger = logging.getLogger(__name__)
bp = Blueprint('backlog', __name__)


# ── BacklogManager ────────────────────────────────────────────────────────────

class BacklogManager:
    def __init__(self, token_file: str):
        try:
            with open(token_file) as f:
                token = f.read().strip()
        except FileNotFoundError:
            raise Exception(f"Token file '{token_file}' not found.")

        self.headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        self.base_url = 'https://jira.devtools.intel.com'

    # ── existing helpers ──────────────────────────────────────────────────────

    def get_issues(self, jql: str, max_results: int = 100) -> list:
        url    = f'{self.base_url}/rest/api/2/search'
        params = {
            'jql':        jql,
            'fields':     'summary,assignee,status,timeoriginalestimate,timeestimate,priority,created,updated,components,fixVersions,labels',
            'maxResults': max_results,
        }
        response = requests.get(url, headers=self.headers, params=params, verify=False)
        if response.status_code != 200:
            raise Exception(f"Error fetching issues: {response.status_code}")
        return response.json().get('issues', [])

    def sort_issues(self, issues: list, order_by: str, reverse: bool = True) -> list:
        sort_fns = {
            'effort':   lambda x: (x['fields'].get('timeoriginalestimate') or x['fields'].get('timeestimate') or 0),
            'created':  lambda x: x['fields'].get('created', ''),
            'updated':  lambda x: x['fields'].get('updated', ''),
            'priority': lambda x: self._priority_value(x['fields'].get('priority')),
            'key':      lambda x: x['key'],
        }
        return sorted(issues, key=sort_fns[order_by], reverse=reverse)

    def _priority_value(self, priority) -> int:
        if not priority:
            return 0
        return {'Critical': 5, 'High': 4, 'Medium': 3, 'Low': 2, 'Trivial': 1}.get(
            priority.get('name', ''), 0)

    def rank_issues(self, issue_keys: list) -> dict:
        """Rank issues in batches of 50.

        This board (VLK 53970) sorts the backlog in DESCENDING LexoRank order,
        meaning `rankAfterIssue` gives each subsequent issue a HIGHER rank value,
        which makes it appear HIGHER (earlier) on the board.
        To get P1 at the top and P4 at the bottom we must process the list in
        REVERSE: rank P4/Undecided first (they get low rank values → bottom),
        then P3, P2, and finally P1 (they get the highest rank values → top).
        """
        if not issue_keys or len(issue_keys) < 2:
            return {'success': True, 'message': 'No ranking needed',
                    'success_count': 0, 'failed_count': 0, 'errors': []}

        # Reverse so the LAST item in the caller's logical order (P4/Undecided)
        # becomes the anchor and P1 issues are placed last → highest rank → TOP.
        issue_keys = list(reversed(issue_keys))

        url           = f'{self.base_url}/rest/agile/1.0/issue/rank'
        BATCH         = 50
        success_count = 0
        failed_count  = 0
        errors        = []
        debug_resps   = []

        # Batch 0: rank keys[1:BATCH] after keys[0]
        tail = issue_keys[1:BATCH]
        if tail:
            payload = {'issues': tail, 'rankAfterIssue': issue_keys[0]}
            resp    = requests.put(url, headers=self.headers, json=payload, verify=False)
            debug_resps.append(f'batch0 status={resp.status_code} body={resp.text[:200]}')
            print(f'[rank] batch0 ({len(tail)} issues after {issue_keys[0]}): {resp.status_code}')
            if resp.status_code == 204:
                success_count += len(tail)
            else:
                failed_count += len(tail)
                errors.append(f'batch0: {resp.status_code} {resp.text[:200]}')

        # Remaining batches
        for start in range(BATCH, len(issue_keys), BATCH):
            batch   = issue_keys[start : start + BATCH]
            anchor  = issue_keys[start - 1]
            payload = {'issues': batch, 'rankAfterIssue': anchor}
            resp    = requests.put(url, headers=self.headers, json=payload, verify=False)
            batch_n = start // BATCH
            print(f'[rank] batch{batch_n} ({len(batch)} issues after {anchor}): {resp.status_code}')
            if resp.status_code == 204:
                success_count += len(batch)
            else:
                failed_count += len(batch)
                errors.append(f'batch{batch_n}@{start}: {resp.status_code} {resp.text[:200]}')
                if len(debug_resps) < 3:
                    debug_resps.append(f'batch{batch_n} status={resp.status_code} body={resp.text[:200]}')

        return {
            'success':       failed_count == 0,
            'success_count': success_count,
            'failed_count':  failed_count,
            'errors':        errors,
            'debug':         debug_resps,
        }

    # ── orchestrate helpers ───────────────────────────────────────────────────

    def extract_board_id(self, board_url: str) -> str:
        """Extract numeric board ID from a Rapid Board URL or plain number."""
        board_url = board_url.strip()
        if board_url.isdigit():
            return board_url
        # ?rapidView=1234
        m = re.search(r'rapidView=(\d+)', board_url)
        if m:
            return m.group(1)
        # /boards/1234
        m = re.search(r'/boards/(\d+)', board_url)
        if m:
            return m.group(1)
        raise ValueError(f"Cannot extract board ID from: {board_url!r}")

    def get_board_backlog(self, board_id: str, assignees: str = None) -> list:
        """Fetch ALL unresolved Stories and Bugs from the Rapid Board backlog in parallel."""
        url       = f'{self.base_url}/rest/agile/1.0/board/{board_id}/backlog'
        page_size = 500
        fields    = 'summary,assignee,status,timeoriginalestimate,timeestimate,priority,created,updated,components,fixVersions,labels,issuetype,issuelinks'
        jql = 'resolution = Unresolved AND issuetype in (Story, Bug)'
        if assignees:
            names = ', '.join(n.strip() for n in assignees.split(',') if n.strip())
            jql += f' AND assignee in ({names})'
        base_params = {
            'fields':     fields,
            'maxResults': page_size,
            'jql':        jql,
        }

        # First request: get total count
        first = requests.get(url, headers=self.headers,
                             params={**base_params, 'startAt': 0},
                             verify=False, timeout=30)
        if first.status_code != 200:
            raise Exception(f"Board backlog error {first.status_code}: {first.text[:300]}")
        body   = first.json()
        total  = body.get('total', 0)
        issues = body.get('issues', [])

        if total <= page_size:
            return issues

        # Fetch remaining pages in parallel
        offsets = list(range(page_size, total, page_size))

        def fetch_page(start_at):
            resp = requests.get(url, headers=self.headers,
                                params={**base_params, 'startAt': start_at},
                                verify=False, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"Page {start_at} error {resp.status_code}")
            return resp.json().get('issues', [])

        all_issues = list(issues)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch_page, off): off for off in offsets}
            for future in as_completed(futures):
                all_issues.extend(future.result())

        return all_issues

    @staticmethod
    def _dep_group(raw_links: list) -> tuple:
        """
        Returns (group_order, group_label):
          0 = blocks other teams   (has outward 'blocks' link)
          1 = no dependencies      (no links at all)
          2 = has external deps    (has inward 'is blocked by' link)
          3 = other links          (relates-to only etc.)
        Within each group issues are sorted by effort ascending.
        """
        blocks  = any(
            lnk.get('outwardIssue') and
            lnk.get('type', {}).get('outward', '').lower() in ('blocks', 'block')
            for lnk in raw_links
        )
        blocked = any(
            lnk.get('inwardIssue') and
            lnk.get('type', {}).get('inward', '').lower() in ('is blocked by', 'blocked by')
            for lnk in raw_links
        )
        if blocks:
            return 0, '🔗 Blocks others'
        if not raw_links or (not blocks and not blocked):
            return 1, '⬜ No dependencies'
        if blocked:
            return 2, '⏳ Has external deps'
        return 3, '🔄 Other links'

    def parse_rules(self, rules_text: str) -> list:
        """
        Parse text rules into callable checkers.
        Format per line:  field operator "value" → BUCKET
        Operators: =  !=  ~  !~
        Fields: component, status, fixVersion, labels/label, assignee, summary, priority
        Lines starting with # are comments; unmatched lines are skipped.
        """
        rules = []
        for line in rules_text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r'^(.+?)\s*→\s*(\w+)\s*$', line)
            if not m:
                continue
            condition_str = m.group(1).strip()
            bucket = m.group(2).upper()
            cm = re.match(r'(\w+)\s*(=|!=|~|!~)\s*["\']?(.+?)["\']?\s*$', condition_str)
            if not cm:
                continue
            field, op, value = cm.group(1).lower(), cm.group(2), cm.group(3).strip().lower()

            def make_checker(field, op, value, rule_str):
                def check(issue):
                    f = issue['fields']
                    extracted = []
                    if field in ('component', 'components'):
                        extracted = [c['name'].lower() for c in (f.get('components') or [])]
                    elif field in ('fixversion', 'fixversions'):
                        extracted = [v['name'].lower() for v in (f.get('fixVersions') or [])]
                    elif field in ('label', 'labels'):
                        extracted = [lb.lower() for lb in (f.get('labels') or [])]
                    elif field == 'status':
                        extracted = [(f.get('status') or {}).get('name', '').lower()]
                    elif field == 'assignee':
                        a = f.get('assignee') or {}
                        extracted = [
                            a.get('name', '').lower(),
                            a.get('displayName', '').lower(),
                            a.get('emailAddress', '').lower(),
                        ]
                    elif field == 'summary':
                        extracted = [f.get('summary', '').lower()]
                    elif field == 'priority':
                        extracted = [(f.get('priority') or {}).get('name', '').lower()]
                    if op == '=':
                        return any(value == e for e in extracted)
                    elif op == '!=':
                        return not any(value == e for e in extracted)
                    elif op == '~':
                        return any(value in e for e in extracted)
                    elif op == '!~':
                        return not any(value in e for e in extracted)
                    return False
                return check, rule_str

            checker, rule_str = make_checker(field, op, value, line)
            rules.append((checker, bucket, rule_str))
        return rules

    def apply_rules(self, issues: list, rules: list, default_bucket: str = 'P4') -> dict:
        """
        Bucket issues by Jira priority field (P1/P2/P3/P4).
        Text rules are optional overrides (first match wins before priority fallback).
        Include Stories and Bugs; skip Epics, Sub-tasks, Tasks.
        Sub-sort within each bucket:
          1. blocks_others → effort asc  (unblock teams fast)
          2. no_deps       → effort asc  (quick wins)
          3. external_deps → effort asc  (least blocked work first)
          4. other_links   → effort asc
        """
        buckets: dict = {'P1': [], 'P2': [], 'P3': [], 'P4': []}
        INCLUDED_TYPES = {'story', 'bug', 'defect'}

        for issue in issues:
            f = issue['fields']
            issuetype   = (f.get('issuetype') or {}).get('name', '')
            priority_nm = (f.get('priority')  or {}).get('name', '')
            raw_links   = f.get('issuelinks') or []

            # only Stories and Bugs/Defects
            if issuetype.lower() not in INCLUDED_TYPES:
                continue

            effort = (f.get('timeoriginalestimate') or f.get('timeestimate') or 0)
            dep_group, dep_label = self._dep_group(raw_links)

            # 1. Text rules first (manual overrides)
            assigned = None
            rule_str = ''
            for check_fn, bucket, rs in rules:
                if check_fn(issue):
                    assigned = bucket
                    rule_str = rs
                    break

            # 2. Fallback: extract P1/P2/P3/P4 from the Jira priority field name
            if assigned is None:
                pm = re.search(r'\bP([1-4])\b', priority_nm, re.IGNORECASE)
                if pm:
                    assigned = f'P{pm.group(1)}'
                    rule_str = f'Priority: {priority_nm}'
                else:
                    assigned = default_bucket
                    rule_str = f'Default ({priority_nm or "No priority"})'

            buckets.setdefault(assigned, []).append({
                'key':          issue['key'],
                'summary':      f['summary'],
                'effort':       effort,
                'effort_h':     round(effort / 3600, 1),
                'assignee':     (f.get('assignee') or {}).get('displayName', 'Unassigned'),
                'status':       (f.get('status')   or {}).get('name', 'Unknown'),
                'issuetype':    issuetype,
                'priority':     priority_nm,
                'components':   [c['name'] for c in (f.get('components')  or [])],
                'fix_versions': [v['name'] for v in (f.get('fixVersions') or [])],
                'labels':       list(f.get('labels') or []),
                'matched_rule': rule_str,
                'dep_group':    dep_group,
                'dep_label':    dep_label,
            })

        # sub-sort: dep_group asc (0→1→2→3), then effort asc within group
        for b in buckets:
            buckets[b].sort(key=lambda x: (x['dep_group'], x['effort']))

        return buckets


# ── Initialise singleton ──────────────────────────────────────────────────────

try:
    backlog_manager = BacklogManager(str(BACKLOG_TOKEN_PATH))
except Exception as _e:
    logger.warning(f"Could not initialise BacklogManager: {_e}")
    backlog_manager = None


# ── Routes ────────────────────────────────────────────────────────────────────

DEFAULT_BOARD_URL = (
    'https://jira.devtools.intel.com/secure/RapidBoard.jspa'
    '?rapidView=53970&view=planning.nodetail&issueLimit=100'
)

DEFAULT_RULES = """# ═══════════════════════════════════════════════════════
#  KMD Backlog — optional rule overrides
#  Primary bucketing uses the Jira PRIORITY field:
#    P1 (Showstopper) · P2 (Critical) · P3 (Major) · P4 (Minor)
#  Rules below override the priority-field bucket when matched.
#  Within each bucket sub-order:
#    🔗 Blocks others → ⬜ No deps (effort ↑) → ⏳ Has external deps (effort ↑)
# ═══════════════════════════════════════════════════════

# ── Optional overrides (uncomment / add as needed) ───
# status = "In Progress" → P1
# fixVersion ~ "JGS-WW" → P1
# labels = "jgs-p1" → P1
# labels = "upstream" → P2
# component = "KMD" → P3"""


@bp.route('/backlog-orchestrator')
def backlog_orchestrator():
    return render_template('backlog.html',
                           default_board_url=DEFAULT_BOARD_URL,
                           default_rules=DEFAULT_RULES)


@bp.route('/api/backlog', methods=['POST'])
def api_backlog():
    if not backlog_manager:
        return jsonify({'error': 'Backlog manager not initialized'}), 500
    data      = request.json
    scope     = data.get('scope', 'user')
    order_by  = data.get('orderBy', 'effort')
    custom_jql= data.get('jql', '')
    try:
        if scope == 'custom' and custom_jql:
            jql = custom_jql
        else:
            jql = 'project = VLK AND resolution = Unresolved'
            if scope == 'user':
                jql += ' AND assignee = currentUser()'
        issues  = backlog_manager.get_issues(jql)
        sorted_ = backlog_manager.sort_issues(issues, order_by, reverse=True)
        total_effort = 0
        formatted = []
        for iss in sorted_:
            effort = iss['fields'].get('timeoriginalestimate') or iss['fields'].get('timeestimate') or 0
            total_effort += effort
            formatted.append({
                'key':      iss['key'],
                'summary':  iss['fields']['summary'],
                'effort':   effort,
                'assignee': (iss['fields'].get('assignee') or {}).get('displayName', 'Unassigned'),
                'status':   iss['fields'].get('status', {}).get('name', 'Unknown'),
            })
        return jsonify({'issues': formatted, 'total': len(formatted), 'totalEffort': total_effort / 3600})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rank', methods=['POST'])
def api_rank():
    if not backlog_manager:
        return jsonify({'error': 'Backlog manager not initialized'}), 500
    data = request.json
    try:
        result = backlog_manager.rank_issues(data.get('issueKeys', []))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rank-debug', methods=['POST'])
def api_rank_debug():
    """Test rank API with just 2 issue keys; returns raw Jira response for diagnosis."""
    if not backlog_manager:
        return jsonify({'error': 'Backlog manager not initialized'}), 500
    data   = request.json or {}
    keys   = data.get('issueKeys', [])
    if len(keys) < 2:
        return jsonify({'error': 'Provide at least 2 issueKeys'}), 400
    url    = f'{backlog_manager.base_url}/rest/agile/1.0/issue/rank'
    test_keys = keys[:2]
    payload   = {'issues': [test_keys[1]], 'rankAfterIssue': test_keys[0]}
    resp      = requests.put(url, headers=backlog_manager.headers, json=payload, verify=False)
    return jsonify({
        'status_code': resp.status_code,
        'body':        resp.text[:500],
        'payload_sent': payload,
    })


@bp.route('/api/orchestrate', methods=['POST'])
def api_orchestrate():
    """
    POST { board_url, rules_text }
    Fetches the Rapid Board backlog, applies text rules, returns bucketed + ranked issues.
    """
    if not backlog_manager:
        return jsonify({'error': 'Backlog manager not initialized'}), 500
    data = request.json or {}
    board_url  = (data.get('board_url')  or '').strip()
    rules_text = (data.get('rules_text') or '').strip()
    assignees  = (data.get('assignees')  or '').strip()
    if not board_url:
        return jsonify({'error': 'board_url is required'}), 400
    try:
        board_id = backlog_manager.extract_board_id(board_url)
        issues   = backlog_manager.get_board_backlog(board_id, assignees or None)
        rules    = backlog_manager.parse_rules(rules_text)
        buckets  = backlog_manager.apply_rules(issues, rules)

        # flat ranked list: P1 → P2 → P3 → P4 → any extras
        ranked = []
        for bucket in ('P1', 'P2', 'P3', 'P4'):
            ranked.extend(buckets.get(bucket, []))
        for bucket in sorted(b for b in buckets if b not in ('P1', 'P2', 'P3', 'P4')):
            ranked.extend(buckets[bucket])

        return jsonify({
            'buckets':       buckets,
            'ranked':        ranked,
            'board_id':      board_id,
            'total':         len(ranked),
            'fetched':       len(issues),
            'rule_count':    len(rules),
            'original_keys': [i['key'] for i in issues],
        })
    except Exception as e:
        logger.exception("orchestrate error")
        return jsonify({'error': str(e)}), 500
