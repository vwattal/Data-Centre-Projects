"""
Misc API endpoints and static file serving.

  GET/POST /api/user-data
  POST     /api/auto-refresh
  GET      /static/rtl_freeze_graph.png
"""
import json
import sys
import logging
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, current_app

from config import SOC_AUTOMATION_DIR

logger = logging.getLogger(__name__)
bp = Blueprint('api', __name__)


# ── Static graph ──────────────────────────────────────────────────────────────

@bp.route('/static/rtl_freeze_graph.png')
def rtl_freeze_graph():
    graph_path = SOC_AUTOMATION_DIR / 'rtl_freeze_graph.png'
    if graph_path.exists():
        return send_file(graph_path, mimetype='image/png')
    return 'Graph not found. Run weekly_update.sh to generate it.', 404


# ── User data (key messages, notes, next steps) ───────────────────────────────

@bp.route('/api/user-data', methods=['GET'])
def get_user_data():
    data_file = Path(current_app.root_path) / 'user_data.json'
    if data_file.exists():
        with open(data_file) as f:
            return jsonify(json.load(f))
    return jsonify({'key_messages': [], 'long_pole_notes': {}, 'next_steps': []})


@bp.route('/api/user-data', methods=['POST'])
def save_user_data():
    try:
        data      = request.get_json()
        data_file = Path(current_app.root_path) / 'user_data.json'
        with open(data_file, 'w') as f:
            json.dump(data, f, indent=2)
        return jsonify({'status': 'success', 'message': 'Data saved'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Jira tracking design — shared notes ─────────────────────────────────────

_NOTES_FILE = Path(__file__).parent.parent / 'jira_tracking_notes.json'


@bp.route('/api/jira-tracking-notes', methods=['GET'])
def get_jira_notes():
    if _NOTES_FILE.exists():
        with open(_NOTES_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({'notes': '', 'names': {}})


@bp.route('/api/jira-tracking-notes', methods=['POST'])
def save_jira_notes():
    try:
        data = request.get_json(force=True)
        existing = {'notes': '', 'names': {}}
        if _NOTES_FILE.exists():
            with open(_NOTES_FILE) as f:
                existing = json.load(f)
        
        # Merge names dict properly instead of replacing
        if 'names' in data:
            if 'names' not in existing:
                existing['names'] = {}
            existing['names'].update(data['names'])
        
        # Update notes if provided
        if 'notes' in data:
            existing['notes'] = data['notes']
        
        with open(_NOTES_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Jira tracking rules — shared rules content ────────────────────────────────

_RULES_FILE = Path(__file__).parent.parent / 'jira_tracking_rules.json'


@bp.route('/api/jira-tracking-rules', methods=['GET'])
def get_jira_rules():
    if _RULES_FILE.exists():
        with open(_RULES_FILE) as f:
            resp = jsonify(json.load(f))
            resp.headers['Cache-Control'] = 'no-store'
            return resp
    resp = jsonify({'content': ''})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/jira-tracking-rules', methods=['POST'])
def save_jira_rules():
    try:
        data = request.get_json(force=True)
        content = data.get('content', '')
        with open(_RULES_FILE, 'w') as f:
            json.dump({'content': content}, f, indent=2)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Excel mtime (used by frontend to detect file changes) ────────────────────

import sqlite3 as _sqlite3

@bp.route('/api/excel-mtime')
def excel_mtime():
    """Return the last-modified timestamp of JGS_SOC_Trend.xlsx and the
    latest task-comment updated_at timestamp.

    The frontend polls this every 30 s and reloads when either value changes,
    so Excel saves AND comment edits by any user propagate to all browsers.
    """
    excel_path = SOC_AUTOMATION_DIR / 'JGS_SOC_Trend.xlsx'
    try:
        mtime = excel_path.stat().st_mtime
    except FileNotFoundError:
        mtime = None

    comments_ts = None
    soc_db = SOC_AUTOMATION_DIR / 'soc_edits.db'
    if soc_db.exists():
        try:
            con = _sqlite3.connect(str(soc_db))
            row = con.execute(
                "SELECT updated_at FROM task_comments ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            con.close()
            comments_ts = row[0] if row else None
        except Exception:
            pass

    return jsonify({'mtime': mtime, 'comments_ts': comments_ts})


# ── Auto-refresh (background thread) ─────────────────────────────────────────

@bp.route('/api/auto-refresh', methods=['POST'])
def auto_refresh():
    try:
        # Capture paths before spawning thread (no request context in thread)
        root = Path(current_app.root_path)

        def run_refresh():
            try:
                import logging as _log
                _logger = _log.getLogger('auto_refresh')
                _logger.setLevel(_log.INFO)
                fh = _log.FileHandler(root / 'auto_refresh.log', mode='a')
                fh.setFormatter(_log.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
                _logger.addHandler(fh)
                _logger.info('=' * 60)
                _logger.info('Starting auto-refresh...')

                def _run(script_path, timeout=60):
                    r = subprocess.run(
                        [sys.executable, str(script_path)],
                        cwd=str(SOC_AUTOMATION_DIR),
                        capture_output=True, text=True, timeout=timeout,
                    )
                    return r

                token_script = SOC_AUTOMATION_DIR / 'refresh_graph_token.py'
                if token_script.exists():
                    _logger.info('Checking SharePoint token...')
                    r = _run(token_script, timeout=30)
                    _logger.info('✓ Token OK' if r.returncode == 0
                                 else f'Token issue: {r.stderr}')

                dl_script = SOC_AUTOMATION_DIR / 'download_sharepoint_file.py'
                if dl_script.exists():
                    _logger.info('Downloading latest Excel...')
                    r = _run(dl_script, timeout=60)
                    if r.returncode != 0:
                        _logger.error(f'Download failed: {r.stderr}')
                    else:
                        _logger.info('✓ Excel downloaded')

                checker = SOC_AUTOMATION_DIR / 'weekly_jira_checker.py'
                if checker.exists():
                    _logger.info('Checking JIRA completions...')
                    r = _run(checker, timeout=120)
                    if r.returncode != 0:
                        _logger.error(f'JIRA check failed: {r.stderr}')
                    else:
                        _logger.info('✓ Task tracking updated')

                graph_script = SOC_AUTOMATION_DIR / 'generate_rtl_freeze_graph.py'
                if graph_script.exists():
                    _logger.info('Regenerating graph...')
                    r = _run(graph_script, timeout=60)
                    _logger.info('✓ Graph regenerated' if r.returncode == 0
                                 else f'Graph failed: {r.stderr}')

                # Invalidate Excel cache
                from routes.jgs_soc_updates import invalidate_cache
                invalidate_cache()
                _logger.info('✓ Cache cleared')
                _logger.info('Auto-refresh completed!')
                _logger.info('=' * 60)

            except Exception as e:
                logger.error(f'Background refresh error: {e}')

        threading.Thread(target=run_refresh, daemon=True).start()
        return jsonify({
            'status':  'success',
            'message': 'Auto-refresh started in background. Page will reload in 15 seconds...',
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
