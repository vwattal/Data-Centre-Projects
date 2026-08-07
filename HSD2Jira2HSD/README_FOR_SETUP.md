# HSD2Jira2HSD — Setup Guide for New Maintainer

> **For: Sobin Thomas**
> This tool was built and maintained by vitasta. vitasta has
> left Intel and all credentials tied to his account are now inactive. Before
> anything else, read the **Credential Handover** section below — you need your
> own credentials in place before the tool will work at all.

This tool runs as a local Flask web server on your NUC and automates creating
Jira issues from HSD queries. Follow every step below in order.

---

## Credential Handover (Read This First)

The previous maintainer's Intel account (IDSID: `vitasta`) has been deactivated.
This means:

| Credential | Status | Action needed |
|---|---|---|
| Jira PAT in `.jira_token` | **Dead** — token revoked when account was deactivated | Create your own PAT (see below) |
| Kerberos ticket | **Dead** — only valid during an active Intel session | Run `kinit` with your own IDSID |

**These two things must be done before the tool can do anything useful.**
The tool will start and load the UI without them, but every HSD query and every
Jira issue creation will fail with 401 errors.

### Step A — Create your Jira PAT right now

1. Open: https://jira.devtools.intel.com/secure/ViewProfile.jspa
2. Click **Personal Access Tokens** in the left sidebar
3. Click **Create token** → name it something like `hsd2jira-nuc` → set expiry
   to **No expiry** (or as long as your team allows)
4. Copy the token — you only see it once
5. Paste it onto the NUC:
   ```bash
   echo "YOUR_NEW_TOKEN_HERE" > /path/to/HSD2Jira2HSD/.jira_token
   ```

No service restart needed — the token is read from disk on every request.

### Step B — Run `kinit` with your IDSID

```bash
kinit sobintha@AMR.CORP.INTEL.COM   # replace with your actual IDSID
klist                                # verify ticket is valid
```

You will need to re-run this after reboots or after the ticket expires (~24h).
See the Kerberos section below for auto-renewal options.

### Why does the PAT "never expire" setting not help here?

