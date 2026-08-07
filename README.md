# Data Centre Landing Page

Central Flask dashboard for Data Centre Graphics & Compute Engineering (CRI and JGS divisions).
Runs on port 8888. Each tool is a Flask Blueprint under `routes/`.

---

## Hosting & Handover

- **Live URL:** http://10.88.27.190:8888/
- **NUC credentials:** username and password with Rajesh Ramachandran
- **Code repository:** https://github.com/intel-sandbox/DataCentreProject
- **Kerberos:** whoever takes over must ensure the Kerberos ticket is active and auto-renewal is configured — see `scripts/auto_renew_kerberos.sh`

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

| URL | Blueprint | Description | Data sources |
|-----|-----------|-------------|--------------|
| `/` | `index` | Landing page — card grid linking to all tools | None — static render |
| `/jgs-soc-updates` | `jgs_soc` | JGS SOC RTL Freeze schedule. Cumulative WW trend graphs (Code Complete / Coral / Palladium), long-pole table, pending tasks, per-category drill-down, UAL schedule. Editable task comments shared across users. | **Excel:** `soc_automation/JGS_SOC_Trend.xlsx` (auto-downloaded from SharePoint, sheets `RTLFreeze` + `UAL_TI_SCHEDULE`). **SQLite:** `soc_automation/soc_edits.db` for comments/UAL edits. **Jira REST API:** blocked-by links for PSOC/Boot categories. |
| `/jgs-upstreaming` | `jgs_upstream` | JGS upstream kernel patch plan. Issue table with target WW, cumulative WW trend graph, pending issues (target WW passed but still open), per-issue editable comments. | **Jira REST API:** filter `452639`, custom field `customfield_34504` for target WW. Token: `config.JIRA_TOKEN_PATH`. Comments saved to `user_data.json`. |
| `/jgs-bug-triage` | `jgs_bug_triage` | JGS hardware/emulation bug triage. Bugs grouped by component (XeSim, KMD, IGC, PISA, Test, Compute Dev), days-open counter, component ownership history trail. Flags bugs open > 14 days without root cause. "Notify Arch" button sends escalation email via `smtpmail.intel.com`. | **HSD query:** `14026739770` (tenant `ip_hw_graphics`, subject `bugeco`). Enrichment via HSD ESService `get_record_by_id` + history API per article. Kerberos auth. |
| `/jgs-emu-bugs` | `jgs_emu_bugs` | JGS emulation-specific bug dashboard. Bugs with priority grouping, component ownership trail, per-bug comments. Supports Teams @mention DM notifications — user types `@username`, server POSTs to a Power Automate HTTP trigger which sends a native 1-on-1 Teams DM. | **HSD query:** `14027480453`. Kerberos auth. Teams webhook URL stored in `routes/webhook_config.json` (set once via the UI). |
| `/jgs-4plus2` | `jgs_4plus2` | JGS 4+2 tracking dashboard. HSD + Jira rows per owner (Rodrigo→xeKMD, Erez→UALkmd, Mrozek→UMD-L0, Santosh→Sysman). Editable ETA and Comments persisted in SQLite, shared across users with 60-second polling. | **Excel:** Rodrigo Vivi's personal OneDrive (SharePoint), downloaded via Microsoft Graph API. **HSD REST API** (Kerberos) for title lookup. **SQLite:** `../JGS_4+2/jgs4plus2.db`. |
| `/cri-e2e` | `cri_e2e` | CRI E2E plan. Two tables: (1) CRI HSD features with linked Jira chips and BMG support flag, (2) XPUM/Sysman features with status, dependencies, ETAs, remarks. Per-row editable comments. | **Excel:** `config.CRI_EXCEL` (`CRI Pre-Map Day.xlsx`) — sheets `weekly_updates` (rows 1–46), `GT_DCN`, `KMD` (cols H–L for Jira cross-ref). **PPTX:** `config.CRI_PPTX` (`CRI_XPUM_E2E_FEATURE_REVIEW.pptx`) slides 1–5 for XPUM/Sysman (downloaded from SharePoint via Graph API). Comments in `config.E2E_COMMENTS` (`e2e_comments.json`). |
| `/cri-ccb` | `cri_ccb` | CRI CCB strawman HSD tracker. Shows per-component AR sign-off status (XeKMD, Sysman, XPUM, E2E) for each strawman feature. Stale indicator if cache > 30 min old. | **HSD query:** `13013902803` (tenant `server_platf`, subject `feature`). AR children fetched via HSD ESService `get_related_records`, sign-off status via HSD REST per AR. Kerberos auth. Results cached in **SQLite:** `cri_ccb.db`. |
| `/cri-ccb-kmd` | `cri_ccb_kmd` | CRI CCB KMD-only view. XeKMD sign-off status, impact scope, scoping ETA, effort estimate and Jira link per strawman HSD. Shows `Pending ETA` where scoping is in progress. Background refresh with 8-second auto-reload only when cache is empty (not on every load). | **HSD query:** same CRI CCB query. **Jira REST API** for linked story status. Kerberos + Jira token. Cached in **SQLite:** `cri_ccb_kmd.db`. |
| `/cri-daily-tf` | `cri_daily_tf` | CRI Daily TF pending driver features. Two editable tables (XPUM/Sysman and XeKMD). All columns (Feature, Priority, TF Meeting Minutes, ETA, Blocker) editable inline and persisted server-side. 30-second polling for multi-user sync. PowerPoint export (`/export-slides`). | **Excel:** SharePoint "Pending Driver Features enabling.xlsx" (Crescent Island site), sheets `Xpum_Sysman` and `XeKMD_pending`. Downloaded via Microsoft Graph API, cached in `/tmp/`. Edits in **SQLite:** `cri_daily_tf.db`. |
| `/backlog-orchestrator` | `backlog` | Jira backlog manager. Fetches issues by scope (My Backlog / Entire Project / Board / custom JQL), applies text-based rules to bucket into P1–P4, optionally re-ranks issues on the Jira agile board. | **Jira REST API:** project VLK. Token: `config.BACKLOG_TOKEN_PATH` (`../HSD2Jira2HSD/.jira_token`). |
| `/hsd2jira` | `hsd2jira` | HSD→Jira automation. Converts Intel HSD `dg_soc.feature` articles into a 9-issue Jira hierarchy (Epic → Code Complete parent/child stories → Sim/Emu stories → Upstream task/story). Three flows: bulk HSD query, single HSD link, or manual entry. Writes Jira key back to HSD AR child. Includes a background cron. | Loaded dynamically from `../HSD2Jira2HSD/`. **HSD REST + ESService** (Kerberos). **Jira REST API**, token at `config.HSD2JIRA_TOKEN_PATH`. |

