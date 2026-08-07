# HSD → Jira Automation Web Tool

## What this tool does

Every GPU feature or hardware workaround tracked in Intel's HSDES system requires a
corresponding set of Jira stories so the XeKMD driver team can track their
implementation work. Doing that by hand means creating 9 linked Jira issues per HSD
and then writing the Code Complete story key back to the HSD child record so the two
systems stay in sync.

This tool automates that entire workflow. It reads HSDs from a saved query, decides
whether each one needs Jira issues (and why it should be skipped if not), creates the
full 9-issue hierarchy in VLK, and writes the key back to HSD — all without leaving
the browser. A background cron job can do this continuously so nothing falls through.

Safe to re-run: every processing path checks whether Jira work already exists before
creating anything, so running the same query twice never produces duplicates.

---

## Key concepts

**HSDES / HSD** — Intel's internal bug and feature tracking system at `hsdes.intel.com`.
Each article has a tenant (e.g. `dg_soc`, `ip_hw_graphics`, `server`) and a subject
(e.g. `feature`, `bugeco`) that together determine which branch of logic this tool uses.

**AR child / sw_impact child** — sub-records attached to an HSD article. For example,
a `dg_soc.feature` has Action Required (AR) children where each team logs their
planned work. The tool reads these children to find the KMD architecture record and,
if none already exists, creates a matching development record.

**Write-back** — after creating the Jira issues the tool fills in a field on the HSD
child record (e.g. `jira_key`, `sw_record`, or `tag`) with the Code Complete story
URL. Anyone looking at HSD can then click straight through to the Jira story.

**Exposure** — a field on the AR/sw_impact child (`high`, `medium`, `low`,
`to_be_assigned`, `none`) that indicates whether the feature needs implementation
work. The tool only creates Jira issues when exposure is `high`, `medium`, or `low`.

**Saved query** — a pre-built filter in HSDES that returns a list of article IDs.
The sidebar shows 9 default queries covering TGS, CRI, and JGS features across
SOC Strawman, SOC Official, and GT stacks. You can replace them with any query
your team uses.

**Tenant** — the HSD article type. This tool handles:
`dg_soc.feature`, `ip_hw_graphics.feature`, `ip_hw_graphics.bugeco`,
`server_platf.feature`, and `server.feature`.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | |
| Kerberos ticket | `kinit <ntlm-id>@AMR.CORP.INTEL.COM` |
| Jira Personal Access Token | Saved to `.jira_token` in this directory |
| Intel network access | HSD and Jira APIs are internal only |

```bash
pip install -r requirements.txt
kinit <your-ntlm-id>@AMR.CORP.INTEL.COM
echo "<your-jira-token>" > .jira_token
chmod 600 .jira_token
```

---

## Running

```bash
python3 app.py
# Open http://localhost:8889/hsd2jira
```

---

## Three flows

### Flow A — Bulk query processing (primary)

Paste a saved HSD query URL (or a single article URL). The tool fetches every
HSD from the query, applies per-tenant rules, creates the Jira issue hierarchy,
and writes the Code Complete story key back to the HSD child record.

A **Preview** mode runs the same checks with no writes — shows exactly which
HSDs would be processed or skipped and why.

### Flow B — Single HSD link

Paste one HSD article URL. The tool looks up the existing development child
record and creates Jira issues against it, then writes back the Jira key.

### Flow C — Manual create

Enter platform, title, component, and assignee directly — no HSD article
needed. Creates the full Jira hierarchy immediately.

---

## Tenant coverage

The tool handles five HSD tenant/subject combinations automatically. The correct
branch is selected based on the `tenant` and `subject` fields of each HSD.

### 1 — `dg_soc.feature`

**Gate:** AR child exists with `team=i915_kmd` and `task=architecture`, and
`exposure` is `high`, `medium`, or `low` (anything else → skip).

**Actions:**
- If an `i915_kmd / development` AR child already exists and has a `jira_key` → **skip** (already processed).
- If the development child exists but `jira_key` is empty → reuse it, update its exposure, create Jira, write back key.
- If no development child → create a new `dg_soc.ar` child (`team=i915_kmd`, `task=development`, same title and exposure as the architecture child), create Jira, write key to `dg_soc.ar.jira_key`.

