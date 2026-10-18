from pathlib import Path
import os
import re
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
# Local state (SQLite, uploads, caches). The Windows service points this at ProgramData so the
# data survives upgrades; development keeps it in the repo's git-ignored data/ folder.
DATA = Path(os.environ.get('POPPYS_DATA_DIR') or ROOT / 'data')
DATA.mkdir(parents=True, exist_ok=True)
VALUES = dotenv_values(ROOT / '.env.poppys')

def setting(key, default=''):
    return os.environ.get('POPPYS_'+key, VALUES.get(key) or default)

def _company():
    """config/company.json: the company this installation serves (one installation per company). Missing or unreadable →
    the built-in defaults, so an old installation keeps working."""
    import json
    try:
        return json.loads((ROOT / 'config' / 'company.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


COMPANY = _company()
# The Oracle schema this installation reads: .env.poppys / POPPYS_ORACLE_OWNER, else the company profile. Any valid schema
# name may be read; saving is allowed only when the write mapping was made for this same schema (planner.plan checks it).
OWNER = str(setting('ORACLE_OWNER', (COMPANY.get('erp') or {}).get('owner') or 'GARMENT_DEMO')).strip().upper()
if not re.fullmatch(r'[A-Z][A-Z0-9_$#]{0,127}', OWNER):
    raise RuntimeError(f'ORACLE_OWNER "{OWNER}" is not an Oracle schema name. Check .env.poppys.')
