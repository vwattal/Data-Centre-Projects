"""
Rate-limiting and HTTP security headers.
Call register_security(app) once in app.py.
"""
import time
import logging
from collections import defaultdict
from flask import request, jsonify

REQUEST_LIMIT  = 500   # requests per minute per IP
BLOCK_DURATION = 300   # seconds to block after limit exceeded

_rate_limit_data: dict = defaultdict(list)
_blocked_ips:     dict = {}

logger = logging.getLogger(__name__)


def check_rate_limit(ip: str) -> bool:
    if ip in _blocked_ips:
        if time.time() < _blocked_ips[ip]:
            return False
        del _blocked_ips[ip]

    now = time.time()
    _rate_limit_data[ip] = [t for t in _rate_limit_data[ip] if now - t < 60]

    if len(_rate_limit_data[ip]) >= REQUEST_LIMIT:
        _blocked_ips[ip] = now + BLOCK_DURATION
        logger.warning(f"Rate limit exceeded for IP: {ip}. Blocked for {BLOCK_DURATION}s")
        return False

    _rate_limit_data[ip].append(now)
    return True


def register_security(app) -> None:
    """Attach rate-limit and security-header hooks to the Flask app."""

    @app.before_request
    def _rate_limit_check():
        if request.path == '/health':
            return None
        ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        if not check_rate_limit(ip):
            logger.warning(f"Blocked request from {ip} to {request.path}")
            return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429
        return None

    @app.after_request
    def _add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response