#### TREND / phase sync logic (ongoing updates)

After a Jira issue exists, `dg_soc.feature` also participates in the weekly TREND sync
cron. For each `i915_kmd / development` AR child where `jira_key` is set **and**
`exposure` is not `none` / `to_be_assigned` / empty:

| Condition | HSD field updated | Value |
|---|---|---|
| Jira status is `Closed` | `dg_soc.ar.phase` | `Done` |
| Jira open and `Actual Trend WW` is set | `dg_soc.ar.phase` | Trend WW value (e.g. `26WW25`) |
| Jira open and no Trend WW | — | skip |

Note: The base `eta` calendar-date field is not writable via the service API; the
ten­ant-specific `dg_soc.ar.phase` field is used instead to surface the WW target.

**Queries used for dg_soc.feature TREND sync:**

| HSDES query ID | Project |
|---|---|
| `14024897996` | `CRI` |
| `14027898530` | `TGS` |

Defined in `_TREND_DG_SOC_QUERIES` in `routes/hsd2jira.py`.

### 2 — `ip_hw_graphics.feature`

**Gate:** `sw_impact` child exists with `sw_component=core`, `os=common`,
`sw_task=architecture`, and a valid (non-empty, non-`to_be_assigned`) `sw_exposure`.

**Actions:**
- If an `i915_kmd / linux / development` sw_impact child already exists and has `sw_record` set → **skip**.
- If it exists but `sw_record` is empty → reuse it, create Jira, write back CC story key to `sw_record`.
- If none exists → create a new `ip_hw_graphics.sw_impact` child (`sw_component=i915_kmd`, `os=linux`, `sw_task=development`), create Jira, write back key.

#### Query-based project mapping

For `ip_hw_graphics.feature` HSDs the platform/tag is derived directly from
which saved HSDES query returned the article — not from `family_affected` /
`release_affected` field detection. This avoids mis-classification when those
fields are incomplete.

| HSDES query ID | Platform | Tag |
|---|---|---|
| `14024748599` | `crescent_island` | `CRI` |
| `16026920807` | `Jaguar Shores` | `JGS` |
| `14026130398` | `tiger_shores` | `TGS` |

These are defined in `_QUERY_PROJECT_MAP` in `routes/hsd2jira.py`. Any article
that does not arrive via one of these three queries falls back to field-based
auto-detection.

#### TREND/DONE sync logic (ongoing updates)

After a Jira issue exists, this tenant also supports a **second update pass** that
keeps the HSD child record in sync with Jira progress. For each `i915_kmd / linux /
development` child that already has `sw_record` set:

| Condition | Action |
|---|---|
| `sw_exposure == 'none'` | Skip — feature not applicable to this component |
| `done == 'Yes'` | Skip — already finalised |
| Jira status is `Closed` | Set `ip_hw_graphics.sw_impact.done = 'Yes'` on the HSD child |
| Jira open and `Actual Trend WW` is set | Write that WW value to `ip_hw_graphics.sw_impact.trend` on the HSD child |
| Jira open and no Trend WW | Skip |

The Jira field used is `customfield_34504` (Actual Trend WW). Sub-decimal values
are stripped before write-back: `"26WW47.2"` → `"26WW47"`.

---

## Weekly TREND sync cron (all tenants)

A **separate background job** dedicated to syncing Jira progress back to HSD. It
is independent of the Jira-creation cron — the creation cron processes all tenants
on an interval, while this job runs **once every Friday** and covers four tenants:
`ip_hw_graphics.feature` (CRI / JGS / TGS), `server.feature` (JGS),
`dg_soc.feature` (CRI / TGS), and `server_platf.feature` (CRI).

All three tenants are processed in a **single parallel pass** using 8 worker threads.

**Enable/disable:** use the **📅 Weekly TREND Sync** card in the sidebar of the
web UI. The toggle and state persist across restarts in `hsd2jira_trend_cron.json`
in the project folder.

