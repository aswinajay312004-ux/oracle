"""Offline PDF ingestion: text-layer PDFs become virtual sheets that reuse the workbook detectors.

Each page is laid out as a grid using pdfplumber's text-alignment table strategy, so a PDF
purchase order with a size table and "Buyer: NEXT" style labels is read like a worksheet.
Scanned (image-only) pages have no text layer; they are reported, never guessed.
"""
from __future__ import annotations
from io import BytesIO
import re

MAX_PAGES = 30


def _cellvalue(text):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not text:
        return None
    if re.fullmatch(r'0\d+', text):
        return text  # codes such as style 0008 keep their leading zeros
    if re.fullmatch(r'-?\d{1,3}(,\d{3})+(\.\d+)?|-?\d+(\.\d+)?', text):
        n = float(text.replace(',', ''))
        return int(n) if n.is_integer() else n
    return text


def _address(col, row):
    label = ''
    while col:
        col, rem = divmod(col - 1, 26); label = chr(65 + rem) + label
    return f'{label}{row}'


def _rows(page):
    """Words → lines (by baseline) → cells (split at wide gaps) → columns (clustered x positions)."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=3)
    lines = []
    for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])):
        if lines and abs(lines[-1][0] - w['top']) <= 3:
            lines[-1][1].append(w)
        else:
            lines.append([w['top'], [w]])
    segmented = []
    for _, ws in lines:
        ws.sort(key=lambda w: w['x0']); cells = []
        for w in ws:
            size = (w['bottom'] - w['top']) or 10
            if cells and w['x0'] - cells[-1]['x1'] <= size * 0.9:
                cells[-1]['text'] += ' ' + w['text']; cells[-1]['x1'] = w['x1']
            else:
                cells.append(dict(text=w['text'], x0=w['x0'], x1=w['x1']))
        segmented.append(cells)
    starts = []
    for x in sorted(c['x0'] for cells in segmented for c in cells):
        if not starts or x - starts[-1][-1] > 8:
            starts.append([x])
        else:
            starts[-1].append(x)
    anchors = [sum(s) / len(s) for s in starts]
    return [[(min(range(len(anchors)), key=lambda i: abs(anchors[i] - c['x0'])) + 1, c['text']) for c in cells] for cells in segmented]


def inspect(content):
    if len(content) > 12 * 1024 * 1024:
        raise ValueError('PDF exceeds 12 MB.')
    if not content.startswith(b'%PDF'):
        raise ValueError('Please upload a PDF document.')
    import pdfplumber
    sheets = []; scanned = []
    try:
        pdf = pdfplumber.open(BytesIO(content))
    except Exception as exc:
        raise ValueError('The PDF could not be read. If it is password-protected, save an unprotected copy.') from exc
    with pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise ValueError(f'PDF has more than {MAX_PAGES} pages.')
        for number, page in enumerate(pdf.pages, start=1):
            if not page.extract_words():
                scanned.append(number); continue
            cells = []
            for r, row in enumerate(_rows(page), start=1):
                for c, raw in row:
                    value = _cellvalue(raw)
                    if value is None:
                        continue
                    # "Buyer: NEXT" in one cell becomes label + value cells, like a worksheet.
                    m = re.fullmatch(r'([A-Za-z][A-Za-z .#/()-]{1,30}?)\s*:\s*(.+)', value) if isinstance(value, str) else None
                    if m:
                        cells.append(dict(cell='', row=r, col=c, value=m.group(1).strip(), formula=None, hidden=False, error=False))
                        cells.append(dict(cell='', row=r, col=c + 0.5, value=_cellvalue(m.group(2)), formula=None, hidden=False, error=False))
                    else:
                        cells.append(dict(cell='', row=r, col=c, value=value, formula=None, hidden=False, error=False))
            # Re-index half columns created by label splitting so the grid stays integral.
            cols = sorted({c['col'] for c in cells}); index = {c: i + 1 for i, c in enumerate(cols)}
            for c in cells:
                c['col'] = index[c['col']]; c['cell'] = _address(c['col'], c['row'])
            sheets.append(dict(name=f'PDF page {number}', hidden=False, rows=max((c['row'] for c in cells), default=0),
                               cols=max((c['col'] for c in cells), default=0), merged=[], cells=cells, kind='pdf'))
    if not sheets:
        raise ValueError('This PDF has no readable text (it looks scanned). Ask the buyer for the Excel or a text PDF, '
                         'or enter the order manually.')
    return sheets, scanned
