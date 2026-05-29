# Routes — Tool Reference

Each file in this directory is a Flask Blueprint that handles one URL path.
All shared paths and constants live in `../config.py`.

---

## `index.py` — Landing Page
**URL:** `/`  
**Template:** `index.html`

Simple entry point. Renders the dashboard homepage with the current date.
No external data sources — pure static render.

---

## `jgs_soc_updates.py` — JGS SOC RTL Freeze Dashboard
**URL:** `/jgs-soc-updates`  
**Template:** `jgs_soc_updates.html`

### What it shows
- Cumulative trend graph: Code Complete / Coral Tested / Palladium Tested by Work Week
- Per-category long-pole table (latest WW target per category)
- Pending tasks: open tasks whose target WW has already passed
- Blocker tasks with linked Jira keys
- Platform stats summary (done vs target counts per category)
- Category-level drill-down graphs
- UAL schedule table (separate Excel tab) with editable cells

### Where data comes from
| Data | Source |
|------|--------|
| Task list (category, owner, trend WW, status, blockers) | `JGS_SOC_Trend.xlsx` → sheet `RTLFreeze` |
| UAL schedule | Same Excel → sheet `UAL_TI_SCHEDULE` |
| Green-cell completion detection | Cell fill colour (openpyxl theme 9, tint ≈ 0) |
| Auto-download | Microsoft Graph API — SharePoint file downloaded to `soc_automation/` |
| Task comments | SQLite `soc_edits.db` → `task_comments` table |
| UAL cell edits | SQLite `soc_edits.db` → `ual_edits` table |
| Jira "is blocked by" links | Jira REST API (VLK keys only, PSOC/Boot categories) |

### Caching
Excel mtime polled every 30 seconds by the frontend — page reloads when the file changes.
In-memory cache invalidated by `invalidate_cache()` or `POST /api/auto-refresh`.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jgs-soc-updates` | Main dashboard |
| POST | `/jgs-soc-updates/save-task-comment` | Persist task comment to SQLite |
| POST | `/jgs-soc-updates/save-ual-cell` | Persist UAL cell edit to SQLite |

---

## `jgs_upstreaming.py` — JGS Upstreaming Plan
**URL:** `/jgs-upstreaming`  
**Template:** `jgs_upstreaming_plan.html`

### What it shows
- Full issue table: key, title, component, assignee, target WW, status
- Cumulative WW trend graph
- Pending issues: open issues whose target WW ≤ current WW
- Per-issue editable comments

### Where data comes from
| Data | Source |
|------|--------|
| Issues | Jira REST API — filter `452639` |
| Target WW | Jira custom field `customfield_34504` |
| Token | `config.JIRA_TOKEN_PATH` |
| Comments | `../user_data.json` → key `jgs_up_comments` |

### Caching
Jira response cached in-memory for 5 minutes. Force refresh via `POST /jgs-upstreaming/refresh`.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jgs-upstreaming` | Main dashboard |
| POST | `/jgs-upstreaming/save-comment` | Save per-issue comment to `user_data.json` |
| POST | `/jgs-upstreaming/refresh` | Clear Jira cache and force re-fetch |

---

## `jgs_bug_triage.py` — JGS Bug Triage Dashboard
**URL:** `/jgs-bug-triage`  
**Template:** `jgs_bug_triage.html`

### What it shows
- JGS hardware/emulation bugs grouped by component (XeSim, KMD, IGC, PISA, Test, Compute Dev)
- Priority badges (P1–P5), days-open counter, last-comment date
- Component ownership history trail
- Flags bugs open > 14 days without a root cause
- "Notify Arch" button — sends an escalation email to the SW Architecture DL

### Where data comes from
| Data | Source |
|------|--------|
| Bug list | HSD query `14026739770` (tenant `ip_hw_graphics`, subject `bugeco`) |
| Enriched fields (tags, root cause, closed_date) | HSD ESService `get_record_by_id` per article |
| Component history | HSD REST `/rest/article/<id>/history` |
| Auth | Kerberos (`requests_kerberos.HTTPKerberosAuth`) |

### Component mapping
Raw `component_affected` field is matched against keyword rules (first match wins):
`fulsim` → XeSim, `linux_kmd` → KMD, `igc_compute` → IGC, `pisa_finalizer` → PISA,
`compute_test` / `test.driver` → Test, `compute_umd` → Compute Dev, `val_only` → Val Team.