**Schedule:** the worker thread checks the current UTC weekday on wake-up. If
it is Friday (`weekday() == 4`) and the job has not already run today, it
executes the sync pass and records the timestamp. It then sleeps and checks again
after one hour. This means the job runs at most once per Friday regardless of
how long the process has been running.

**What is synced per tenant:**

| Tenant | Child record | Jira Closed → | Jira open + Trend WW → |
|---|---|---|---|
| `ip_hw_graphics.feature` | `i915_kmd / linux / development` | `sw_impact.done = 'Yes'` | `sw_impact.trend = <WW>` |
| `server.feature` | `SW Development Review` | `server.ar.reason = 'verified'` | `server.ar.new_projected_dates = <WW>` |
| `dg_soc.feature` | `i915_kmd / development` | `dg_soc.ar.phase = 'Done'` | `dg_soc.ar.phase = <WW>` |
| `server_platf.feature` | KMD AR child (title contains KMD) | `server_platf.ar.trend = 'Done'` | `server_platf.ar.trend = <WW>` |

**Skip conditions (all tenants):**
- No KMD child found, or `tag` / `jira_key` field is empty (no Jira linked)
- `exposure` / `sw_exposure` is `none`, `to_be_assigned`, or empty (where applicable)
- `ip_hw_graphics` only: `done` field already `Yes`
- `server_platf` only: child HSD `status` is not `sign_off`, or `tag` does not contain a VLK key

**Log file:** `hsd2jira_trend_cron_log.json` in the project folder. Keeps the
last 10 runs. Each entry contains:
- `run_time` — ISO timestamp of the run
- `summary` — `{updated, skipped, errors}` counts
- `details` — per-child-record list with `hsd_id`, `child_id`, `tag`,
  `sw_record` (Jira key), `action` (`update_trend` / `set_done` / `update_projected_date` / `set_verified` / `update_eta` / `set_phase_done` / `skipped` / `error`),
  `value` or `reason`, and `title`

