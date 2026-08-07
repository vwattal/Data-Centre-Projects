# Kerberos Handoff Runbook (data_centre_landing)

This service depends on live Kerberos tickets for HSD/HSDES endpoints.
If tickets expire, dashboards can show stale or fallback data.

## Goal

Run app.py under auto-renewed Kerberos credentials so ownership transition is safe.

## Prerequisites

- Service principal created by IT/SRE (example: svc_dc_landing@REALM)
- Keytab issued for that principal
- Keytab stored with strict permissions on host
- k5start installed (present on this host)

## One-time setup

1. Place keytab in a protected location
- Example: /home/vitasta/.keytabs/svc_dc_landing.keytab
- Permissions:
  - chmod 600 /home/vitasta/.keytabs/svc_dc_landing.keytab

2. Export required env vars (or place in systemd EnvironmentFile)
- KRB5_PRINCIPAL=svc_dc_landing@REALM
- KRB5_KEYTAB=/home/vitasta/.keytabs/svc_dc_landing.keytab
- APP_DIR=/home/vitasta/triage/repos/Projects/data_centre_landing

3. Start via wrapper script
- scripts/start_landing_with_kerberos.sh

## Recommended systemd unit (user service)

Use this as a template in ~/.config/systemd/user/landing-page.service

[Unit]
Description=Data Centre Landing Page (Kerberos auto-renew)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/vitasta/triage/repos/Projects/data_centre_landing
Environment=APP_DIR=/home/vitasta/triage/repos/Projects/data_centre_landing
Environment=APP_LOG=/tmp/dc_landing.log
Environment=K5START_LOG=/tmp/dc_landing_k5start.log
Environment=K5START_RENEW_MIN=10
Environment=KRB5_PRINCIPAL=svc_dc_landing@REALM
Environment=KRB5_KEYTAB=/home/vitasta/.keytabs/svc_dc_landing.keytab
ExecStart=/home/vitasta/triage/repos/Projects/data_centre_landing/scripts/start_landing_with_kerberos.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target

Then run:
- systemctl --user daemon-reload
- systemctl --user enable --now landing-page.service
- systemctl --user status landing-page.service

## Operational checks

Run:
- scripts/check_landing_kerberos.sh

Look for:
- port 8888 listener active
- valid ticket in klist output
- no Kerberos warning text on /cri-ccb-kmd

## Transition checklist

Before handoff:
1. Transfer service principal ownership process and keytab rotation owner.
2. Document keytab path and renewal policy.
3. Verify new owner can run:
- klist
- systemctl --user status landing-page.service
- scripts/check_landing_kerberos.sh
4. Confirm dashboards that require Kerberos (cri-ccb, cri-ccb-kmd, jgs-bug-triage).

## Incident quick fix

If live data disappears:
1. Check ticket: klist
2. Restart service: systemctl --user restart landing-page.service
3. Re-check endpoint and logs:
- tail -f /tmp/dc_landing.log
- tail -f /tmp/dc_landing_k5start.log