### Arch escalation email
`POST /jgs-bug-triage/send-arch-email` sends an HTML email via `smtpmail.intel.com`
to the address in `SW_ARCH_EMAIL` (set in the route file). The dashboard link in
the email uses `config.APP_BASE_URL`.

### Caching
Bug list cached in-memory for 5 minutes.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jgs-bug-triage` | Main dashboard |
| POST | `/jgs-bug-triage/refresh` | Clear cache and force re-fetch |
| GET | `/jgs-bug-triage/debug` | JSON dump of first raw article (debugging) |
| POST | `/jgs-bug-triage/send-arch-email` | Send escalation email for one bug |

---

## `jgs_emu_bugs.py` — JGS Emulation Bugs Dashboard
**URL:** `/jgs-emu-bugs`  
**Template:** `jgs_emu_bugs.html`

### What it shows
- Emulation-specific JGS bugs from a dedicated HSD query
- Component ownership trail (how ownership moved between teams over time)
- Priority grouping and days-open badges
- Per-bug editable comments with **Teams @mention notifications**

### Where data comes from
| Data | Source |
|------|--------|
| Bug list | HSD query `14027480453` |
| Enriched fields | HSD REST `/rest/article/<id>` per article |
| Component history | HSD REST `/rest/article/<id>/history` |
| Auth | Kerberos |

### Teams @mention flow
When a user types `@username` in a Comments cell and saves:
1. Frontend calls `POST /jgs-emu-bugs/notify-mention` with `{bug_id, bug_title, note, mentions: [...]}`
2. Server POSTs once per mentioned user to the Power Automate HTTP-trigger URL stored in `routes/webhook_config.json`
3. Power Automate sends a native Teams 1-on-1 DM

The Power Automate flow is set up once by the user at flow.microsoft.com.
The webhook URL is saved via `POST /jgs-emu-bugs/teams-webhook`.

### Caching
Bug list cached in-memory for 5 minutes.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jgs-emu-bugs` | Main dashboard |
| POST | `/jgs-emu-bugs/refresh` | Clear cache and force re-fetch |
| GET | `/jgs-emu-bugs/debug` | JSON dump of first raw article |
| GET | `/jgs-emu-bugs/teams-webhook` | Return current webhook URL (masked) |
| POST | `/jgs-emu-bugs/teams-webhook` | Save or clear the Power Automate URL |
| POST | `/jgs-emu-bugs/notify-mention` | Send Teams DM to each @mentioned user |
| GET | `/jgs-emu-bugs/lookup/<article_id>` | Fetch single article for debugging |

---

## `jgs_4plus2.py` — JGS 4+2 Tracking Dashboard
**URL:** `/jgs-4plus2`  
**Template:** `jgs_4plus2.html`

### What it shows
- HSD + Jira tracking table from Rodrigo Vivi's personal SharePoint Excel (4+2 spreadsheet)
- Rows grouped by owner/column
- Editable ETA and Comments for each HSD row (persisted in SQLite, shared across users)
- 60-second polling so edits by any user appear on all browsers without a reload

