#!/usr/bin/env bash
# Ensures data centre landing page is running with valid Kerberos ticket
# Run via cron every 15-30 minutes for high availability

set -euo pipefail

APP_DIR="/home/vitasta/triage/repos/Projects/data_centre_landing"
APP_LOG="/tmp/dc_landing.log"
PORT=8888

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$APP_LOG"
}

# 1. Check if ticket is valid
if ! klist -s 2>/dev/null; then
    log "✗ Kerberos ticket expired - attempting renewal..."
    "$APP_DIR/scripts/auto_renew_kerberos.sh"
    sleep 2
fi

# 2. Check if app is running
if ss -ltn | grep -q ":$PORT "; then
    # Port is in use, verify it's responding
    if curl -s --max-time 5 "http://localhost:$PORT/cri-ccb" >/dev/null 2>&1; then
        log "✓ App running and responding on port $PORT"
        exit 0
    else
        log "⚠ Port $PORT in use but not responding - killing stale process"
        fuser -k ${PORT}/tcp || true
        sleep 2
    fi
fi

# 3. Start the app
log "Starting data centre landing page..."
cd "$APP_DIR"

# Kill any orphaned processes
pkill -f "python3 app.py" || true
sleep 1

# Start app in background
nohup python3 app.py >> "$APP_LOG" 2>&1 &
APP_PID=$!

log "✓ Started app.py with PID $APP_PID"

# Wait a moment and verify it started
sleep 3
if ss -ltn | grep -q ":$PORT "; then
    log "✓ App successfully listening on port $PORT"
else
    log "✗ Failed to start app on port $PORT"
    exit 1
fi
