#!/usr/bin/env bash
set -euo pipefail

# Starts data_centre_landing with auto-renewed Kerberos tickets via k5start.
# Required env vars:
#   KRB5_PRINCIPAL   e.g. svc_dc_landing@CORP.INTEL.COM
#   KRB5_KEYTAB      e.g. /home/vitasta/.keytabs/svc_dc_landing.keytab
# Optional env vars:
#   APP_DIR          default: /home/vitasta/triage/repos/Projects/data_centre_landing
#   APP_LOG          default: /tmp/dc_landing.log
#   K5START_LOG      default: /tmp/dc_landing_k5start.log
#   K5START_RENEW_MIN default: 10 (minutes)

APP_DIR="${APP_DIR:-/home/vitasta/triage/repos/Projects/data_centre_landing}"
APP_LOG="${APP_LOG:-/tmp/dc_landing.log}"
K5START_LOG="${K5START_LOG:-/tmp/dc_landing_k5start.log}"
K5START_RENEW_MIN="${K5START_RENEW_MIN:-10}"

if [[ -z "${KRB5_PRINCIPAL:-}" || -z "${KRB5_KEYTAB:-}" ]]; then
  echo "ERROR: KRB5_PRINCIPAL and KRB5_KEYTAB must be set."
  exit 1
fi

if [[ ! -f "$KRB5_KEYTAB" ]]; then
  echo "ERROR: keytab not found at $KRB5_KEYTAB"
  exit 1
fi

mkdir -p "$(dirname "$APP_LOG")" "$(dirname "$K5START_LOG")"

cd "$APP_DIR"

# Stop any prior instance on port 8888 to avoid split-brain serving.
pkill -f "python3 app.py" || true

# k5start options:
# -U : create/refresh user ticket cache
# -f : keytab path
# -K : renew interval in minutes
# -l : ticket lifetime request (10h)
# -- : command to run under refreshed credentials
exec /usr/bin/k5start -U -f "$KRB5_KEYTAB" -K "$K5START_RENEW_MIN" -l 10h \
  -- /usr/bin/python3 app.py >>"$APP_LOG" 2>>"$K5START_LOG"