**TREND sync API endpoints:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/hsd2jira/trend-cron/status` | Enabled state, thread alive, last run, last result summary |
| POST | `/hsd2jira/trend-cron/toggle` | Enable or disable; body `{"enabled": true/false}` |
| GET | `/hsd2jira/trend-cron/log` | Full per-record detail for the last 10 runs |
| GET | `/hsd2jira/trend-cron/download-log` | Download last run as a CSV file |

**View results:** click **📋 View log** in the sidebar card to open a per-record
table. Click **⬇ Download CSV** to save the latest run as a spreadsheet.

### 3 — `ip_hw_graphics.bugeco` (SW Workaround)

**Gate:** `sw_impact` child exists with `sw_component=i915_kmd` and
`func_impact=wa_needed`.

**Actions:**
- If the matched child already has `sw_record` set → **skip**.
- Otherwise: create Jira hierarchy, write CC story key to `sw_record` on the existing sw_impact child.

### 4 — `server_platf.feature`

**Gate:** `server_platf.ar` child exists with "KMD" in its title.

**Actions:**
- If the matched child already has a `tag` value set → **skip**.
- Otherwise: create Jira hierarchy, write CC story key to the `tag` field of the server_platf.ar child.

#### TREND sync logic (ongoing updates)

After a Jira issue exists, `server_platf.feature` also participates in the weekly TREND sync
cron. For each KMD AR child where `status = sign_off` **and** the `tag` field contains a VLK Jira key:

| Condition | HSD field updated | Value |
|---|---|---|
| Jira status is `Closed` | `server_platf.ar.trend` | `Done` |
| Jira open and `Actual Trend WW` is set | `server_platf.ar.trend` | Trend WW value (e.g. `26WW25`) |
| Jira open and no Trend WW | — | skip |

Note: Both open and closed states write to the same `server_platf.ar.trend` field.

**Queries used for server_platf.feature TREND sync:**

| HSDES query ID | Project |
|---|---|
| `13013902803` | `CRI` |

Defined in `_TREND_SERVER_PLATF_QUERIES` in `routes/hsd2jira.py`.



**Gate:** `server.ar` children exist where:
- `title = "SW Arch Review"`
- `component` is `sw.xe_kmd` or `sw.ual_kmd`
- `exposure` is not `none`, `to_be_assigned`, or empty

**Actions (per matching component):**
- If a corresponding `SW Development Review` child already has `jira_key` set → **skip** that component.
- If the SW Development Review exists, `jira_key` is empty, **but `exposure` is `none` / `to_be_assigned` / empty** → **skip** (no KMD action required for this component).
- If the SW Development Review exists, `jira_key` is empty, and exposure is valid → reuse it, create Jira, write back key.
- If no SW Development Review exists → create one (`server.ar`, same component and exposure), create Jira, write CC story key to `jira_key`.

Component-to-Jira mapping:

| HSD component | Jira component | Assignee |
|---|---|---|
| `sw.xe_kmd` | `XeKMD` | `guptasa2` |
| `sw.ual_kmd` | `Kernel - UAL` | `myossefi` |

#### TREND / projected-date sync logic (ongoing updates)

After a Jira issue exists, `server.feature` also participates in the weekly
TREND sync cron. For each `SW Development Review` child where `jira_key` is set
**and** `exposure` is not `none` / `to_be_assigned` / empty:

| Condition | HSD field updated | Value |
|---|---|---|
| Jira status is `Closed` | `server.ar.reason` | `verified` |
| Jira open and `Actual Trend WW` is set | `server.ar.new_projected_dates` | Trend WW value (sub-decimal stripped) |
| Jira open and no Trend WW | — | skip |

Multiple `SW Development Review` children per HSD are all processed independently.

**Queries used for server.feature TREND sync:**

| HSDES query ID | Project | `release_affected` |
|---|---|---|
| `14028056822` | `JGS` | `Xe4_XPC` / Jaguar Shores |

Defined in `_TREND_SERVER_QUERIES` in `routes/hsd2jira.py`.

---

## Platform and tag auto-detection

The tool automatically derives the Jira platform value and project tag from the
HSD `family_affected` and `release_affected` fields. Form-provided values are
used only as a fallback if auto-detection yields no match.

| Tenant | Condition | Platform | Tag |
|---|---|---|---|
| `dg_soc.feature` | `family_affected` contains "Alpine" | `tiger_shores` | `TGS` |
| `dg_soc.feature` | `family_affected` contains "Crescent Island" | `crescent_island` | `CRI` |
| `ip_hw_graphics.feature` | `family_affected` contains "xe5" | `tiger_shores` | `TGS` |
| `ip_hw_graphics.feature` | `family_affected` contains "xe3p" **and** `release_affected` contains "Xe3p_v1c_XPC" | `crescent_island` | `CRI` |
| `ip_hw_graphics.feature` | `family_affected` contains "xe4" **and** `release_affected` contains "Xe4_XPC" | `Jaguar Shores` | `JGS` |
| `server_platf.feature` | `family_affected` contains "Crescent Island" | `crescent_island` | `CRI` |
| `server.feature` | `family_affected` contains "Jaguar Shores" | `Jaguar Shores` | `JGS` |

---

## Jira hierarchy created (all flows)

Every successfully processed HSD results in the following 9 Jira issues in
project **VLK**:

```
Epic     [Epic] {title}
  Story  [Code Complete] {title}              ← key written back to HSD child
    Child Story  [Code Complete][Part-1] {title}
    Child Story  [Code Complete][Part-2] {title}
    Child Story  [Test][IGT] {title}
  Story  [Val][Sim] {title}
  Story  [Val][Emu] {title}
  Task   [Upstream] {title}
    Child Story  [Upstream] {title}