### Where data comes from
| Data | Source |
|------|--------|
| HSD/Jira rows | SharePoint Excel (Rodrigo Vivi's OneDrive) — downloaded via Microsoft Graph API |
| HSD titles | HSD REST API (Kerberos) |
| ETA + Comments | SQLite → `../JGS_4+2/jgs4plus2.db` (WAL mode) |

### Owner → column mapping
| Excel column | Owner |
|-------------|-------|
| xeKMD | Rodrigo / Vivi |
| UALkmd | Erez |
| UMD-L0 | Mrozek |
| Sysman | Santosh |

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jgs-4plus2` | Main dashboard |
| POST | `/jgs-4plus2/save` | Save ETA or Comments for one HSD row |
| POST | `/jgs-4plus2/refresh-excel` | Force re-download of the SharePoint Excel |
| GET | `/jgs-4plus2/api/data` | JSON of all current edits (polling endpoint) |

---

## `cri_e2e.py` — CRI E2E Plan
**URL:** `/cri-e2e`  
**Template:** `e2e_plan.html`

### What it shows
- CRI HSD issue table: HSD ID, title, section (sw_only / hw+sw / gt_dcn), linked Jira chips, BMG support flag, editable comment
- XPUM/Sysman feature table: feature name, Jira chips, status, dependencies, ETAs, remarks

### Where data comes from
| Data | Source |
|------|--------|
| HSD rows (rows 1–46) | `config.CRI_EXCEL` (`CRI Pre-Map Day.xlsx`) → sheet `weekly_updates` |
| GT DCN rows | Same Excel → sheet `GT_DCN` |
| KMD Jira cross-reference | Same Excel → sheet `KMD`, cols H–L |
| XPUM/Sysman features | `config.CRI_PPTX` (`CRI_XPUM_E2E_FEATURE_REVIEW.pptx`) — slides 1–5 (primary) |
| XPUM fallback | Excel sheet `xpum` (if PPTX unavailable) |
| PPTX download | Microsoft Graph API via `utils/cri_helpers.refresh_pptx()` |
| Comments | `config.E2E_COMMENTS` (`e2e_comments.json`) |

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/cri-e2e` | Main dashboard |
| POST | `/cri-e2e/save` | Save BMG flag or comment to `e2e_comments.json` |

---

## `cri_ccb.py` — CRI CCB Strawman Dashboard
**URL:** `/cri-ccb`  
**Template:** `cri_ccb.html`

### What it shows
- CRI strawman HSD features with per-component AR sign-off status
- Components tracked: XeKMD, Sysman, XPUM, E2E
- Each row shows whether each component's AR child is Signed Off or Pending
- Last-refresh timestamp + stale indicator (> 30 min old)

### Where data comes from
| Data | Source |
|------|--------|
| Strawman HSDs | HSD query `13013902803` (tenant `server_platf`, subject `feature`) |
| AR children | HSD ESService `get_related_records` per HSD |
| AR sign-off status | HSD REST `/rest/article/<ar_id>` per AR child |
| Auth | Kerberos |
| Cache | SQLite `cri_ccb.db` → tables `ccb_rows`, `ccb_meta` |

### Component detection
AR title is matched against keyword lists: `xekmd`/`xe-kmd`/`kmd` → XeKMD,
`sysman` → Sysman, `xpum`/`xpu manager` → XPUM, `e2e` → E2E.
If an AR status is `sign_off` / `signoff` / `signed_off` it shows "Signed off", otherwise "Pending".

### Caching
Results cached in SQLite with a 30-minute TTL. A stale indicator is shown when data is older than 30 min.
Force refresh via `POST /cri-ccb/refresh` (live re-fetch, ~30–60s).

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/cri-ccb` | Main dashboard (serves from SQLite cache) |
| POST | `/cri-ccb/refresh` | Force live re-fetch from HSD and update cache |

---

## `cri_daily_tf.py` — CRI Daily TF Pending Features
**URL:** `/cri-daily-tf`  
**Template:** `cri_daily_tf.html`

### What it shows
- Two tables: XPUM/Sysman and XeKMD pending driver features
- Columns: Feature, Priority, TF Meeting Minutes (latest WW update), ETA, Blocker
- All columns are editable inline — edits persisted server-side in SQLite
- 30-second polling so edits by any user appear on all browsers without a reload
- Latest WW update is auto-extracted from a multi-line "TF Meeting Minutes" cell
- Export to PowerPoint (`GET /cri-daily-tf/export-slides`)

### Where data comes from
| Data | Source |
|------|--------|
| Feature rows | SharePoint Excel — Pending Driver Features (`Xpum_Sysman` and `XeKMD_pending` tabs) |
| Excel download | Microsoft Graph API — sharing URL in `_SHARING_URL` constant |
| Excel cache | `/tmp/cri_daily_tf_cache.xlsx` (1-minute burst cache) |
| Edits | SQLite `cri_daily_tf.db` → `tf_edits` table |
| Graph token | `soc_automation/refresh_graph_token.py` |

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/cri-daily-tf` | Main dashboard |
| POST | `/cri-daily-tf/save` | Save one field edit to SQLite |
| GET | `/cri-daily-tf/api/data` | JSON of all current edits (polling) |
| POST | `/cri-daily-tf/reset-field` | Clear one field override so Excel value shows through |
| POST | `/cri-daily-tf/force-refresh` | Delete Excel cache and re-download from SharePoint |
| GET | `/cri-daily-tf/export-slides` | Download a PowerPoint with both tables |

---

## `backlog_orchestrator.py` — Backlog Orchestrator
**URL:** `/backlog-orchestrator`  
**Template:** `backlog.html`

### What it shows
- Jira backlog table: key, summary, assignee, status, effort, priority bucket (P1–P4)
- Sortable by effort, priority, created date, updated date, or key
- Rule-based auto-bucketing: text rules classify issues into P1–P4
- Optional re-rank: pushes new ordering back to Jira's agile board

### Where data comes from
| Data | Source |
|------|--------|
| Issues | Jira REST API — JQL built from scope (My Backlog / Entire Project / Board / Custom JQL) |
| Default project | VLK |
| Token | `config.BACKLOG_TOKEN_PATH` (`../HSD2Jira2HSD/.jira_token`) |
| Rules | `POST /api/orchestrate` body — plain-text rules evaluated per issue |

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/backlog-orchestrator` | Main page |
| POST | `/api/backlog` | Fetch and sort issues; body: `{scope, orderBy, jql}` |
| POST | `/api/rank` | Re-rank issues in Jira agile board; body: `{issueKeys: [...]}` |
| POST | `/api/rank-debug` | Dry-run rank — shows what order would be applied |
| POST | `/api/orchestrate` | Fetch issues + apply rules → bucketed results |

---

## `api.py` — Shared API Endpoints
**URL:** `/api/*`, `/static/rtl_freeze_graph.png`  
No dedicated template.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/static/rtl_freeze_graph.png` | Serves the generated RTL freeze PNG from `soc_automation/` |
| GET | `/api/user-data` | Read `user_data.json` |
| POST | `/api/user-data` | Write to `user_data.json` |
| GET | `/api/jira-tracking-notes` | Read `jira_tracking_notes.json` |
| POST | `/api/jira-tracking-notes` | Write (merged) to `jira_tracking_notes.json` |
| GET | `/api/jira-tracking-rules` | Read `jira_tracking_rules.json` (no-cache) |
| POST | `/api/jira-tracking-rules` | Write to `jira_tracking_rules.json` |
| GET | `/api/excel-mtime` | Return `JGS_SOC_Trend.xlsx` mtime + latest comment timestamp (frontend polls every 30s) |
| POST | `/api/auto-refresh` | Spawn background thread: re-download Excel + run JIRA checker + regenerate graph |

---

## `hsd2jira.py` — HSD→Jira Loader Stub
**URL:** `/hsd2jira`, `/hsd2jira-flow`, `/jira-tracking-design` (and all sub-routes)  
**Template:** loaded from `../HSD2Jira2HSD/`

This file is a **loader stub only** — it dynamically imports the blueprint from
the sibling `HSD2Jira2HSD` repo using `importlib`. Edit code there, not here.

```
Projects/
├── data_centre_landing/routes/hsd2jira.py   ← this file (stub only)
└── HSD2Jira2HSD/routes/hsd2jira.py          ← actual implementation
```

### What HSD2Jira2HSD does

Automates converting Intel HSD feature articles into a structured Jira issue hierarchy in the VLK project.

#### Flow A — Bulk HSD Query (`/hsd2jira/process-hsd-query`)
Processes every `dg_soc.feature` HSD in a saved HSD query:
1. Fetch HSD article and confirm it is `dg_soc.feature`
2. Find AR child with `team=core`, check `exposure` (skip if `to_be_assigned`)
3. Check for existing `team=i915_kmd / task=development` AR child — skip if already exists
4. Create new `dg_soc.ar` child HSD with `team=i915_kmd, task=development`
5. Create 9 Jira issues in VLK
6. Write Code Complete story key back to `dg_soc.ar.jira_key`

#### Flow B — From HSD Link (`/hsd2jira/create-from-hsd-link`)
Single-HSD flow. Finds the existing `team=i915_kmd` AR child and creates 9 Jira issues against it.

#### Flow C — Manual New Feature (`/hsd2jira/create`)
Enter platform, title, component, assignee directly — creates the same 9-issue hierarchy without an HSD link.

#### Jira hierarchy created (all flows)
```
Epic
└─ Story  [Parent][Code Complete]
     ├─ Child Story  [Code Complete][Part-1]
     ├─ Child Story  [Code Complete][Part-2]
     └─ Child Story  [Test][IGT]
└─ Story  [Parent][Val][Sim]
└─ Story  [Parent][Val][Emu]
└─ Task   [Parent][Upstream]
     └─ Child Story  [Upstream]
```
Total: **9 issues** (1 Epic + 3 Parent Stories + 3 Child Stories + 1 Task + 1 Child Story)

#### Cron
A background cron thread can be enabled to run the bulk HSD query on a schedule.
State is persisted in `hsd2jira_cron.json`. Controlled via the `/hsd2jira` UI.


---

## `index.py` — Landing Page
**URL:** `/`  
**Template:** `index.html`

Simple entry point. Renders the dashboard homepage with the current date.  
No external data sources — pure static render.

---

## `jgs_soc_updates.py` — JGS SOC RTL Freeze Dashboard
**URL:** `/jgs-soc-updates`  
**Template:** `jgs_soc_updates.html`

### What it shows
- Cumulative trend graph: Code Complete / Coral Tested / Palladium Tested by Work Week
- Per-category long-pole table (latest WW target per category)
- Pending tasks table: tasks whose target WW has passed but aren't marked done
- Blocker tasks with linked JIRA keys
- Platform stats summary (done vs target counts per category)
- Category-level drill-down graphs

### Where data comes from
| Data | Source |
|------|--------|
| Task list (category, owner, trend WW, status, blockers) | `../soc_automation/JGS_SOC_Trend.xlsx` → sheet `RTLFreeze`, cols A–O |
| Green-cell detection for completion | Cell fill colour (theme 9, tint ≈ 0) read via openpyxl |
| Manual completion overrides | `../soc_automation/task_tracking_database.json` |

### Caching
Excel is cached in-memory for **5 minutes** (TTL from `config.CACHE_TTL`).  
Cache is invalidated via `POST /api/auto-refresh` or calling `invalidate_cache()`.

---

## `jgs_upstreaming.py` — JGS Upstreaming Plan
**URL:** `/jgs-upstreaming`  
**Template:** `jgs_upstreaming_plan.html`

### What it shows
- Full issue table: key, title, component, assignee, target WW, status
- Cumulative WW trend graph (issues by target work week)
- Milestone vertical lines (IP Freeze → PV, up to 28WW06)
- Pending issues: open issues whose target WW ≤ current WW
- Per-issue editable comments (saved to `user_data.json`)

### Where data comes from
| Data | Source |
|------|--------|
| Issues | Jira REST API — filter `452639` |
| Target WW | Jira custom field `customfield_34504` |
| Token | `/home/vitasta/triage/repos/Projects/soc_automation/.jira_token` |
| Comments | `../user_data.json` → key `jgs_up_comments` |

### Caching
Jira response cached in-memory for **5 minutes**.  
Force refresh via `POST /jgs-upstreaming/refresh`.

### Additional endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/jgs-upstreaming/save-comment` | Save per-issue comment to `user_data.json` |
| POST | `/jgs-upstreaming/refresh` | Clear Jira cache and force re-fetch |

---

## `kmd_post_freeze.py` — JGS Post-Freeze Feature Plan (KMD Core)
**URL:** `/kmd-post-freeze`  
**Template:** `kmd_post_freeze.html`

### What it shows
- Issue table grouped by component (GuC / Kernel Core / Platform Team) and subcategory
- Cumulative WW trend graph with component-level breakdown
- Closed-bar overlay (issues closed this WW vs target)
- Component and subcategory summary tables
- Pending issues (open, target WW ≤ current WW)
- Per-issue editable comments

### Where data comes from
| Data | Source |
|------|--------|
| Issues | Jira REST API — filter `452621` |
| Target WW | Jira custom field `customfield_34504` |
| Token | `/home/vitasta/triage/repos/Projects/soc_automation/.jira_token` |
| Comments | `../user_data.json` → key `kmd_freeze_comments` |
| Closure log | `../user_data.json` → key `kmd_closure_log` (tracks which WW each issue closed) |

### Component mapping
Raw Jira components are normalised:
- `kernel - core`, `base xekmd`, `xekmd`, `test-core` → **Kernel Core**
- `kernel - telemetry`, `kernel - power/freq`, `kernel - cp`, `kernel - sriov`, `platform enabling`, `kernel - pmt` → **Platform Team**
- `guc` → **GuC**

### Caching
Jira response cached in-memory for **5 minutes**.  
Force refresh via `POST /kmd-post-freeze/refresh`.

### Additional endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/kmd-post-freeze/save-comment` | Save per-issue comment |
| POST | `/kmd-post-freeze/refresh` | Clear Jira cache |

---

## `cri_e2e.py` — CRI E2E Plan
**URL:** `/cri-e2e`  
**Template:** `e2e_plan.html`

### What it shows
- CRI HSD issue table: HSD ID, title, section (sw_only / hw+sw / gt_dcn), linked JIRAs, BMG support flag, editable comment
- XPUM/Sysman feature table: feature name, JIRA chips, status, dependencies, ETAs, remarks

### Where data comes from
| Data | Source |
|------|--------|
| HSD rows (rows 1–46) | `/home/vitasta/CRI_Weekly_review/CRI Pre-Map Day.xlsx` → sheet `weekly_updates` |
| GT DCN rows | Same Excel → sheet `GT_DCN` |
| KMD JIRA cross-reference | Same Excel → sheet `KMD`, cols H–L |
| XPUM/Sysman features | `/home/vitasta/CRI_Weekly_review/CRI_XPUM_E2E_FEATURE_REVIEW.pptx` — slides 1–5 (primary) |
| XPUM fallback | Excel sheet `xpum` (if PPTX unavailable) |
| HSD links | `https://hsdes.intel.com/appstore/article-one/#/article/<hsd_id>` |
| PPTX download | Microsoft Graph API via `utils/cri_helpers.refresh_pptx()` (SharePoint) |
| Comments | `/home/vitasta/CRI_Weekly_review/e2e_comments.json` |

### Additional endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/cri-e2e/save` | Save BMG flag or comment to `e2e_comments.json` |

---

## `cri_kmd_e2e.py` — CRI KMD/UMD E2E Feature Plan
**URL:** `/cri-kmd-e2e`  
**Template:** `kmd_e2e_plan.html`

### What it shows
- Feature tables per section: RAS, Telemetry, AMC Related, Firmware, XPUM Only, Opens
- Per-row: feature name, JIRA chips, status, UMD/KMD dependencies, blocker ETA, overall ETA, remarks, editable summary
- Cumulative schedule graph by section (features planned by WW)
- Closed-bar overlay per section
- Long-pole WW per section and overall

### Where data comes from
| Data | Source |
|------|--------|
| Feature rows | `/home/vitasta/CRI_Weekly_review/CRI_XPUM_E2E_FEATURE_REVIEW.pptx` — slides 1–6 (merged-cell aware) |
| PPTX download | Microsoft Graph API via `utils/cri_helpers.refresh_pptx()` |
| Status/ETA overrides | `/home/vitasta/CRI_Weekly_review/kmd_e2e_comments.json` |
| Closure log | `../user_data.json` → key `kmd_e2e_closure_log` |

### Slide → section mapping
| Slide | Section |
|-------|---------|
| 1 | RAS |
| 2 | Telemetry |
| 3 | AMC Related |
| 4 | Firmware |
| 5 | XPUM Only |
| 6 | Opens |

### Additional endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/cri-kmd-e2e/save` | Save any editable field to `kmd_e2e_comments.json` |

---

## `backlog_orchestrator.py` — Backlog Orchestrator
**URL:** `/backlog-orchestrator`  
**Template:** `backlog.html`

### What it shows
- JIRA backlog table: key, summary, assignee, status, effort, priority
- Sortable by effort, priority, created date, updated date, or key
- Total effort summary
- Optional re-rank: pushes new ordering back to Jira's agile board

### Where data comes from
| Data | Source |
|------|--------|
| Issues | Jira REST API — JQL built from scope (My Backlog / Entire Project / Custom JQL) |
| Default project | VLK |
| Token | `../HSD2Jira2HSD/.jira_token` |

### Additional endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/backlog` | Fetch and sort issues; body: `{scope, orderBy, jql}` |
| POST | `/api/rank` | Re-rank issues in Jira agile board; body: `{issueKeys: [...]}` |

---

## `api.py` — Internal API / Graph Serving
**URL:** `/api/*`, `/static/rtl_freeze_graph.png`  
No dedicated template.

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/static/rtl_freeze_graph.png` | Serves the generated RTL freeze PNG graph |
| GET/POST | `/api/user-data` | Read or write arbitrary key-value pairs to `../user_data.json` |
| POST | `/api/auto-refresh` | Spawns a background thread to invalidate the SOC Excel cache then re-warm it |

---

## `hsd2jira.py` — HSD → Jira Modernisation
**URL:** `/hsd2jira`  
**Template:** `hsd2jira.html`

Automates the end-to-end flow of converting Intel HSD feature articles into a
structured Jira issue hierarchy in the VLK project. Three entry points are
provided on the same page.

---

### Flow A — Process HSD Query (`/hsd2jira/process-hsd-query`)

Bulk-processes every `dg_soc.feature` HSD returned by a saved HSD query URL
(or a single article URL).

**Steps per HSD:**
1. Fetch the HSD article and confirm it is a `dg_soc.feature`.
2. Fetch all AR children; find the one with `team=core`.
3. If the core AR child's `exposure` is `to_be_assigned` (or empty) → **skip**.
4. Check whether a `dg_soc.ar` child with `team=i915_kmd` and
   `task=development` **already exists** → if yes, **skip** to avoid
   duplicates (bug-fix: 2026-05-20).
5. Create a new `dg_soc.ar` child HSD under the feature with:
   - `team = i915_kmd`, `task = development`
   - Same `title`, `type`, and `exposure` as the core AR child
   - `owner = vwattal`
6. Create **9 Jira issues** in VLK (see _Jira hierarchy_ below).
7. Write the Code Complete parent story key back to `dg_soc.ar.jira_key` of
   the new HSD child.

**Platform auto-detection:** if the feature's `release` field contains
`"alpine"` the platform is set to `tiger_shores` automatically.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/hsd2jira/process-hsd-query` | Run the full bulk flow |
| POST | `/hsd2jira/preview-hsd-query` | Dry-run preview — shows what would be processed/skipped, no writes |
| GET  | `/hsd2jira/debug-query/<query_id>` | Execute a saved HSD query and return the raw IDs found |
| GET  | `/hsd2jira/debug-feature/<hsd_id>` | Dump extended AR child info for a single feature HSD |
| GET  | `/hsd2jira/debug-create-ar/<parent_id>` | Try multiple REST/ESService body formats for child creation and report results |

---

### Flow B — Create from HSD Link (`/hsd2jira/create-from-hsd-link`)

Single-HSD flow. Paste an HSD article URL; the system finds the existing
`team=i915_kmd / task=development` AR child and creates Jira issues against it.

**Steps:**
1. Extract HSD ID from the supplied URL.
2. Fetch the HSD article title.
3. Find the `dg_soc.ar` child with `team=i915_kmd` and `task=development`.
4. Create 9 Jira issues in VLK.
5. Write the Code Complete story key back to `dg_soc.ar.jira_key` of that AR
   child.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/hsd2jira/create-from-hsd-link` | Full creation flow |
| POST | `/hsd2jira/preview-hsd` | Fetch HSD title + matched AR child, no writes |
| GET  | `/hsd2jira/debug-ar/<hsd_id>` | Dump raw ESService AR child data for debugging |

---

### Flow C — New Feature (manual, `/hsd2jira/create`)

Enter a platform, feature title, component, and assignee directly without an
HSD link. Creates the same 9-issue Jira hierarchy.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/hsd2jira/create` | Create 9 Jira issues from manually supplied fields |

---

### Jira hierarchy created (all three flows)

```
Epic  [Epic] {title}
  └─ Story  [Parent][Story] [Code Complete] {title}          ← jira_key written to HSD
       ├─ Child Story  [Child][Story][Code Complete][Part-1] {title}
       ├─ Child Story  [Child][Story][Code Complete][Part-2] {title}
       └─ Child Story  [Child][Story][Test][IGT] {title}
  └─ Story  [Parent][Story][Val][Sim] {title}
  └─ Story  [Parent][Story][Val][Emu] {title}
  └─ Task   [Parent][Task][Upstream] {title}
       └─ Child Story  [Child][Story][Upstream] {title}
```

Total: **1 Epic + 3 Parent Stories + 3 Child Stories + 1 Task + 1 Child Story = 9 issues**

The External Issue ID field (`customfield_10808`) on the Code Complete and
Sim/Emu stories is set to:
```
[parent] https://hsdes.intel.com/appstore/article-one/#/{hsd_id}
[child]  https://hsdes.intel.com/appstore/article-one/#/{child_hsd_id}
```

---

### HSD / ESService helpers

| Function | Purpose |
|----------|---------|
| `_new_hsd_session()` | Kerberos-authenticated `requests.Session` for all HSD calls |
| `_esservice_request(session, command, args)` | POST to HSD ESService API; returns `result_table` on success or `None` |
| `_fetch_hsd_title(session, hsd_id)` | GET `/rest/article/{id}` → title string |
| `_fetch_hsd_info(session, hsd_id)` | GET `/rest/article/{id}` → `{title, tenant, subject, release}` |
| `_get_ar_children(session, hsd_id)` | ESService `get_related_records` → fetch full records for each `dg_soc.ar` link |
| `_parse_ar_record(rec)` | Extract `id, team, task, jira_key, status` from a raw AR record |
| `_parse_ar_record_extended(rec)` | Like above plus `title, exposure, type_` |
| `_create_hsd_ar_child(session, parent_id, ...)` | POST `/rest/article` with `fieldValues` list to create a new `dg_soc.ar` child |
| `_writeback_jira_key(session, child_id, jira_key)` | Update `dg_soc.ar.jira_key`; tries ESService `update_record` first, falls back to REST PUT |
| `_execute_hsd_query(session, query_id)` | ESService `execute_saved_query` with two REST fallbacks; returns list of HSD IDs |
| `_is_feature_hsd(info)` | Returns `True` if `tenant=dg_soc` and `subject=feature` |
| `_platform_from_release(release)` | Maps `"alpine…"` release string → `"tiger_shores"` |
| `_build_jira_issues(post_issue, create_link, ...)` | Creates the full 9-issue hierarchy; returns `{created, errors, epic_key, cc_story_key}` |

---

### Where data comes from

| Data | Source |
|------|--------|
| HSD articles & AR children | HSD REST API (`https://hsdes.intel.com/rest/article`) + ESService (`/ws/ESService`) via Kerberos auth |
| HSD query results | ESService `execute_saved_query` / HSD REST `/rest/query/{id}` / `/rest/article?query_id=` |
| Jira issue creation | Jira REST API v2 (`https://jira.devtools.intel.com/rest/api/2/issue`) |
| Jira token | `HSD2JIRA_TOKEN_PATH` (defined in `../config.py`) |
| Default component | `Kernel - core` |
| Default assignee | `vwattal` |

---

### Known bug fixed (2026-05-20)

**Problem:** `process-hsd-query` fetched AR children to find `team=core` but never
checked whether a `team=i915_kmd / task=development` AR child already existed.
Running the query a second time would call `_create_hsd_ar_child` again and
produce a duplicate child in HSD.

**Fix:** Added a guard block immediately after the core-exposure check:
```python
kmd_list = [c for c in ar_children
            if c['team'].lower() == 'i915_kmd'
            and c['task'].lower() == 'development']
if kmd_list:
    result['skipped'] = True
    result['skip_reason'] = (f'i915_kmd/development AR child already exists '
                             f'({kmd_list[0]["id"]}) — skipping to avoid duplicate')
    ...
    continue
```
The entry is now marked **SKIPPED** with a clear reason when re-processed.

---

## Shared infrastructure

| File | Purpose |
|------|---------|
| `../config.py` | All file paths, URLs, token paths, `CACHE_TTL=300` |
| `../utils/helpers.py` | `parse_ww()`, `is_cell_green()`, `extract_jira_keys()`, `current_ww_info()` |
| `../utils/cri_helpers.py` | `refresh_pptx()` (SharePoint download), `extract_jiras()`, `chips()`, `chip_class()` |
| `../utils/security.py` | Rate limiting (500 req/min, 5-min block) + security headers |
| `../user_data.json` | Persistent store for comments, closure logs, user preferences |
