#!/usr/bin/env bash
# Auto-renew Kerberos ticket for data centre landing page
# Run this via cron every 6-8 hours to maintain valid credentials

set -euo pipefail

LOG_FILE="/tmp/kerberos_auto_renew.log"
PRINCIPAL="${KERBEROS_PRINCIPAL:-vwattal@AMR.CORP.INTEL.COM}"
PASSWORD_FILE="${HOME}/.kerberos_password"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Check if ticket exists and is valid
if klist -s 2>/dev/null; then
    REMAINING=$(klist | grep -oP 'Expires:\s+\K.*' | head -1 || echo "")
    log "Ticket still valid until: $REMAINING"
    
    # Only renew if less than 2 hours remaining
    if ! klist -s 2>&1 | grep -q "Credentials cache file.*not found"; then
        kinit -R 2>/dev/null && log "Ticket renewed successfully" && exit 0
    fi
fi

# Ticket expired or doesn't exist - need full re-authentication
if [[ -f "$PASSWORD_FILE" ]]; then
    log "Ticket expired, re-authenticating with stored credentials..."
    if kinit "$PRINCIPAL" < "$PASSWORD_FILE" 2>/dev/null; then
        log "✓ Kerberos ticket obtained successfully"
        klist | tee -a "$LOG_FILE"
        exit 0
    else
        log "✗ Failed to obtain ticket with stored credentials"
        exit 1
    fi
else
    log "✗ Password file not found at $PASSWORD_FILE"
    log "Run: echo 'YOUR_PASSWORD' > $PASSWORD_FILE && chmod 600 $PASSWORD_FILE"
    exit 1
fi