The "never expire" setting only means Jira itself won't time out the token on a
schedule. However, **the token is permanently tied to the Intel account that
created it**. The moment that account is deactivated by Intel IT (on the
employee's last day), every PAT issued by that account is revoked, regardless of
the expiry setting. There is no way around this — you must create a fresh PAT
under your own account.

---

## What Files to Change on This Codebase

Only two files need to be updated when setting this up on a new machine.
Everything else in the source code is portable.

### 1. `.jira_token` (mandatory)

This file is **not in git** (gitignored). It must be created manually on every
machine. Replace it with your own Jira PAT:

```bash
echo "YOUR_PAT_HERE" > .jira_token
```

### 2. `~/.config/systemd/user/hsd2jira.service` (if running as a service)

This file lives outside the repo (in your home directory). Copy the template
and update the path:

```bash
cp hsd2jira.service.example ~/.config/systemd/user/hsd2jira.service
nano ~/.config/systemd/user/hsd2jira.service
```

Change this one line to match where you cloned the repo:
```
WorkingDirectory=/home/YOUR_USERNAME/path/to/HSD2Jira2HSD
```

### What you do NOT need to change

- **`config.py`** — `DEFAULT_ASSIGNEE` is set to `mtangri` and should stay that
  way. Sobin is the tool maintainer but Jira stories are still assigned to
  mtangri (Mansi Tangri). Do not change this unless the team decides otherwise.
- All other source files — no machine-specific paths or credentials anywhere.

---

## Kerberos in Detail

### What Kerberos is and why the tool needs it

HSD (hsdes.intel.com) does not use username/password or tokens. It uses Intel's
internal Kerberos SSO. When the tool calls the HSD REST API, the `requests-kerberos`
library automatically attaches a proof ticket from your system's Kerberos
credential cache. Intel verifies the ticket server-side and allows the request.

**No password is stored anywhere in the code.** The only thing required is a
valid ticket in your active session.

### How to get a Kerberos ticket

```bash
kinit YOUR_IDSID@AMR.CORP.INTEL.COM
# Enter your Intel network password when prompted
klist   # verify — shows expiry time
```

### How long it lasts

Intel Kerberos tickets are typically valid for **24 hours**. After that, HSD
calls will return 401 and the tool will log auth errors. The fix is simply to
run `kinit` again.

### After a reboot

The credential cache is cleared on reboot. You must run `kinit` again before
the service will be able to reach HSD. The service itself will still start — it
just won't be able to fetch HSD data until a valid ticket exists.

### Auto-renewal (do this — it saves manual work)

A renewal script is included in `scripts/auto_renew_kerberos.sh`. It tries a
soft renewal first (`kinit -R`, no password), and if the ticket has fully expired
it re-authenticates using a password file you create once.

**One-time setup:**

```bash
# 1. Store your Intel password in a protected file
echo 'YOUR_INTEL_PASSWORD' > ~/.kerberos_password
chmod 600 ~/.kerberos_password

# 2. Add the cron job (runs every 6 hours)
crontab -e
# Add this line (update the path to match where you cloned the repo):
0 */6 * * * /home/YOUR_USERNAME/HSD2Jira2HSD/scripts/auto_renew_kerberos.sh
```

The script logs to `/tmp/kerberos_renew_hsd2jira.log` — check there if
something seems wrong.

After this, Sobin never needs to think about Kerberos again unless the NUC is
rebuilt or his Intel password changes.

### Kerberos for the systemd service

A user-level systemd service (`systemctl --user`) runs under your user session
and automatically shares your Kerberos credential cache. No special config is
needed — if `klist` shows a valid ticket in your terminal, the service can use it.

---

## How Authentication Works

The tool talks to **two separate systems**, each with its own auth method:

### 1. HSD (hsdes.intel.com) — Kerberos

HSD uses Intel's internal Kerberos single-sign-on. When your code makes an HTTP
request to `hsdes.intel.com`, it attaches a Kerberos ticket from your system's
credential cache. The tool uses `requests-kerberos` with
`mutual_authentication=OPTIONAL`, which means:

- It automatically picks up the ticket from your active `kinit` session.
- No username/password is ever stored in the code.
- If no valid ticket exists, HSD returns a 401 and the tool logs an auth error.

**You must run `kinit` on the NUC before starting the service** (and renew it
before it expires — typically every 8–10 hours, though Intel tickets are usually
24 hours):

```bash
kinit YOUR_IDSID@AMR.CORP.INTEL.COM
# Enter your Intel network password when prompted
# Check ticket is valid:
klist
```

> **Important for systemd services**: A user-level systemd service shares the
> Kerberos credential cache of the user session it runs under. As long as you
> have a valid ticket in your session, the service will use it automatically.
> If the NUC reboots or the ticket expires, run `kinit` again and restart the
> service.

To auto-renew tickets (optional but recommended for long-running NUCs), you can
add a cron job:
```bash
crontab -e
# Add: renew every 6 hours
0 */6 * * * kinit -R 2>/dev/null || kinit YOUR_IDSID@AMR.CORP.INTEL.COM < /dev/null
```
Note: `kinit -R` only works if your ticket was issued with the renewable flag.
For a fully unattended setup, use a keytab file (ask your Intel IT admin).

---

### 2. Jira (jira.devtools.intel.com) — Personal Access Token (PAT)

Jira uses a long-lived Bearer token stored in a plain text file:

```
HSD2Jira2HSD/.jira_token
```

This file is excluded from git (see `.gitignore`) and must be created manually
on each machine.

**How to create your Jira PAT:**
1. Go to: https://jira.devtools.intel.com/secure/ViewProfile.jspa
2. Click **Personal Access Tokens** in the left sidebar
3. Click **Create token** → give it a name (e.g. `hsd2jira-nuc`) → set expiry
   to **No expiry** (recommended so the tool doesn't silently break)
4. Copy the token string — it is only shown once
5. Paste it into `.jira_token`:
   ```bash
   echo "YOUR_TOKEN_HERE" > .jira_token
   ```

The token has no newline issues as long as you use `echo` above. The tool reads
it with `.strip()` so trailing whitespace is safe.

> **Important — What happens when the PAT expires or is revoked:**
> The tool does not crash. Instead, every Jira issue creation attempt in that
> run returns a `Jira 401` error entry in the UI results table. Nothing is
> created in Jira and nothing is written back to HSD. Fix: replace `.jira_token`
> with a valid token — no restart needed, it takes effect on the next request.

> **Important — When you leave Intel:**
> If you hand this tool to another person later, be aware that your PAT will be
> revoked the day your Intel account is deactivated. The next maintainer must
> create their own PAT and replace `.jira_token`, exactly as described above.
> They will also need to run `kinit` with their own IDSID. Point them to the
> **Credential Handover** section at the top of this file.

---

## Step-by-Step Setup on a New NUC

### Prerequisites

| Item | Check |
|---|---|
| Ubuntu 22.04+ (or similar) | |
| Python 3.12+ | `python3 --version` |
| Intel VPN or on Intel network | Required for HSD + Jira |
| Kerberos client installed | `which kinit` |
| `git` installed | `which git` |

Install Kerberos client if missing:
```bash
sudo apt install krb5-user -y
# When prompted for realm: AMR.CORP.INTEL.COM
# KDC: kdc.amr.corp.intel.com
# Admin server: kdc.amr.corp.intel.com
```

---

### 1. Clone the Repository

```bash
cd ~
git clone <your-github-repo-url> HSD2Jira2HSD
cd HSD2Jira2HSD
```

---

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs: `flask`, `requests`, `requests-kerberos`, `urllib3`.

---

### 3. Create the Jira Token File

```bash
cp .jira_token.example .jira_token
# Now edit it:
echo "YOUR_JIRA_PAT_HERE" > .jira_token
```

---

### 4. Review config.py

Open `config.py` — this is the only file you may need to change:

```python
JIRA_BASE    = 'https://jira.devtools.intel.com'  # Jira URL (unlikely to change)
JIRA_PROJECT = 'VLK'                               # Jira project key
DEFAULT_ASSIGNEE  = 'mtangri'                      # Change to your IDSID
DEFAULT_COMPONENT = 'XeKMD'                        # Change if needed
```

The Jira custom field IDs (`customfield_*`) are Intel-internal and should not
need to change unless the Jira admin modifies them.

---

### 5. Initialise Kerberos

```bash
kinit YOUR_IDSID@AMR.CORP.INTEL.COM
# Verify:
klist
```

You should see a ticket valid for several hours.

---

### 6. Run the Tool

**Option A — directly (for testing):**
```bash
python3 app.py
```
Tool will be available at: http://localhost:8889/hsd2jira

**Option B — as a systemd user service (recommended for a always-on NUC):**
```bash
# Copy and edit the service file
mkdir -p ~/.config/systemd/user
cp hsd2jira.service.example ~/.config/systemd/user/hsd2jira.service

# Edit WorkingDirectory to your actual path
nano ~/.config/systemd/user/hsd2jira.service
# Change: WorkingDirectory=/home/YOUR_USERNAME/path/to/HSD2Jira2HSD

# Enable and start
systemctl --user daemon-reload
systemctl --user enable hsd2jira.service
systemctl --user start hsd2jira.service

# Check it's running
systemctl --user status hsd2jira.service

# View logs
tail -f /tmp/hsd2jira.log
```

---

### 7. Verify

Open a browser and go to:
```
http://localhost:8889/hsd2jira
```

Or from another machine on the same network:
```
http://<NUC-IP-ADDRESS>:8889/hsd2jira
```

You should see the HSD2Jira tool UI. Paste any HSD query URL or article URL to
test.

---

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| HSD calls return 401 | Kerberos ticket expired | Run `kinit` again, restart service |
| Jira calls return 401 | PAT expired or wrong | Regenerate PAT, update `.jira_token` |
| `ModuleNotFoundError: requests_kerberos` | Dependencies not installed | `pip install -r requirements.txt` |
| Port 8889 already in use | Another process running | `fuser -k 8889/tcp` then restart |
| Tool shows but HSD queries return nothing | VPN not connected | Connect to Intel VPN |

---

## Restarting After Changes

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemctl --user restart hsd2jira.service
systemctl --user is-active hsd2jira.service   # should print: active
```

---

## What NOT to commit to GitHub

The `.gitignore` already excludes these, but be aware:
- `.jira_token` — your Jira PAT (treat like a password)
- `hsd2jira_trend_cron.json` / `hsd2jira_cron.json` — runtime state
- `*.db` — local SQLite cache files
- `*.log` — log files