External links and services referenced on the landing page (not in this repo):

| Card | Target | Notes |
|---|---|---|
| GT IP (JGS section) | SharePoint Excel | Static link — no server needed. Update the URL in `templates/index.html` if the file moves. |
| GT SW E2E plan for EXI | `http://10.88.27.190:8889/xe5-dcn-status` | Served by the **HSD2JiraTool** Flask app on port **8889** — separate process. See `github.com/intel-sandbox/…` (HSD2JiraTool repo). Must be running independently. |
| CRI WA Dashboard | `http://10.88.27.190:8080/` | Separate service — not in this repo. |
| Reference Links row | Various SharePoint/Teams URLs | Static links only — no server dependency. Update URLs in `templates/index.html` if they change. |

---

## Authentication

- **Kerberos** — used by all HSD API calls. Run `kinit` before starting the server.
- **Jira Bearer token** — stored in `.jira_token` files (see `config.py`). Never committed.
- **Microsoft Graph (SharePoint)** — token refresh scripts live in `soc_automation/`. The app calls them as subprocesses when auto-refreshing Excel files.

For production-safe ownership transition and auto-renewed Kerberos setup, see:
- `docs/KERBEROS_HANDOFF.md`
- `scripts/start_landing_with_kerberos.sh`
- `scripts/check_landing_kerberos.sh`

---

## Credentials & Tokens Checklist (for new maintainer)

When taking over this tool, every credential below is tied to the **previous owner's Intel account** and will stop working the day that account is deactivated. Replace each one under your own account before going live.

| Credential | Used by | Where it lives | What to do |
|---|---|---|---|
| **Kerberos ticket** | All HSD pages (`/cri-ccb`, `/cri-ccb-kmd`, `/jgs-bug-triage`, `/jgs-emu-bugs`, `/jgs-4plus2`, `/hsd2jira`) | OS credential cache (not a file) | Run `kinit YOUR_IDSID@AMR.CORP.INTEL.COM`. Set up cron auto-renewal via `scripts/auto_renew_kerberos.sh` |
| **Jira PAT** (this app) | `/jgs-upstreaming`, `/backlog-orchestrator`, `/hsd2jira`, `/cri-ccb-kmd` | `.jira_token` (path set in `config.py` → `HSD2JIRA_TOKEN_PATH`) | Create new PAT at jira.devtools.intel.com → Profile → Personal Access Tokens. `echo "TOKEN" > .jira_token` |
| **Jira PAT** (HSD2JiraTool) | `/backlog-orchestrator` uses the sibling repo's token | `../HSD2Jira2HSD/.jira_token` (path set in `config.py` → `BACKLOG_TOKEN_PATH`) | Same as above — separate file in the HSD2JiraTool repo |
| **Microsoft Graph token** (SharePoint) | `/jgs-soc-updates` (JGS_SOC_Trend.xlsx), `/cri-e2e` (PPTX), `/cri-daily-tf` (pending features Excel), `/jgs-4plus2` (Rodrigo's OneDrive) | Token refresh handled by scripts in `soc_automation/` | Run the token-refresh script in `soc_automation/` — this generates a new access token via device-code flow or stored refresh token. Ask previous maintainer for the client ID / tenant details. |
| **Teams webhook URL** | `/jgs-emu-bugs` — @mention DM notifications | `routes/webhook_config.json` (gitignored) | Open the JGS Emu Bugs page → settings → paste your team's Power Automate HTTP trigger URL. |

> **Note on Jira PAT expiry:** Even a token set to "never expire" is revoked the moment the issuing Intel account is deactivated. Always create tokens under your own account.

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

Recommended for durable operations: run via `k5start` wrapper instead of raw `nohup`.
See `docs/KERBEROS_HANDOFF.md`.

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
