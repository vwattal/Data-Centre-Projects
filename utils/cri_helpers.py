"""
Shared CRI utilities: PPTX refresh (Graph API), JIRA chip rendering,
and JIRA key extraction from mixed text.

Consumed by routes/cri_e2e.py and routes/cri_kmd_e2e.py.
"""
import json
import re
import requests
import logging
from datetime import datetime
from pathlib import Path

from config import CRI_PPTX, PPTX_SHARING_URL, JIRA_BASE_URL, SOC_AUTOMATION_DIR

logger = logging.getLogger(__name__)


# ── PPTX refresh via Microsoft Graph ─────────────────────────────────────────

def refresh_pptx() -> bool:
    """Download the latest CRI PPTX from SharePoint using a cached Graph token."""
    try:
        import base64
        token_cache = SOC_AUTOMATION_DIR / '.graph_token_cache.json'
        if not token_cache.exists():
            return False
        token_data = json.load(open(token_cache))
        if datetime.now().timestamp() > token_data.get('expires_at', 0):
            from refresh_graph_token import refresh_token
            if not refresh_token():
                return False
            token_data = json.load(open(token_cache))
        token   = token_data['access_token']
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        proxies = {
            'http':  'http://proxy-chain.intel.com:911',
            'https': 'http://proxy-chain.intel.com:912',
        }
        encoded = 'u!' + base64.urlsafe_b64encode(
            PPTX_SHARING_URL.encode()
        ).rstrip(b'=').decode()
        r = requests.get(
            f'https://graph.microsoft.com/v1.0/shares/{encoded}/driveItem',
            headers=headers, proxies=proxies, timeout=20,
        )
        if r.status_code != 200:
            return False
        dl_url = r.json().get('@microsoft.graph.downloadUrl')
        if not dl_url:
            return False
        r2 = requests.get(dl_url, proxies=proxies, stream=True, timeout=30)
        if r2.status_code != 200:
            return False
        with open(CRI_PPTX, 'wb') as f:
            for chunk in r2.iter_content(65536):
                f.write(chunk)
        logger.info(f"Refreshed PPTX: {CRI_PPTX}")
        return True
    except Exception as e:
        logger.warning(f"PPTX refresh failed: {e}")
        return False


# ── JIRA chip helpers ─────────────────────────────────────────────────────────

def chip_class(jid: str) -> str:
    if jid.startswith('VLK'):   return 'chip-vlk'
    if jid.startswith('XPUM'):  return 'chip-xpum'
    if jid.startswith('NEO'):   return 'chip-neo'
    if jid.startswith('GUC'):   return 'chip-guc'
    return 'chip-other'


def extract_jiras(text: str) -> list[str]:
    """Extract JIRA IDs from freeform text (URL or bare key)."""
    if not text:
        return []
    s = str(text).strip()
    if s.lower() in ('done', 'no impact', 'in analysis', 'none', ''):
        return []
    ids = re.findall(
        r'https://jira\.devtools\.intel\.com/browse/([A-Z]+-\d+)', s
    )
    rest = re.sub(
        r'https://jira\.devtools\.intel\.com/browse/[A-Z]+-\d+', '', s
    )
    ids += re.findall(r'\b([A-Z]+-\d+)\b', rest)
    return list(dict.fromkeys(ids))


def chips(jira_ids: list[str]) -> list[dict]:
    """Convert a list of JIRA IDs to chip dicts for template rendering."""
    return [
        {'id': j, 'link': JIRA_BASE_URL + j, 'cls': chip_class(j)}
        for j in jira_ids
    ]
