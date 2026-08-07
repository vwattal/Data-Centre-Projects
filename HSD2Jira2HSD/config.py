"""
Configuration for the HSD→Jira tool.

Edit this file when setting up on a new machine — everything deployment-specific
lives here so you never need to touch routes/hsd2jira.py.
"""
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ── Jira bearer token ─────────────────────────────────────────────────────────
# Create a file named .jira_token in this directory containing your PAT.
# See .jira_token.example for the expected format.
HSD2JIRA_TOKEN_PATH = BASE_DIR / '.jira_token'

# ── Jira connection ───────────────────────────────────────────────────────────
JIRA_BASE    = 'https://jira.devtools.intel.com'
JIRA_PROJECT = 'VLK'                # Jira project key

# ── Jira custom field IDs (Intel internal — verify in your Jira instance) ─────
JIRA_CF_EPIC_NAME  = 'customfield_11901'  # Epic Name (required on Epic creation)
JIRA_CF_EPIC_LINK  = 'customfield_11900'  # Epic Link (on Stories/Tasks)
JIRA_CF_EXT_ISSUE  = 'customfield_10808'  # External Issue ID
JIRA_CF_TREND_WW   = 'customfield_34504'  # Actual Trend WW

# ── HSD / ESService ───────────────────────────────────────────────────────────
HSD_BASE = 'https://hsdes.intel.com'

# ── Defaults (overridden per-request from the UI) ─────────────────────────────
DEFAULT_COMPONENT = 'XeKMD'
DEFAULT_ASSIGNEE  = 'mtangri'
