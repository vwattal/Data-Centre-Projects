#!/usr/bin/env bash
# One-time setup for auto-renewal of Kerberos tickets
# This configures cron jobs to keep the landing page operational 24/7

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASSWORD_FILE="${HOME}/.kerberos_password"

echo "=========================================="
echo "Data Centre Landing - Auto-Renewal Setup"
echo "=========================================="
echo

# Step 1: Create password file
if [[ ! -f "$PASSWORD_FILE" ]]; then
    echo "Setting up secure password storage..."
    echo
    echo "⚠️  SECURITY NOTE: Your password will be stored in $PASSWORD_FILE"
    echo "    This file will have 600 permissions (readable only by you)"
    echo
    read -s -p "Enter your Kerberos password for vwattal@AMR.CORP.INTEL.COM: " PASSWORD
    echo
    
    echo "$PASSWORD" > "$PASSWORD_FILE"
    chmod 600 "$PASSWORD_FILE"
    echo "✓ Password file created with secure permissions"
    echo
else
    echo "✓ Password file already exists at $PASSWORD_FILE"
    echo
fi

# Step 2: Test credential renewal
echo "Testing Kerberos authentication..."
if "$SCRIPT_DIR/auto_renew_kerberos.sh"; then
    echo "✓ Kerberos ticket obtained successfully"
else
    echo "✗ Failed to obtain Kerberos ticket"
    echo "Please check your password in $PASSWORD_FILE"
    exit 1
fi
echo

# Step 3: Set up cron jobs
echo "Setting up cron jobs..."

# Remove any existing entries first
(crontab -l 2>/dev/null | grep -v "auto_renew_kerberos\|ensure_landing_running" || true) | crontab -

# Add new entries
(
    crontab -l 2>/dev/null || true
    echo "# Auto-renew Kerberos ticket every 6 hours"
    echo "0 */6 * * * $SCRIPT_DIR/auto_renew_kerberos.sh"
    echo "# Ensure landing page is running every 15 minutes"
    echo "*/15 * * * * $SCRIPT_DIR/ensure_landing_running.sh"
) | crontab -

echo "✓ Cron jobs installed:"
crontab -l | grep -E "auto_renew_kerberos|ensure_landing_running"
echo

# Step 4: Start the app now
echo "Starting the landing page with valid credentials..."
"$SCRIPT_DIR/ensure_landing_running.sh"
echo

echo "=========================================="
echo "✓ Setup complete!"
echo "=========================================="
echo
echo "Your dashboard will now:"
echo "  • Auto-renew Kerberos tickets every 6 hours"
echo "  • Auto-restart if the app crashes (checked every 15 min)"
echo "  • Stay operational 24/7 for executive viewing"
echo
echo "Useful commands:"
echo "  • Check status:  $SCRIPT_DIR/check_landing_kerberos.sh"
echo "  • View logs:     tail -f /tmp/dc_landing.log"
echo "  • View cron:     crontab -l"
echo
