"""
Shared configuration and path constants for the Data Centre Landing Page.
All route modules import from here to keep paths and settings in one place.
"""
from pathlib import Path
import sys

# Project root (directory containing this file)
BASE_DIR = Path(__file__).parent

# External automation directory (sibling to this project)
SOC_AUTOMATION_DIR = BASE_DIR.parent / 'soc_automation'
sys.path.insert(0, str(SOC_AUTOMATION_DIR))

# CRI data files
CRI_WEEKLY_DIR   = Path('/home/vitasta/CRI_Weekly_review')
CRI_EXCEL        = CRI_WEEKLY_DIR / 'CRI Pre-Map Day.xlsx'
CRI_PPTX         = CRI_WEEKLY_DIR / 'CRI_XPUM_E2E_FEATURE_REVIEW.pptx'
E2E_COMMENTS     = CRI_WEEKLY_DIR / 'e2e_comments.json'
KMD_E2E_COMMENTS = CRI_WEEKLY_DIR / 'kmd_e2e_comments.json'

# Jira / SharePoint URLs
JIRA_BASE_URL  = 'https://jira.devtools.intel.com/browse/'
HSD_BASE_URL   = 'https://hsdes.intel.com/appstore/article-one/#/article/'
PPTX_SHARING_URL = (
    'https://intel.sharepoint.com/:p:/s/IAGS-VTTSWSOSGCLinuxCompute-'
    'XPUManagerTeam/IQAFCL97Bg8qRrV9XSPh5z6KAU4bNMOKIwuGNI8LzDo7X_Q'
)

# Jira token used by KMD-freeze and JGS-upstreaming
JIRA_TOKEN_PATH = Path('/home/vitasta/triage/repos/Projects/soc_automation/.jira_token')

# Backlog token
BACKLOG_TOKEN_PATH = BASE_DIR.parent / 'HSD2Jira2HSD' / '.jira_token'

# HSD2Jira modernisation token
HSD2JIRA_TOKEN_PATH = BASE_DIR / '.jira_token'

# Cache TTL (seconds)
CACHE_TTL = 300

# Base URL of this app (used in email notifications, etc.)
APP_BASE_URL = 'http://10.88.27.190:8888'
