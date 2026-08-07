"""
HSD→Jira blueprint — loaded from the canonical HSD2Jira2HSD tool.

The single source of truth lives in:
  ../HSD2Jira2HSD/routes/hsd2jira.py

Edit code there. This file is just a loader.
"""
import sys
import importlib.util
from pathlib import Path

_tool_root = Path(__file__).resolve().parents[1] / 'HSD2Jira2HSD'

# Put HSD2Jira2HSD on sys.path so 'from config import ...' inside the blueprint
# resolves to HSD2Jira2HSD/config.py (which points to HSD2Jira2HSD/.jira_token)
if str(_tool_root) not in sys.path:
    sys.path.insert(0, str(_tool_root))

# Explicitly load HSD2JiraTool's config.py into sys.modules['config'] BEFORE
# exec_module, so 'from config import ...' inside the blueprint always picks up
# the right module even if data_centre_landing's config was already imported.
_cfg_spec = importlib.util.spec_from_file_location('config', _tool_root / 'config.py')
_cfg_mod = importlib.util.module_from_spec(_cfg_spec)
_prev_config = sys.modules.get('config')
sys.modules['config'] = _cfg_mod
_cfg_spec.loader.exec_module(_cfg_mod)

# Load the blueprint module directly to avoid collision with the local 'routes' package
_spec = importlib.util.spec_from_file_location(
    '_hsd2jira_blueprint',
    _tool_root / 'routes' / 'hsd2jira.py',
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['_hsd2jira_blueprint'] = _mod
_spec.loader.exec_module(_mod)

# Restore data_centre_landing's own config module
if _prev_config is not None:
    sys.modules['config'] = _prev_config
elif 'config' in sys.modules:
    del sys.modules['config']

bp = _mod.bp
