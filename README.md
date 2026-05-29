# Data Centre Landing Page

Central Flask dashboard for Data Centre Graphics & Compute Engineering (CRI and JGS divisions).
Runs on port 8888. Each tool is a Flask Blueprint under `routes/`.

---

## Quick Start (new machine setup)

### 1. Prerequisites

- Python 3.12+
- Kerberos credentials active (`kinit <username>`)
- Access to Intel internal network (or VPN)

### 2. Clone and install

```bash
git clone <repo-url> data_centre_landing
cd data_centre_landing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Update `config.py`

Open `config.py` and adjust the paths for your machine:

| Setting | What to change |
|---------|---------------|
| `SOC_AUTOMATION_DIR` | Path to your `soc_automation/` directory |
| `CRI_WEEKLY_DIR` | Path to your local CRI Weekly review folder |
| `JIRA_TOKEN_PATH` | Path to your `.jira_token` for Jira API |
| `BACKLOG_TOKEN_PATH` | Path to the HSD2Jira2HSD `.jira_token` |
| `HSD2JIRA_TOKEN_PATH` | Path to this app's own `.jira_token` |
| `APP_BASE_URL` | URL this app is reachable at (used in email links) |

### 4. Create a Jira token

Get a Personal Access Token from https://jira.devtools.intel.com → Profile → Personal Access Tokens.
Write it to the file referenced by `HSD2JIRA_TOKEN_PATH` (default: `data_centre_landing/.jira_token`):

```bash
echo "your-token-here" > .jira_token
chmod 600 .jira_token
```

### 5. HSD2Jira2HSD sibling repo

The HSD→Jira automation tool lives in a separate repo loaded dynamically at startup.
It must be cloned as a sibling directory:

```
Projects/
├── data_centre_landing/   ← this repo
└── HSD2Jira2HSD/          ← must be present
```

Clone it:
```bash
cd ..
git clone <hsd2jira-repo-url> HSD2Jira2HSD
```

### 6. Start the server

```bash
cd data_centre_landing
nohup python3 app.py >> /tmp/dc_landing.log 2>&1 &
echo "Started. Logs: /tmp/dc_landing.log"
```

Or for foreground development:
```bash
python3 app.py
```

App will be available at `http://<your-host>:8888`.

---

## Project structure

```
data_centre_landing/
├── app.py                      # Flask app — registers all blueprints
├── config.py                   # All paths and settings (edit for your machine)
├── requirements.txt
├── routes/                     # One blueprint per tool
│   ├── index.py                # Landing page (/)
│   ├── api.py                  # Shared API endpoints (/api/*, /static/*)
│   ├── backlog_orchestrator.py # Jira backlog ranking (/backlog-orchestrator)
│   ├── cri_ccb.py              # CRI CCB strawman dashboard (/cri-ccb)
│   ├── cri_daily_tf.py         # CRI Daily TF pending features (/cri-daily-tf)
│   ├── cri_e2e.py              # CRI E2E plan (/cri-e2e)
│   ├── hsd2jira.py             # Loader stub → ../HSD2Jira2HSD/routes/hsd2jira.py
│   ├── jgs_4plus2.py           # JGS 4+2 tracking dashboard (/jgs-4plus2)
│   ├── jgs_bug_triage.py       # JGS HSD bug triage (/jgs-bug-triage)
│   ├── jgs_emu_bugs.py         # JGS emulation bugs (/jgs-emu-bugs)
│   ├── jgs_soc_updates.py      # JGS SOC driver schedule (/jgs-soc-updates)
│   └── jgs_upstreaming.py      # JGS upstream patch tracking (/jgs-upstreaming)
├── templates/                  # Jinja2 HTML templates (one per route)
├── static/                     # CSS / JS / images
└── utils/
    ├── helpers.py              # parse_ww, extract_jira_keys, current_ww_info
    ├── cri_helpers.py          # refresh_pptx, extract_jiras, chips
    └── security.py             # register_security (CSP headers)
```

---

## Tools

| URL | Blueprint | Description |
|-----|-----------|-------------|
| `/` | `index` | Landing page — card grid linking to all tools |
| `/jgs-soc-updates` | `jgs_soc` | JGS SOC driver schedule — reads `JGS_SOC_Trend.xlsx` from SharePoint, shows task/UAL tracking with trend graphs |
| `/jgs-upstreaming` | `jgs_upstream` | JGS upstream patch plan — fetches Jira filter 452639, cumulative WW graph |
| `/jgs-bug-triage` | `jgs_bug_triage` | JGS emulation bug triage — HSD query, component routing, arch-escalation email |
| `/jgs-emu-bugs` | `jgs_emu_bugs` | JGS emulation bugs dashboard — HSD query 14027480453, Teams DM @mention notifications |
| `/jgs-4plus2` | `jgs_4plus2` | JGS 4+2 tracking — SharePoint Excel (Rodrigo's OneDrive), HSD title lookup, shared SQLite edits |
| `/cri-e2e` | `cri_e2e` | CRI E2E plan — Excel (weekly_updates / GT_DCN / KMD tabs) + PPTX (XPUM/Sysman) |
| `/cri-ccb` | `cri_ccb` | CRI CCB strawman tracker — HSD query 13013902803, AR child sign-off status |
| `/cri-daily-tf` | `cri_daily_tf` | CRI Daily TF pending features — SharePoint Excel, editable table, PowerPoint export |
| `/backlog-orchestrator` | `backlog` | Jira backlog ranking — rule-based P1–P4 bucketing, agile board re-rank |
| `/hsd2jira` | `hsd2jira` | HSD→Jira automation UI — loaded from `../HSD2Jira2HSD/` |

External links on the landing page (separate services, not in this repo):
- `http://<host>:5050` — CRI Weekly Updates
- `http://<host>:8080` — CRI WA Dashboard

---

## Authentication

- **Kerberos** — used by all HSD API calls. Run `kinit` before starting the server.
- **Jira Bearer token** — stored in `.jira_token` files (see `config.py`). Never committed.
- **Microsoft Graph (SharePoint)** — token refresh scripts live in `soc_automation/`. The app calls them as subprocesses when auto-refreshing Excel files.

---

## Server management

```bash
# Start (background)
nohup python3 app.py >> /tmp/dc_landing.log 2>&1 &

# Check running
fuser 8888/tcp

# Stop
fuser -k 8888/tcp

# Logs
tail -f /tmp/dc_landing.log
```

---

## Runtime files (not committed)

| File | Created by | Purpose |
|------|-----------|---------|
| `*.db` | App on first run | SQLite caches for CCB, Daily TF, 4+2 |
| `user_data.json` | App | Shared user notes (upstreaming comments, key messages) |
| `jira_tracking_notes.json` | App | Jira tracking design notes |
| `jira_tracking_rules.json` | App | Jira tracking rule definitions |
| `hsd2jira_cron.json` | HSD2Jira | Cron enable/disable state |
| `hsd2jira_cron_log.json` | HSD2Jira | Cron run log |
| `routes/webhook_config.json` | App | Teams webhook URL for @mention DMs |
