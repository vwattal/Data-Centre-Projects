#!/usr/bin/env bash
# Auto-renew Kerberos ticket for HSD2Jira2HSD
# Runs every 6 hours via cron — keeps HSD API calls working unattended.
#
# Setup (one-time, run manually):
#   echo 'YOUR_INTEL_PASSWORD' > ~/.kerberos_password
#   chmod 600 ~/.kerberos_password
#
# Add to crontab (crontab -e):
#   0 */6 * * * /path/to/HSD2Jira2HSD/scripts/auto_renew_kerberos.sh
#
# Override principal via env if needed:
#   KERBEROS_PRINCIPAL=sobintha@AMR.CORP.INTEL.COM /path/to/script

LOG_FILE="/tmp/kerberos_renew_hsd2jira.log"
PRINCIPAL="${KERBEROS_PRINCIPAL:-sobintha@AMR.CORP.INTEL.COM}"
PASSWORD_FILE="${HOME}/.kerberos_password"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# Try a soft renewal first (no password needed)
if klist -s 2>/dev/null; then
    if kinit -R 2>/dev/null; then
        log "Ticket renewed (kinit -R): $PRINCIPAL"
        exit 0
    fi
    log "kinit -R failed — ticket not renewable, will re-authenticate"
fi

# Full re-authentication using stored password
if [[ ! -f "$PASSWORD_FILE" ]]; then
    log "ERROR: No ticket and no password file at $PASSWORD_FILE"
    log "Fix: echo 'YOUR_PASSWORD' > $PASSWORD_FILE && chmod 600 $PASSWORD_FILE"
    exit 1
fi

if kinit "$PRINCIPAL" < "$PASSWORD_FILE" 2>/dev/null; then
    log "Ticket obtained for $PRINCIPAL"
    klist 2>/dev/null | tee -a "$LOG_FILE"
    exit 0
else
    log "ERROR: kinit failed for $PRINCIPAL — wrong password or network issue"
    exit 1
fi
