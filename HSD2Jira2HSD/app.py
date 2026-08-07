#!/usr/bin/env python3
"""
HSD→Jira Web Tool — standalone entry point.

Runs the HSD→Jira automation tool as a self-contained Flask web app.
This is the canonical version of the tool intended for GitHub distribution.

Usage:
    python3 app.py

The tool will be available at http://localhost:8889/hsd2jira
(or http://<your-ip>:8889/hsd2jira)

Prerequisites:
    pip install -r requirements.txt
    kinit <your-ntlm-id>@AMR.CORP.INTEL.COM   # Kerberos auth for HSD
    echo "<your-jira-token>" > .jira_token

"""
import logging
import sys
from pathlib import Path
from flask import Flask, redirect, url_for

# Ensure this directory is on sys.path so 'from config import ...' works inside routes
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

# ── Register blueprints ───────────────────────────────────────────────────────
from routes.hsd2jira import bp as hsd2jira_bp  # noqa: E402
app.register_blueprint(hsd2jira_bp)


@app.route('/')
def index():
    """Root redirect → hsd2jira tool."""
    return redirect(url_for('hsd2jira.hsd2jira'))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = '127.0.0.1'

    port = 8889   # different from data_centre_landing (8888) to allow both to run
    print('=' * 60)
    print('  HSD→Jira Web Tool')
    print('=' * 60)
    print(f'  http://localhost:{port}/hsd2jira')
    print(f'  http://{local_ip}:{port}/hsd2jira')
    print('=' * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
