"""Index supplied catalog evidence without connecting to Oracle."""
import hashlib
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(r'C:\Users\ASUS2025\.codex\attachments\5d1041b3-da9b-47df-8632-8059d8716a21\Pasted text.txt')
NAMES = {'ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'ORDCOMP', 'ORDERENTRY', 'ORDFAB', 'ORDCMTFAB',
         'BUYMAS', 'FPLANM', 'FPLANDET', 'FABP', 'FPCOL', 'YARNP', 'PORTIONMAS', 'COUNTMAS',
         'FABMAS', 'STYLENUM', 'STYLEMAS', 'SIZE_MAS', 'COLORMAS', 'DESIGNMAS', 'UNITMAS'}

if __name__ == '__main__':
    source = CATALOG.read_text(encoding='utf-8-sig')
    tables = {}
    for block in re.split(r'(?=^TABLE \d+ of \d+: )', source, flags=re.M)[1:]:
        name = re.search(r'^TABLE \d+ of \d+: (\w+)', block).group(1)
        if name not in NAMES:
            continue
        columns = []
        for line in block.splitlines():
            match = re.match(r'^    (\w+)\s+((?:NUMBER|VARCHAR2|NVARCHAR2|CHAR|NCHAR|DATE|TIMESTAMP|CLOB|BLOB|FLOAT|RAW)[^ ]*)\s*(NOT NULL)?\s*$', line)
            if match:
                columns.append({'name': match[1], 'type': match[2], 'not_null_reported': bool(match[3])})
        links = []
        for a, ac, b, bc, evidence in re.findall(r'^    (\w+)\.(\w+)  <->  (\w+)\.(\w+)    \[([^\]]+)\]', block, flags=re.M):
            links.append({'left': f'{a}.{ac}', 'right': f'{b}.{bc}', 'evidence': evidence,
                          'declared_key_reported_in_supplied_catalog': 'declared key' in evidence,
                          'verified_against_live_database_this_task': False})
        pk = re.search(r'^  Primary key: (.+)', block, flags=re.M)
        tables[name] = {'primary_key_reported': pk[1] if pk else None, 'columns': columns, 'links': links}
    result = {'source': str(CATALOG), 'source_sha256': hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
              'owner_reported': 'GARMENT', 'catalog_date_reported': '2026-10-15',
              'scope': 'Relevant subset of the supplied historical catalog; not live database discovery.',
              'screen_to_column_mappings': 'Candidates requiring Axpert screen definitions/read-back verification.',
              'tables': tables}
    (ROOT / 'catalog_evidence.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    scene_times = [float(v) for v in re.findall(r'pts_time:([0-9.]+)', (ROOT / 'scene_detection.log').read_text(encoding='utf-8', errors='replace'))]
    (ROOT / 'scene_timestamps.json').write_text(json.dumps(scene_times, indent=2), encoding='utf-8')
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 20)
    paths = sorted((ROOT / 'scenes').glob('*.jpg'))
    for start in range(0, len(paths), 16):
        canvas = Image.new('RGB', (1376, 4*282), 'white')
        draw = ImageDraw.Draw(canvas)
        for n, path in enumerate(paths[start:start+16]):
            index = start + n
            im = Image.open(path)
            im.thumbnail((344, 242))
            x, y = (n%4)*344, (n//4)*282
            canvas.paste(im, (x, y+28))
            sec = scene_times[index]
            draw.text((x+3,y+2), f'{index+1}: {int(sec)//60:02d}:{sec%60:04.1f}', fill='black', font=font)
        canvas.save(ROOT / 'contact_sheets' / f'scenes_{start//16+1:02d}.jpg', quality=93)
    print(f'Indexed {len(tables)} catalog tables and {len(scene_times)} scene changes.')
