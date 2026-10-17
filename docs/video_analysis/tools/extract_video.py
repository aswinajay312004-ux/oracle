"""Local, read-only extraction of a screen recording for evidence review."""
import concurrent.futures
import json
from pathlib import Path
import cv2
from PIL import Image, ImageDraw, ImageFont

SOURCE = Path(r'C:\Users\ASUS2025\Videos\Captures\GOPAL-PC (83082217) - UltraViewer 2026-10-09 22-24-07.mp4')
ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / 'frames'
SHEETS = ROOT / 'contact_sheets'
DURATION = 2626.511633

def capture_batch(times):
    cap = cv2.VideoCapture(str(SOURCE))
    for second in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = cap.read()
        if ok:
            cv2.imwrite(str(FRAMES / f'{second:04d}.jpg'), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    cap.release()

def sheets(times, prefix, count=12):
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 23)
    for start in range(0, len(times), count):
        chunk = times[start:start + count]
        sheet = Image.new('RGB', (1376, 4 * (388 + 32)), 'white')
        draw = ImageDraw.Draw(sheet)
        for n, second in enumerate(chunk):
            im = Image.open(FRAMES / f'{second:04d}.jpg')
            im.thumbnail((688, 388))
            x, y = (n % 2) * 688, (n // 2) * 420
            sheet.paste(im, (x, y + 32))
            draw.text((x + 6, y + 2), f'{second//60:02d}:{second%60:02d}  ({second}s)', fill='black', font=font)
        sheet.save(SHEETS / f'{prefix}_{start//count+1:02d}.jpg', quality=94)

if __name__ == '__main__':
    times = list(range(0, 2626, 5)) + [2625]
    times = sorted(set(times))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(capture_batch, [times[i::4] for i in range(4)]))
    sheets(list(range(0, 2626, 30)), 'overview', 8)
    sheets(times, 'detail', 8)
    (ROOT / 'video_metadata.json').write_text(json.dumps({
        'source': str(SOURCE), 'duration_seconds': DURATION, 'resolution': [1376, 776],
        'audio': 'Digital silence throughout; ffmpeg volumedetect mean/max -91.0 dB',
        'sample_interval_seconds': 5, 'sample_count': len(times),
        'method': 'OpenCV timestamp seeks; samples are evidence, not a transcript of every frame'
    }, indent=2), encoding='utf-8')
    print(f'Extracted {len(times)} timestamped frames', flush=True)
