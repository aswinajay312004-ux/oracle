"""Generate a searchable local OCR index; OCR is never treated as ground truth."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import pytesseract
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def scan(path):
    value = pytesseract.image_to_string(Image.open(path), config='--psm 11')
    return {'seconds': int(path.stem), 'timestamp': f'{int(path.stem)//60:02d}:{int(path.stem)%60:02d}', 'text': value}

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(scan, sorted((ROOT / 'frames').glob('*.jpg'))))
    (ROOT / 'ocr_index.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'ocr_index.txt').write_text('\n\n'.join(f"=== {e['timestamp']} ({e['seconds']}s) ===\n{e['text']}" for e in entries), encoding='utf-8')
    print(f'OCR indexed {len(entries)} frames', flush=True)
