from pathlib import Path
import os
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('POPPYS_DATA_DIR') or ROOT / 'data').expanduser().resolve()
DATA.mkdir(parents=True, exist_ok=True)
VALUES = dotenv_values(ROOT / '.env.poppys')

def setting(key, default=''):
    return os.environ.get('POPPYS_'+key, VALUES.get(key) or default)

OWNER = setting('ORACLE_OWNER', 'GARMENT_DEMO')
if OWNER != 'GARMENT_DEMO':
    raise RuntimeError('This demo build is scoped to GARMENT_DEMO.')
