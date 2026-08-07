#!/usr/bin/env bash
set -euo pipefail

# Quick operational checks for Kerberos + landing page service.
URL="${1:-http://127.0.0.1:8888/cri-ccb-kmd}"

echo "=== Process on 8888 ==="
ss -ltnp | grep ':8888' || true

echo
echo "=== Ticket cache ==="
klist || true

echo
echo "=== Endpoint smoke ==="
# A 200 only confirms app is reachable; inspect body for auth errors too.
BODY="$(curl -sS "$URL" || true)"
echo "body-bytes: ${#BODY}"
if echo "$BODY" | grep -q "verify Kerberos auth on the server process"; then
  echo "WARN: page indicates Kerberos/live data issue"
else
  echo "OK: no Kerberos warning banner detected"
fi