```

The title is prefixed with `[TAG]` when a project tag is present, e.g. `[TGS] My Feature`.

The `External Issue ID` field on the Code Complete and Sim/Emu stories is set to
the HSD parent URL and child URL so every Jira issue links back to HSD.

---

## Background cron job

The cron job runs all 9 saved sidebar queries automatically at a configurable
interval and processes any new HSDs that match the tenant rules above.

**Enable/disable:** use the ⏱ Auto-processing card in the sidebar of the web UI
(checkbox persists across restarts via `hsd2jira_cron.json` in the project folder).

**Behaviour in cron mode:**
- Platform/tag are auto-detected only — no form fallback.
- HSDs where platform cannot be auto-detected are silently skipped.
- Items already processed (jira_key / sw_record / tag already set) are skipped
  without creating duplicates.

**View results:** click **📋 View run details** in the sidebar to see a full
per-HSD breakdown of the last 5 runs (processed table with Jira links + skipped
table with skip reasons).

**Cron API endpoints:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/hsd2jira/cron/status` | Current state: enabled, interval, last run, last result |
| POST | `/hsd2jira/cron/toggle` | Enable or disable; body `{"enabled": true/false}` |
| POST | `/hsd2jira/cron/update-queries` | Replace query URL list; body `{"query_urls": [...]}` |
| POST | `/hsd2jira/cron/update-interval` | Change interval; body `{"interval_minutes": 15}` |
| GET | `/hsd2jira/cron/log` | Full per-HSD detail for the last 5 runs |

---

## Debug endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/hsd2jira/debug-ar/<hsd_id>` | Dump raw AR child data for a `dg_soc.feature` HSD |
| GET | `/hsd2jira/debug-feature/<hsd_id>` | Extended AR child info including exposure and type |
| GET | `/hsd2jira/debug-query/<query_id>` | Execute a saved HSD query and return the IDs found |
| GET | `/hsd2jira/debug-server-platf/<hsd_id>` | Dump server_platf.ar child data |

---

## Data sources

| Data | Source |
|---|---|
| HSD articles | HSD REST API — `https://hsdes.intel.com/rest/article` |
| AR / sw_impact children | HSD ESService — `https://hsdes.intel.com/ws/ESService` |
| Query results | ESService `execute_saved_query` with REST fallbacks |
| Authentication | Kerberos (`HTTPKerberosAuth`) for HSD; Bearer token for Jira |
| Jira issues | Jira REST API v2 — `https://jira.devtools.intel.com/rest/api/2` |
| Jira token | `.jira_token` file in project root (excluded from git) |

---

## Project structure

```
HSD2Jira2HSD/
├── app.py                  # Standalone Flask entry point (port 8889)
├── config.py               # Token path and BASE_DIR
├── requirements.txt
├── .jira_token             # Your Jira PAT — excluded from git, chmod 600
├── routes/
│   └── hsd2jira.py         # All backend logic and API routes
└── templates/
    └── hsd2jira.html       # Single-page UI
```

---

## Taking over this tool

If you are the new owner:

1. **Jira token** — generate your own Personal Access Token in Jira and save it to `.jira_token`. The token in this file is personal to the previous owner and will stop working when they leave.

2. **Default assignee** — `_ASSIGNEE = 'guptasa2'` in `routes/hsd2jira.py` is used for all XeKMD Jira issues. Update it to your NTLM ID.

3. **Sidebar queries** — the 9 default query URLs in `_CRON_DEFAULT_QUERIES` point to the current team's saved HSDES queries. If the queries are renamed or new platforms are added, update those constants and the saved list in the web UI.

4. **Cron state** — `hsd2jira_cron.json` (gitignored, created at runtime) stores whether the cron job is enabled and the current query list. Delete it to reset to defaults.

5. **Server deployment** — the tool is a plain Flask app; run it with `python3 app.py` inside a `tmux` or `screen` session, or wrap it in a systemd unit. There is no WSGI config included.

- **Epic**: Links added to description
- **Stories**: Links added to External Issue ID field

Format:
```
[parent] https://hsdes.intel.com/appstore/article-one/#/article/{parent_hsd_id}
[child] https://hsdes.intel.com/appstore/article-one/#/{sw_impact_id}
```

## Command Line Options

- `<HSD_ID>`: Required - HSD article ID
- `--title TEXT`: HSD title (uses HSD ID if not provided)
- `--effort FLOAT`: Override SW effort in weeks
- `--create`: Actually create JIRA issues (default: dry-run)
- `--debug`: Enable debug logging

## Authentication

- **HSDES**: Kerberos (HTTPKerberosAuth)
- **JIRA**: Personal Access Token in `.jira_token` file

