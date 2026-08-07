#!/usr/bin/env python3
"""
Data Centre Projects Landing Page — entry point.

Start via systemd (recommended):
    systemctl --user start landing-page.service

Or directly:
    python3 app.py
"""
import socket
import logging
import sys
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify

from utils.security import register_security

PROJECTS_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECTS_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECTS_ROOT))

# ── Create app ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

register_security(app)

# ── Register blueprints ───────────────────────────────────────────────────────
from routes.index               import bp as index_bp
from routes.jgs_soc_updates     import bp as jgs_soc_bp
from routes.api                 import bp as api_bp
from routes.cri_e2e             import bp as cri_e2e_bp
from routes.backlog_orchestrator import bp as backlog_bp
from routes.jgs_upstreaming     import bp as jgs_upstream_bp
from routes.hsd2jira            import bp as hsd2jira_bp
from routes.jgs_bug_triage      import bp as jgs_bug_triage_bp
from routes.cri_ccb             import bp as cri_ccb_bp
from routes.cri_ccb_kmd         import bp as cri_ccb_kmd_bp
from routes.jgs_emu_bugs       import bp as jgs_emu_bugs_bp
from routes.jgs_allbugs_sw     import bp as jgs_allbugs_sw_bp
from routes.jgs_4plus2          import bp as jgs_4plus2_bp
from routes.cri_daily_tf        import bp as cri_daily_tf_bp

for _bp in (
    index_bp, jgs_soc_bp, api_bp,
    cri_e2e_bp,
    backlog_bp, jgs_upstream_bp,
    hsd2jira_bp, jgs_bug_triage_bp, cri_ccb_bp, cri_ccb_kmd_bp,
    jgs_emu_bugs_bp, jgs_allbugs_sw_bp, jgs_4plus2_bp, cri_daily_tf_bp,
):
    app.register_blueprint(_bp)


# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/health')
def health_check():
    return jsonify({
        'status':    'healthy',
        'timestamp': datetime.now().isoformat(),
        'service':   'data-centre-landing-page',
    }), 200


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    hostname   = socket.gethostname()
    ip_address = socket.gethostbyname(hostname)
    print()
    print('=' * 70)
    print('🚀 Data Centre Projects Landing Page')
    print('=' * 70)
    print(f'   http://localhost:8888')
    print(f'   http://{ip_address}:8888')
    print('=' * 70)
    print()
    app.run(host='0.0.0.0', port=8888, debug=False)
