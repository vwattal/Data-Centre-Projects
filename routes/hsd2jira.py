"""
HSD→Jira blueprint — loaded from the canonical HSD2Jira2HSD tool.

The single source of truth lives in:
  ../HSD2Jira2HSD/routes/hsd2jira.py

Edit code there. This file is just a loader.
"""
import sys
import importlib.util
from pathlib import Path

_tool_root = Path(__file__).resolve().parents[2] / 'HSD2Jira2HSD'

# Put HSD2Jira2HSD on sys.path so 'from config import ...' inside the blueprint
# resolves to HSD2Jira2HSD/config.py (which points to HSD2Jira2HSD/.jira_token)
if str(_tool_root) not in sys.path:
    sys.path.insert(0, str(_tool_root))

# Load the blueprint module directly to avoid collision with the local 'routes' package
_spec = importlib.util.spec_from_file_location(
    '_hsd2jira_blueprint',
    _tool_root / 'routes' / 'hsd2jira.py',
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['_hsd2jira_blueprint'] = _mod
_spec.loader.exec_module(_mod)

bp = _mod.bp