## Output Example

```
✓ Found 1 matching sw_impact record(s)

Planning JIRA issues:
  SW_EFFORT: 5.0 weeks → 200.0 hours

Creating JIRA issues...
  ✓ Epic created: VLK-86719
  ✓ Story created: VLK-86720 (Code Complete)
  ✓ Story created: VLK-86721 (Simulation Verification)
  ✓ Story created: VLK-86722 (Emulation Verification)
  ✓ Task created: VLK-86723

View Epic: https://jira.devtools.intel.com/browse/VLK-86719
```

---

## Backlog Management

The `manage_backlog.py` tool allows you to automatically rank and organize JIRA backlogs for individuals, teams, or the entire project.

### Usage Examples

#### 1. Rank Your Own Backlog by Effort

```bash
# Preview what will happen (dry run)
python3 manage_backlog.py --scope user --order-by effort --dry-run

# Actually rank the backlog
python3 manage_backlog.py --scope user --order-by effort
```

#### 2. Rank Another User's Backlog

```bash
# Rank specific user's backlog
python3 manage_backlog.py --scope user --assignee jdoe --order-by effort
```

#### 3. Rank Entire Team's Backlog

```bash
# Use custom JQL for multiple users
python3 manage_backlog.py --scope custom \
  --jql "project = VLK AND assignee in (user1, user2, user3) AND resolution = Unresolved" \
  --order-by effort
```

#### 4. Rank by Component

```bash
# Rank all issues in a specific component
python3 manage_backlog.py --scope project \
  --component "Kernel - core" \
  --order-by effort
```

#### 5. Rank Entire Project

```bash
# Rank all VLK issues by effort
python3 manage_backlog.py --scope project --order-by effort
```

#### 6. Other Ranking Criteria

```bash
# Rank by priority
python3 manage_backlog.py --scope user --order-by priority

# Rank by creation date (oldest first)
python3 manage_backlog.py --scope user --order-by created --reverse

# Rank by last update
python3 manage_backlog.py --scope user --order-by updated
```

#### 7. Verify Current Order

```bash
# Just view current backlog order without making changes
python3 manage_backlog.py --scope user --verify-only
```

### Backlog Management Options

| Option | Description |
|--------|-------------|
| `--scope user` | Manage current user's backlog (or specific user with `--assignee`) |
| `--scope project` | Manage entire project backlog |
| `--scope custom` | Use custom JQL query with `--jql` |
| `--assignee USERNAME` | Specify user (for user scope) |
| `--project VLK` | JIRA project key (default: VLK) |
| `--component NAME` | Filter by component |
| `--order-by CRITERIA` | Sort by: `effort`, `priority`, `created`, `updated`, `key` |
| `--reverse` | Reverse sort order |
| `--dry-run` | Preview without making changes |
| `--verify-only` | Only show current order |
| `--max-results N` | Maximum issues to process (default: 100) |

### Team Workflow

For team leads managing backlog for their entire team:

```bash
# Create a team backlog script
cat > rank_team_backlog.sh << 'EOF'
#!/bin/bash
# Rank the entire team's backlog by effort

TEAM_MEMBERS="user1,user2,user3,user4,user5"

python3 manage_backlog.py \
  --scope custom \
  --jql "project = VLK AND assignee in ($TEAM_MEMBERS) AND resolution = Unresolved" \
  --order-by effort \
  --dry-run

read -p "Proceed with ranking? (yes/no): " response
if [ "$response" = "yes" ]; then
    python3 manage_backlog.py \
      --scope custom \
      --jql "project = VLK AND assignee in ($TEAM_MEMBERS) AND resolution = Unresolved" \
      --order-by effort
fi
EOF

chmod +x rank_team_backlog.sh
```

### Automated Backlog Management

You can set up a cron job to automatically maintain backlog order:

```bash
# Add to crontab (run every Monday at 9 AM)
0 9 * * 1 cd /home/vitasta/triage/repos/Projects/HSD2Jira2HSD && python3 manage_backlog.py --scope user --order-by effort --dry-run | mail -s "Weekly Backlog Review" your-email@intel.com
```

