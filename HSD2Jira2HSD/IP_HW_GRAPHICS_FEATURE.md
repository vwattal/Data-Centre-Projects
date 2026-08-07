# ip_hw_graphics.feature — How the Tool Works

This document describes exactly what the HSD2Jira tool does when it encounters an
HSD article of tenant `ip_hw_graphics` and subject `feature`.  
Written in plain language so the next maintainer can understand, modify, and debug
the flow without reading all the code first.

---

## Background — What is ip_hw_graphics.feature?

These are **hardware feature HSDs** raised by the GT (Graphics Technology) IP team.
They describe a new GPU hardware capability that the driver team needs to implement.

Each such HSD lives in the `ip_hw_graphics` tenant in HSDES and has the subject
`feature`.  The driver team's job is to:
1. Be assigned an sw_impact child record on the HSD so ownership is clear in HSDES.
2. Have a linked set of Jira stories in VLK so day-to-day work can be tracked there.

The tool automates both of those steps.

---

## HSD Children — What the tool looks for

Every `ip_hw_graphics.feature` HSD can have child records of type
`ip_hw_graphics.sw_impact`.  Each child record has these key fields:

| Field | Meaning |
|---|---|
| `sw_component` | Which SW team owns this child (e.g. `core`, `i915_kmd`, `compute`) |
| `sw_task` | What kind of work (e.g. `architecture`, `development`) |
| `os` | Operating system (e.g. `common`, `linux`) |
| `sw_exposure` | How much driver work is needed (`high`, `medium`, `low`, `none`, `to_be_assigned`) |
| `sw_record` | Field where a Jira story key is written back (e.g. `VLK-12345`) |

---

## Step-by-step Processing Logic

### Step 1 — Gate check (is there an architecture decision?)

The tool first looks for a child record where:
- `sw_component` = `core`
- `os` = `common`
- `sw_task` = `architecture`
- `sw_exposure` is **not** `to_be_assigned`, `none`, or blank

This "core/architecture" child is created by the architecture team and tells the tool
what level of driver work is expected.

**If no such child exists → the HSD is skipped.**  
Reason: the arch team has not yet decided if KMD work is needed.

---

### Step 2 — Read the exposure level

The `sw_exposure` value on the `core/architecture` child is copied to any new
`i915_kmd/development` child the tool creates.  This keeps the exposure consistent
between the arch and dev records.

---

### Step 3 — Check for an existing i915_kmd development child

The tool looks for a child where:
- `sw_component` = `i915_kmd`
- `os` = `linux`
- `sw_task` = `development`

**Three possible outcomes:**

#### 3a — Child exists and already has `sw_record` set (e.g. `VLK-12345`)
→ **Skip.** Jira work already exists. Nothing is created.

#### 3b — Child exists but `sw_exposure` is `none` / `to_be_assigned` / blank
→ **Skip.** The KMD team has explicitly indicated no action is required on this child.

#### 3c — Child exists and `sw_record` is empty (no Jira yet)
→ Use the existing child. Go to Step 4.

#### 3d — No i915_kmd/development child exists at all
→ **Create one** with these fixed values:
  - `sw_component` = `i915_kmd`
  - `sw_task` = `development`
  - `os` = `linux`
  - `sw_exposure` = copied from the `core/architecture` child (Step 2)
  - owner = `guptasa2`

Then go to Step 4.

---

### Step 4 — Create Jira issues

The tool creates **9 Jira issues** in project **VLK**, all linked to each other:

```
Epic         [Epic] [TAG] {HSD title}
  Story      [Parent][Story][Code Complete] [TAG] {title}     ← key written to HSD
    Story    [Child][Story][Code Complete][Part-1] [TAG] {title}
    Story    [Child][Story][Code Complete][Part-2] [TAG] {title}
    Story    [Child][Story][Test][IGT] [TAG] {title}
  Story      [Parent][Story][Val][Sim] [TAG] {title}
  Story      [Parent][Story][Val][Emu] [TAG] {title}
  Task       [Parent][Task][Upstream] [TAG] {title}
    Story    [Child][Story][Upstream] [TAG] {title}
```

`[TAG]` is `CRI`, `JGS`, or `TGS` depending on which query the HSD came from
(see Cron section below).

All 9 issues are assigned to `guptasa2` and linked to the HSD via the
`External Issue ID` field (both the parent feature HSD and the sw_impact child ID
are included).

---

### Step 5 — Write Jira key back to HSD

After creating the Jira issues, the tool writes the **Code Complete Parent Story key**
(e.g. `VLK-12345`) into the `sw_record` field of the `i915_kmd/development` sw_impact
child.

This means anyone looking at the HSD in HSDES can see the Jira story key directly on
the child record and click through to Jira from there.

---

## How the Cron Job Works

The background cron job runs the above logic automatically at a configured interval
against a fixed list of HSD queries.

### Query → Project mapping

Each query URL corresponds to one project. The cron uses this mapping directly —
it does **not** re-read `family_affected` or `release_affected` from the HSD record
to figure out the project. The query already tells us the project.

| Query ID | HSD Query | Project |
|---|---|---|
| `14024748599` | CRI GT query (Xe3p / Crescent Island) | CRI |
| `16026920807` | JGS GT query (Xe4 / Jaguar Shores) | JGS |
| `14026130398` | TGS GT query (Xe5 / Tiger Shores) | TGS |

### What happens in one cron pass

1. For each query URL, fetch all HSD IDs and tag them with the project from the table above.
2. For each HSD ID, run Steps 1–5 above.
3. Log results (processed / skipped / errors) to `hsd2jira_cron_log.json`.
4. Wait for the configured interval, then repeat.

**Safe to re-run:** every step checks for existing work before creating anything.
Running the same query twice will not create duplicate Jira issues or duplicate
sw_impact children.

---

## Skip Reasons You Will See in the Log

| Skip reason | What it means |
|---|---|
| `No sw_impact child with sw_component=core…` | Arch team has not decided on driver work yet |
| `kmd dev child has sw_exposure='none'` | KMD team says no action needed |
| `kmd dev child already has sw_record=VLK-XXXXX` | Already processed — Jira exists |
| `Platform/tag could not be determined…` | HSD came from a query not in the project map and field detection also failed |

---

## Key Constants in the Code

All in `routes/hsd2jira.py`:

| Constant | Value | Meaning |
|---|---|---|
| `_PROJECT` | `VLK` | Jira project where issues are created |
| `_ASSIGNEE` | `guptasa2` | Default assignee for new sw_impact children and Jira issues |
| `_QUERY_PROJECT_MAP` | see above | Maps query ID → (platform, project tag) |
| `_CRON_DEFAULT_QUERIES` | list of URLs | Queries the cron runs when no custom list is configured |

---

## Files

| File | Purpose |
|---|---|
| `routes/hsd2jira.py` | All logic — HSD API calls, Jira creation, cron worker |
| `hsd2jira_cron.json` | Cron configuration (interval, enabled flag, custom query list) |
| `hsd2jira_cron_log.json` | Rolling log of the last 5 cron runs |
| `.jira_token` | Personal Access Token for the Jira API (not committed to git) |
