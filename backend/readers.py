"""Spreadsheet readers. Every accepted upload becomes the same cell model, so every layout detector,
template and validator works the same whatever format the buyer sent:

    [{name, hidden, rows, cols, merged: ["A1:C1"], cells: [cell], capabilities: {...}, reader, notes: []}]

    cell = {cell, row, col, value, formula, hidden, error}, plus these keys only when they say something:
        fmt    number-format code of a number or date cell when it is not General
        raw    the stored number when `value` is the text Excel shows for it (8 formatted 0000 -> '0008')
        flag   'decimal_comma' text such as 1.234,56 left as text for a person to check
               'scientific'    text such as 1.23457E+11: a long code that Excel already damaged
               'big_number'    a 16+ digit number kept as text so that no digit is lost

    capabilities = {merged, hidden_rows, hidden_cols, number_formats, formulas}: True when the reader
        read it from this file, False when the file can hold it but this reader cannot see it, None when
        the format cannot hold it at all. limitations(sheet) words the gaps for people.

Formats (decided from the file's bytes, never from its name alone):
  .xlsx .xlsm .xltx .xltm   openpyxl (formulas, cached results, number formats, hidden rows/columns,
                            merged cells; macros never run). Strict Open XML or damaged files fall back
                            to python-calamine (values only).
  .xls (Excel 97-2003)      xlrd (values, dates, number formats, hidden rows/columns, merged cells);
                            BIFF2-4 files too. Damaged files: xlrd lenient mode, then python-calamine.
  .xlsb                     python-calamine (values, dates, sheet visibility)
  .ods                      python-calamine values + a capped content.xml pass for merges and hidden rows
  Excel 2003 XML (.xml)     defusedxml (no entity expansion); styles give number formats
  HTML / MHTML saved as .xls, .htm, .mht   html.parser + email (ERP "Export to Excel" buttons)
  .csv .tsv .txt            csv with encoding, sep= line and delimiter detection
  .json                     records, NDJSON, API envelopes, sheet maps or a single nested document

Protected files are refused with what to do: password-protected (encrypted) workbooks, Microsoft IRM /
sensitivity-label files, and other Office documents (Word, PowerPoint, Outlook e-mails).

Safety: cell contents are data only - nothing is executed (backend/formulas.py calculates only simple
arithmetic formulas a file saved without their result). Sizes are capped before any
parser runs (bytes, real decompressed zip sizes, cells, sheets, merged areas, nesting), and read()
parses in a separate worker process with a time limit and a memory cap (backend/isolation.py).
Environment: POPPYS_PARSE_TIMEOUT (seconds, default 60), POPPYS_PARSE_MEMORY_MB (default 1536),
POPPYS_PARSE_WORKERS (parallel uploads, default 2), POPPYS_INLINE_PARSE=1 (no worker: tests only).
selfcheck() reports the parser platform (Python, Expat, packages, isolation) for the health page.
"""
from __future__ import annotations
import bisect
import codecs
from collections import Counter
import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from io import BytesIO, StringIO
import io
import json
import logging
import math
import os
import posixpath
import re
import struct
import sys
import warnings
import zipfile
import zlib

from . import formulas, isolation, numfmt

MAX_BYTES = 12 * 1024 * 1024
MAX_SHEETS = 30
MAX_CELLS = 250_000            # per sheet
MAX_TOTAL_CELLS = 1_000_000    # per file
MAX_ZIP_ENTRIES = 10_000
MAX_EXPANDED = 80 * 1024 * 1024        # all parts of a zip-based workbook, measured, not declared
MAX_PART = 40 * 1024 * 1024            # one part: a sheet's XML, the shared strings, ODS content.xml
MAX_JSON_DEPTH = 20
MAX_MERGE_AREA = 10_000        # cells in one merged range; larger ranges are ignored
MAX_MERGES = 20_000            # merged ranges kept per sheet
MAX_MERGED_CELLS = 250_000     # cells covered by all kept merged ranges of one sheet
MAX_SPAN_CELLS = 1_000_000     # cells covered by row-spans in one HTML page
MAX_TEXT = 32 * 1024 * 1024    # characters of text (values, formulas, formats) in one file's cell model
EXCEL_ROWS, EXCEL_COLS = 1_048_576, 16_384
TEXT_NAMES = ('.csv', '.tsv', '.txt')


def _env_int(name, default, low, high):
    try:
        return max(low, min(int(os.environ.get(name) or default), high))
    except ValueError:
        return default


# Worker limits per upload; an administrator can raise them for a laptop that reads very large workbooks.
PARSE_TIMEOUT = _env_int('POPPYS_PARSE_TIMEOUT', 60, 10, 600)                          # seconds
PARSE_MEMORY = _env_int('POPPYS_PARSE_MEMORY_MB', 1536, 256, 16384) * 1024 * 1024        # bytes
INLINE_ENV = 'POPPYS_INLINE_PARSE'     # '1': parse in this process (tests, debugging)

OLE2 = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
ZIP = b'PK\x03\x04'
ODS_MIME = b'application/vnd.oasis.opendocument.spreadsheet'
SS_NS = 'urn:schemas-microsoft-com:office:spreadsheet'

ACCEPT = ('.xlsx', '.xlsm', '.xltx', '.xltm', '.xls', '.xlsb', '.ods', '.xml', '.htm', '.html', '.mht', '.mhtml',
          '.csv', '.tsv', '.txt', '.json')
LABELS = {
    'ooxml': 'Excel workbook', 'xls': 'Excel 97-2003 workbook', 'xlsb': 'Excel binary workbook',
    'ods': 'OpenDocument spreadsheet', 'xml2003': 'Excel 2003 XML spreadsheet', 'html': 'Excel web-page export',
    'mhtml': 'Excel single-file web page', 'csv': 'CSV / text export', 'json': 'JSON data',
}
NOUNS = {'ooxml': 'workbook', 'xls': '.xls workbook', 'xlsb': '.xlsb workbook', 'ods': 'OpenDocument spreadsheet',
         'xml2003': 'Excel 2003 XML file', 'html': 'web-page export', 'mhtml': 'single-file web page',
         'csv': 'text file', 'json': 'JSON file'}

# Messages shown to people as-is: plain English, and each says what to do.
MSG_EMPTY = 'The file is empty. Check that it was saved completely and upload it again.'
MSG_NOT_EXCEL = 'This is not an Excel workbook. Upload the buyer file as .xlsx, .xls, .xlsb, .ods, .csv or .json.'
MSG_TOO_BIG = 'The file is larger than 12 MB. Delete unused sheets, pictures and empty formatted rows, save it and upload again.'
MSG_DAMAGED = 'This workbook is damaged or incomplete. Open it in Excel, save it again and upload.'
MSG_EXPANDS = ('This workbook expands to more than 80 MB when opened, so it cannot be read safely. Delete unused sheets, '
               'pictures and empty formatted rows in Excel, save it and upload again.')
MSG_PART = ('One sheet of this workbook expands to more than 40 MB, usually because thousands of empty rows or columns are '
            'formatted. Delete the unused rows and columns in Excel, save it and upload again.')
MSG_LYING = ('This workbook is damaged: one of its parts is larger than the file says, so it was not opened. '
             'Open it in Excel, save it again and upload.')
MSG_PASSWORD = ('This workbook is password-protected, so it cannot be read. Open it in Excel, remove the password '
                '(File > Info > Protect Workbook > Encrypt with Password, then clear the password), save it and upload again.')
MSG_IRM = ('This workbook is protected by Microsoft Information Rights Management (a sensitivity label or "Restrict Access"), '
           'which cannot be opened offline. Ask the sender for an unprotected copy, or open it in Excel, remove the '
           'restriction (File > Info > Protect Workbook), save it and upload again.')
MSG_WORD = ('This is a Word document, not an Excel workbook. Ask the buyer for the Excel file, or copy the order table '
            'into Excel, save it as .xlsx and upload that.')
MSG_POWERPOINT = ('This is a PowerPoint presentation, not an Excel workbook. Ask the buyer for the Excel file, or copy '
                  'the table into Excel, save it as .xlsx and upload that.')
MSG_OUTLOOK = ('This is an Outlook e-mail (.msg), not an Excel workbook. Open the e-mail, save the attached Excel file '
               'to your computer and upload that.')
MSG_EMAIL = ('This is a saved e-mail, not an Excel workbook. Open the e-mail, save the attached Excel file to your '
             'computer and upload that.')
MSG_OFFICE = ('This is a Microsoft Office file that holds no Excel workbook. Open it in the program that made it, save '
              'the order sheet as an Excel workbook (.xlsx) and upload that.')
MSG_ODT = ('This is an OpenDocument text document, not a spreadsheet. Copy the order table into a spreadsheet, save it '
           'as .xlsx or .ods and upload that.')
MSG_NUMBERS = 'This is an Apple Numbers file. In Numbers choose File > Export To > Excel, then upload the .xlsx file.'
MSG_ZIP = 'This file is a zip archive, not an Excel workbook. Unzip it and upload the Excel file inside.'
# Some .xls files are encrypted with Excel's built-in default password, so Excel opens them without asking.
MSG_XLS_PASSWORD = ('This .xls workbook is password-protected (encrypted), so it cannot be read. Open it in Excel, remove the '
                    'password if there is one (File > Info > Protect Workbook > Encrypt with Password), then save it as '
                    '.xlsx and upload that.')
MSG_ODS_PASSWORD = ('This spreadsheet is password-protected, so it cannot be read. Open it in LibreOffice, choose File > '
                    'Save As, clear "Save with password", save it and upload again.')
MSG_TOO_MUCH_TEXT = ('This file holds more than 32 million characters of text, far more than an order needs, so it was not '
                     'read. Delete unused sheets, long notes and repeated text, save it and upload again.')
MSG_XLS_DAMAGED = 'This .xls workbook could not be read. Open it in Excel, save it as .xlsx and upload again.'
MSG_CANNOT_OPEN = 'This workbook could not be opened. Open it in Excel, save it again as an Excel Workbook (.xlsx) and upload.'
MSG_BACKUP_READER = 'Read with the backup reader because the main reader could not open this workbook ({why}).'
WORKER_MESSAGES = {
    'timeout': (f'Reading this file took longer than {PARSE_TIMEOUT} seconds, so it was stopped. Open it in Excel, delete '
                'unused sheets and empty formatted rows, save it as .xlsx and upload again.'),
    'memory': ('This file needs more memory to read than is safe on this computer, so it was stopped. Delete unused '
               'sheets, pictures and empty formatted rows, save it and upload again.'),
    'crashed': ('The reader stopped unexpectedly while opening this file, usually because the file is damaged or needs '
                'more memory than is safe. Open it in Excel, save it again as .xlsx and upload.'),
    'busy': 'Other files are being read right now. Wait a minute and upload again.',
    'too_large': 'This file holds too much data to show. Split it into smaller workbooks and upload them one at a time.',
}
BINARY_SIGNATURES = (
    (b'%PDF', 'This is a PDF file, not an Excel workbook. Ask the buyer for the Excel file (.xlsx or .xls) and upload that.'),
    (b'\x89PNG', 'This is a picture, not an Excel workbook. Ask the buyer for the Excel file (.xlsx or .xls) and upload that.'),
    (b'\xff\xd8\xff', 'This is a picture, not an Excel workbook. Ask the buyer for the Excel file (.xlsx or .xls) and upload that.'),
    (b'GIF8', 'This is a picture, not an Excel workbook. Ask the buyer for the Excel file (.xlsx or .xls) and upload that.'),
    (b'{\\rtf', 'This is a Word (RTF) document, not an Excel workbook. Copy the order table into Excel, save it as .xlsx and upload that.'),
    (b'Rar!', 'This is a RAR archive, not an Excel workbook. Extract it and upload the Excel file inside.'),
    (b'7z\xbc\xaf\x27\x1c', 'This is a 7-Zip archive, not an Excel workbook. Extract it and upload the Excel file inside.'),
    (b'\x1f\x8b', 'This is a compressed (gzip) file, not an Excel workbook. Extract it and upload the Excel file inside.'),
)

log = logging.getLogger('poppys.readers')
stats = {'isolated': 0, 'inline': 0, 'inline_fallback': 0}


class Unsupported(ValueError):
    """The upload is not a spreadsheet this product reads (shown to the user as-is)."""


class Unreadable(ValueError):
    """A spreadsheet this product reads, refused as uploaded: too large, damaged, nested too deeply.
    The message says what to do (shown to the user as-is)."""


def address(col, row):
    label = ''
    while col:
        col, rem = divmod(col - 1, 26); label = chr(65 + rem) + label
    return f'{label}{row}'


def clean(value):
    """One value convention for every reader: trimmed text, ISO dates, whole floats as ints, exact
    decimals as int, float or (when a float would lose digits) text."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace('\xa0', ' ').strip()
        if not value:
            return None
        return value if value.isascii() else _valid_text(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return '#NUM!'
        return int(value) if value.is_integer() and abs(value) < 1e15 else value
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec='seconds')
    if isinstance(value, timedelta):
        return str(value)
    return value


def _valid_text(value):
    """Text that can always be saved and sent as UTF-8 (lone surrogates from broken files become U+FFFD)."""
    try:
        value.encode('utf-8')
        return value
    except UnicodeEncodeError:
        return re.sub('[\ud800-\udfff]', '\ufffd', value)


def _decimal(d):
    if not d.is_finite():
        return '#NUM!'
    try:                     # adjusted() needs no arithmetic, so 1E+999999999 cannot overflow here
        if d.adjusted() < 15 and d == d.to_integral_value():
            return int(d)
        f = float(d)
        if math.isfinite(f) and Decimal(repr(f)) == d:
            return int(f) if f.is_integer() and abs(f) < 1e15 else f
    except (ArithmeticError, ValueError):     # decimal.Overflow / InvalidOperation are ArithmeticErrors
        pass
    return str(d)            # more digits than a float can hold: keep every digit as text


# Western (1,500,000) and Indian lakh/crore (15,00,000 and 12,34,567.50) digit grouping.
NUMBER = re.compile(r'^[-+]?(\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})+,\d{3}|\d+)(\.\d+)?$')
DECIMAL_COMMA = re.compile(r'^[-+]?(\d{1,3}(\.\d{3})+|\d+),\d+$')
SCIENTIFIC = re.compile(r'^[-+]?\d+(\.\d+)?[eE][-+]?\d+$')
LONG_DIGITS = re.compile(r'^[-+]?[1-9]\d{15,}(\.\d+)?$')


def typed(text):
    """Text from CSV/HTML/XML exports, typed the way Excel would show it. Codes with leading zeros
    (style 0008) stay text; '1,500' and '1,50,000' become numbers; 16+ digit numbers stay text so that
    no digit is lost; decimal-comma text (1.234,56) stays text."""
    value = clean(text)
    if not isinstance(value, str) or not NUMBER.match(value):
        return value
    digits = value.lstrip('+-')
    if len(digits) > 1 and digits[0] == '0' and digits[1] != '.':
        return value
    plain = value.replace(',', '')
    if len(plain.lstrip('+-').split('.')[0]) > 15:
        return value          # 16+ digits: a code, and more digits than Excel (or a float) keeps
    return int(plain) if '.' not in plain else float(plain)


def _text_value(text):
    """(typed value, extra cell keys) for exported text, flagging text a person should check."""
    value = typed(text)
    if isinstance(value, str):
        if DECIMAL_COMMA.match(value):
            return value, {'flag': 'decimal_comma'}
        if SCIENTIFIC.match(value):
            return value, {'flag': 'scientific'}
        if LONG_DIGITS.match(value.replace(',', '')):
            return value, {'flag': 'big_number'}
    return value, {}


def _formatted(value, code):
    """(value, extra cell keys) for a stored number or date shown with number format `code`: digits-only
    code formats become the text Excel shows (keeping the number as `raw`)."""
    if not code or code == 'General' or isinstance(value, bool) or not isinstance(value, (int, float, date, time, timedelta)):
        return value, {}
    extras = {'fmt': str(code)[:255]}
    if isinstance(value, (int, float)):
        shown = numfmt.render(value, code)
        if shown is not None:
            extras['raw'] = clean(value)
            return shown, extras
    return value, extras


class _Spans:
    """Row or column numbers kept as inclusive ranges: one record can hide a million rows."""

    def __init__(self, limit):
        self.limit = limit; self._ranges = []; self._starts = None; self._ends = None

    def add(self, first, last=None):
        last = first if last is None else last
        first, last = max(1, int(first)), min(int(last), self.limit)
        if first <= last:
            self._ranges.append((first, last)); self._starts = None

    def _build(self):
        merged = []
        for a, b in sorted(self._ranges):
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self._ranges = [(a, b) for a, b in merged]
        self._starts = [a for a, _ in merged]; self._ends = [b for _, b in merged]

    def __contains__(self, n):
        if not self._ranges:
            return False
        if self._starts is None:
            self._build()
        i = bisect.bisect_right(self._starts, n) - 1
        return i >= 0 and n <= self._ends[i]

    def __bool__(self):
        return bool(self._ranges)


CAPABILITY_KEYS = ('merged', 'hidden_rows', 'hidden_cols', 'number_formats', 'formulas')
FULL_CAPS = dict.fromkeys(CAPABILITY_KEYS, True)
VALUES_ONLY = dict.fromkeys(CAPABILITY_KEYS, False)
TEXT_CAPS = dict(merged=None, hidden_rows=None, hidden_cols=None, number_formats=True, formulas=None)


class Builder:
    """Collects cells for one sheet with limits and the shared cell format."""

    def __init__(self, name, hidden=False, capabilities=None, reader=''):
        self.name = str(name)[:120] or 'Sheet1'; self.hidden = bool(hidden); self.cells = {}; self.merged = set()
        self.hidden_rows = _Spans(EXCEL_ROWS); self.hidden_cols = _Spans(EXCEL_COLS)
        self.capabilities = dict(capabilities if capabilities is not None else VALUES_ONLY)
        self.reader = reader; self.notes = []
        self.dropped_merges = 0      # merged ranges larger than MAX_MERGE_AREA
        self.excess_merges = 0       # merged ranges past MAX_MERGES / MAX_MERGED_CELLS
        self.bad_merges = 0          # merged-range references that could not be read
        self.merged_area = 0; self.outside = 0

    def put(self, row, col, value, formula=None, error=False, **extra):
        if isinstance(value, float) and not math.isfinite(value):
            value, error = '#NUM!', True
        value = clean(value)
        if value is None:
            return
        self._add(row, col, value, formula, error, extra)

    def put_formula(self, row, col, formula):
        """A formula without a calculated result: kept visible to the parser."""
        self._add(row, col, None, formula, False, {})

    def _add(self, row, col, value, formula, error, extra):
        if not (1 <= row <= EXCEL_ROWS and 1 <= col <= EXCEL_COLS):
            self.outside += 1             # Excel itself cannot show it (HTML, JSON and text have no grid limit)
            return
        if len(self.cells) >= MAX_CELLS and (row, col) not in self.cells:
            raise Unreadable(f'{self.name}: sheet exceeds {MAX_CELLS:,} filled cells; trim it and upload again.')
        cell = dict(cell=address(col, row), row=row, col=col, value=value, formula=formula, error=bool(error))
        for key, v in extra.items():
            if v is not None:
                cell[key] = v
        self.cells[(row, col)] = cell

    def merge(self, r0, c0, r1, c1):
        """A merged range (1-based, inclusive), clamped to Excel's grid; absurd ranges are ignored."""
        r0, r1 = sorted((int(r0), int(r1))); c0, c1 = sorted((int(c0), int(c1)))
        r0, c0, r1, c1 = max(1, r0), max(1, c0), min(r1, EXCEL_ROWS), min(c1, EXCEL_COLS)
        if r1 < r0 or c1 < c0 or (r0 == r1 and c0 == c1):
            return
        box = (r0, c0, r1, c1)
        if box in self.merged:
            return
        area = (r1 - r0 + 1) * (c1 - c0 + 1)
        if area > MAX_MERGE_AREA:
            self.dropped_merges += 1
        elif len(self.merged) >= MAX_MERGES or self.merged_area + area > MAX_MERGED_CELLS:
            self.excess_merges += 1      # what the merged areas cover is bounded, not only their number
        else:
            self.merged.add(box); self.merged_area += area

    def sheet(self):
        rows = max((r for r, _ in self.cells), default=0); cols = max((c for _, c in self.cells), default=0)
        hr, hc = self.hidden_rows, self.hidden_cols
        cells = [dict(c, hidden=c['row'] in hr or c['col'] in hc) for _, c in sorted(self.cells.items())]
        notes = list(self.notes)
        if self.dropped_merges:
            notes.append(f'{self.dropped_merges:,} merged areas larger than {MAX_MERGE_AREA:,} cells were ignored.')
        if self.excess_merges:
            notes.append(f'{self.excess_merges:,} merged areas were ignored because the sheet has too many (the limit is '
                         f'{MAX_MERGES:,} areas covering {MAX_MERGED_CELLS:,} cells).')
        if self.bad_merges:
            notes.append(f'{self.bad_merges:,} merged areas could not be read and were ignored.')
        if self.outside:
            notes.append(f'{self.outside:,} cells beyond Excel\'s last row ({EXCEL_ROWS:,}) or column ({EXCEL_COLS:,}) were ignored.')
        caps = {k: self.capabilities.get(k, False) for k in CAPABILITY_KEYS}
        merged = [f'{address(c0, r0)}:{address(c1, r1)}' for r0, c0, r1, c1 in sorted(self.merged)]
        return dict(name=self.name, hidden=self.hidden, rows=rows, cols=cols, merged=merged, cells=cells,
                    capabilities=caps, reader=self.reader, notes=notes)


CAPABILITY_TEXT = {'merged': 'merged cells', 'hidden_rows': 'hidden rows', 'hidden_cols': 'hidden columns',
                   'number_formats': 'number formats (for example leading zeros in codes such as 0008)'}


def limitations(sheet):
    """Plain-English notes on what this sheet's reader could not provide (for the UI and draft notes)."""
    notes = list(sheet.get('notes') or [])
    missing = [text for key, text in CAPABILITY_TEXT.items() if (sheet.get('capabilities') or {}).get(key) is False]
    if missing:
        listed = missing[0] if len(missing) == 1 else ', '.join(missing[:-1]) + ' and ' + missing[-1]
        notes.append(f'The {listed} of sheet "{sheet.get("name")}" could not be read from this file; check them in the original file.')
    return notes


# --- format detection ---------------------------------------------------------------------------
def _zname(name):
    return name.replace('\\', '/').lstrip('/').lower()


def _zip_entry_count(content):
    """Entries declared by the end-of-central-directory record, read before zipfile builds them all."""
    tail = content[-(65535 + 22):]
    at = tail.rfind(b'PK\x05\x06')
    if at < 0 or len(tail) - at < 22:
        return None
    count = struct.unpack('<H', tail[at + 10:at + 12])[0]
    return None if count == 0xFFFF else count


def _zip_infos(content):
    count = _zip_entry_count(content)
    # zipfile ignores the declared count and builds every central-directory record it finds, so the
    # records themselves are counted too (a cheap scan; a stray match in compressed data is harmless).
    if (count is not None and count > MAX_ZIP_ENTRIES) or content.count(b'PK\x01\x02') > MAX_ZIP_ENTRIES:
        raise Unreadable(f'This workbook has more than {MAX_ZIP_ENTRIES:,} internal parts, so it cannot be read safely. '
                         'Open it in Excel, save it again and upload.')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
    except Exception as exc:     # BadZipFile, LargeZipFile, struct.error, ValueError, EOFError ...
        raise Unsupported(MSG_DAMAGED) from exc
    if len(infos) > MAX_ZIP_ENTRIES:
        raise Unreadable(f'This workbook has more than {MAX_ZIP_ENTRIES:,} internal parts, so it cannot be read safely. '
                         'Open it in Excel, save it again and upload.')
    if sum(i.file_size for i in infos) > MAX_EXPANDED:
        raise Unreadable(MSG_EXPANDS)
    return infos


def _zip_kind(content):
    infos = _zip_infos(content)
    names = {_zname(i.filename): i for i in infos}
    if 'xl/workbook.bin' in names:
        return 'xlsb'
    mimetype = names.get('mimetype')
    if mimetype is not None and mimetype.file_size <= 256:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                declared = archive.read(mimetype).strip()
        except Exception as exc:
            raise Unsupported(MSG_DAMAGED) from exc
        if declared.startswith(ODS_MIME):
            if _odf_encrypted(content, names.get('meta-inf/manifest.xml')):
                raise Unsupported(MSG_ODS_PASSWORD)
            return 'ods'
        if declared.startswith(b'application/vnd.oasis.opendocument.text'):
            raise Unsupported(MSG_ODT)
    if 'xl/workbook.xml' in names or ('[content_types].xml' in names and any(n.startswith('xl/') for n in names)):
        return 'ooxml'
    if 'word/document.xml' in names:
        raise Unsupported(MSG_WORD)
    if 'ppt/presentation.xml' in names:
        raise Unsupported(MSG_POWERPOINT)
    if 'index/document.iwa' in names or 'index.xml' in names:
        raise Unsupported(MSG_NUMBERS)
    raise Unsupported(MSG_ZIP)


def _odf_encrypted(content, manifest):
    """True when an OpenDocument file was saved with a password: its manifest lists encryption data."""
    if manifest is None or manifest.file_size > 1024 * 1024:
        return False
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            return b'encryption-data' in archive.read(manifest)
    except Exception:
        return False


def _ole_kind(content):
    """Excel 97-2003 workbook, or the exact reason this OLE2 (compound) file cannot be read."""
    try:
        import olefile
        ole = olefile.OleFileIO(BytesIO(content))
        try:
            entries = ['/'.join(path).lower() for path in ole.listdir(streams=True, storages=True)]
        finally:
            ole.close()
    except Exception:       # not parseable by olefile: let xlrd (lenient) and calamine try and explain
        return 'xls'
    roots = {e.split('/', 1)[0] for e in entries}
    if any('drmencrypted' in e or e.endswith('\x09drmcontent') or e.endswith('\x09drmviewercontent') for e in entries):
        raise Unsupported(MSG_IRM)
    if 'encryptioninfo' in roots and 'encryptedpackage' in roots:
        raise Unsupported(MSG_PASSWORD)
    if 'encryptedpackage' in roots:          # a protected package without password information: IRM
        raise Unsupported(MSG_IRM)
    if 'workbook' in roots or 'book' in roots:
        return 'xls'
    if 'worddocument' in roots:
        raise Unsupported(MSG_WORD)
    if 'powerpoint document' in roots:
        raise Unsupported(MSG_POWERPOINT)
    if any(r.startswith('__substg1.0_') or r == '__properties_version1.0' for r in roots):
        raise Unsupported(MSG_OUTLOOK)
    raise Unsupported(MSG_OFFICE)


def _is_biff(content):
    """A raw BIFF2-BIFF5 workbook or worksheet stream (Excel 2.x-5.0 without an OLE2 container)."""
    if len(content) < 8:
        return False
    opcode, length, _, kind = struct.unpack('<HHHH', content[:8])
    return opcode in (0x0009, 0x0209, 0x0409, 0x0809) and 4 <= length <= 20 and kind in (0x0005, 0x0010, 0x0020, 0x0040, 0x0100)


def _utf16_guess(sample):
    """'utf-16-le' / 'utf-16-be' for BOM-less UTF-16 text (every other byte NUL), else None."""
    sample = sample[:len(sample) - len(sample) % 2]
    if len(sample) < 4:
        return None
    even, odd = sample[0::2], sample[1::2]
    zeros_even, zeros_odd = even.count(0) / len(even), odd.count(0) / len(odd)
    for codec, zeros, other in (('utf-16-le', zeros_odd, zeros_even), ('utf-16-be', zeros_even, zeros_odd)):
        if zeros >= 0.3 and other <= 0.05:
            try:
                text = sample.decode(codec)
            except UnicodeDecodeError:
                continue
            if sum(ch.isprintable() or ch in '\t\r\n' for ch in text) >= 0.95 * len(text):
                return codec
    return None


def _text_codec(name):
    """A Python text codec for a declared charset, or None (unknown, bytes-to-bytes, or UTF-7)."""
    if not name:
        return None
    try:
        info = codecs.lookup(str(name).strip().strip('"\'').strip())
    except (LookupError, ValueError):
        return None
    if not getattr(info, '_is_text_encoding', True) or info.name.startswith('utf-7'):
        return None
    if info.name in ('ascii', 'iso8859-1', 'latin-1'):
        return 'cp1252'           # what browsers and Excel do with these labels
    return info.name


def _mostly_utf8(content):
    """True for UTF-8 text with a few stray bytes (an ERP export that mixed encodings). Valid multi-byte
    UTF-8 sequences almost never occur by chance in Windows-1252 text, so at least as many of them as
    invalid bytes is strong evidence."""
    text = content.decode('utf-8', errors='replace')
    bad = text.count('\ufffd')
    good = sum(1 for ch in text if ord(ch) > 127) - bad
    return good >= 2 and bad <= good


def _detect_encoding(content):
    """Encoding of non-UTF-8 text from a closed candidate list (never an exotic code page)."""
    sample = content[:65536]
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(sample[:len(sample) - len(sample) % 2],
                          cp_isolation=['cp1252', 'latin_1', 'utf_16_le', 'utf_16_be']).best()
        encoding = best.encoding if best else None
    except Exception:          # detection is a convenience; the fallback below always works
        encoding = None
    if encoding in (None, 'latin_1', 'latin-1', 'iso8859_1'):
        try:
            content.decode('cp1252')
            return 'cp1252'
        except UnicodeDecodeError:
            return 'latin_1' if encoding else 'cp1252'
    return encoding


def _decode(content, declared=None):
    """Text of an upload: BOM, then a declared charset, then BOM-less UTF-16, strict UTF-8,
    charset-normalizer over cp1252/latin-1/UTF-16, and finally cp1252 with replacement characters."""
    if content.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return content.decode('utf-32', errors='replace')
    if content.startswith(b'\xef\xbb\xbf'):
        return content[3:].decode('utf-8', errors='replace')
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        return content.decode('utf-16', errors='replace')
    codec = _text_codec(declared)
    if codec:
        try:
            return content.decode(codec)
        except (UnicodeDecodeError, LookupError):
            pass
    guess = _utf16_guess(content[:4096])
    if guess:
        return content.decode(guess, errors='replace')
    try:
        return content.decode('utf-8')
    except UnicodeDecodeError:
        pass
    if _mostly_utf8(content):
        return content.decode('utf-8', errors='replace')
    return content.decode(_detect_encoding(content), errors='replace')


def _decode_head(sample):
    """Cheap decoding of the first bytes for sniffing (no detection library)."""
    if sample.startswith((b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff')):
        return _decode(sample)
    guess = _utf16_guess(sample)
    if guess:
        return sample.decode(guess, errors='replace')
    try:
        return sample.decode('utf-8')
    except UnicodeDecodeError:
        return sample.decode('utf-8' if _mostly_utf8(sample) else 'cp1252', errors='replace')


def _is_mime(head):
    """An RFC 822 header block with MIME-Version: an MHTML page (or a saved e-mail)."""
    block = re.split(r'\r?\n\r?\n', head, maxsplit=1)[0]
    seen = False
    for line in block.splitlines()[:80]:
        if line[:1] in (' ', '\t'):
            continue
        name, sep, _ = line.partition(':')
        if not sep or not re.fullmatch(r'[!-9;-~]+', name):
            return False
        seen = seen or name.lower() == 'mime-version'
    return seen


def _root_element(text):
    """(prefix, local name, attributes) of the first element after the XML prolog, or None."""
    s = text
    for _ in range(100):
        s = s.lstrip()
        if s.startswith('<?'):
            end = s.find('?>'); s = s[end + 2:] if end >= 0 else ''
        elif s.startswith('<!--'):
            end = s.find('-->'); s = s[end + 3:] if end >= 0 else ''
        elif s.startswith('<!'):
            end, subset = s.find('>'), s.find('[')
            if 0 <= subset < end:                       # DOCTYPE with an internal subset
                close = s.find(']', subset)
                end = s.find('>', close) if close >= 0 else -1
            s = s[end + 1:] if end >= 0 else ''
        else:
            break
    m = re.match(r'<([A-Za-z_][\w.\-]*)(?::([A-Za-z_][\w.\-]*))?(\s[^>]*)?/?>', s)
    if not m:
        return None
    prefix, local = (m.group(1), m.group(2)) if m.group(2) else (None, m.group(1))
    attrs = {k.lower(): v.strip('"\'') for k, v in re.findall(r'([\w:.\-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', m.group(3) or '')}
    return prefix, local, attrs


SS_WORKBOOK = re.compile(r'<(?:[A-Za-z_][\w.\-]*:)?Workbook\b[^>]{0,4000}?urn:schemas-microsoft-com:office:spreadsheet')
HTML_TABLE = re.compile(r'<\s*table[\s>]', re.I)
HTML_CELL = re.compile(r'<\s*t[dh][\s>/]', re.I)


def _markup_kind(head, content, filename=''):
    root = _root_element(head)
    if root is None and len(content) > 8192:          # a prolog (comments, DOCTYPE) longer than the sniffed head
        root = _root_element(_decode_head(content[:1 << 20]))
    if root:
        prefix, local, attrs = root
        namespace = attrs.get(f'xmlns:{prefix.lower()}' if prefix else 'xmlns')
        if local == 'Workbook' and namespace == SS_NS:
            return 'xml2003'
    wide = content[:2] in (b'\xff\xfe', b'\xfe\xff') or _utf16_guess(content[:4096])
    text = _decode(content) if wide else content.decode('latin-1')     # latin-1: every byte, one char, for searching
    if root is None and SS_WORKBOOK.search(text[:1 << 20]):
        return 'xml2003'          # a SpreadsheetML root we could not reach: its reader refuses DTDs plainly
    # SpreadsheetML's <Table> is not an HTML table: an HTML table always has <td> or <th> cells.
    if HTML_TABLE.search(text) and HTML_CELL.search(text):
        return 'html'
    if filename.lower().endswith(TEXT_NAMES) and any(d in head for d in DELIMITERS):
        return 'csv'              # a text export whose first value starts with '<'
    raise Unsupported('This XML/HTML file contains no spreadsheet table. Upload the buyer file as .xlsx, .xls, .csv or .json.')


def sniff(content, filename=''):
    """The real format of the bytes: ooxml, xls, xlsb, ods, xml2003, html, mhtml, csv or json."""
    if not content:
        raise Unsupported(MSG_EMPTY)
    if len(content) > MAX_BYTES:     # the server reads MAX_BYTES + 1: a cut-off zip must not be called "damaged"
        raise Unreadable(MSG_TOO_BIG)
    try:
        return _sniff(bytes(content), str(filename or ''))
    except (Unsupported, Unreadable):
        raise
    except Exception as exc:    # sniffing must never turn into a server error
        log.warning('sniff failed: %s: %s', type(exc).__name__, exc)
        raise Unsupported(MSG_NOT_EXCEL) from exc


def _sniff(content, filename):
    if content.startswith((ZIP, b'PK\x05\x06')):
        return _zip_kind(content)
    if content.startswith(OLE2):
        return _ole_kind(content)
    if _is_biff(content):
        return 'xls'
    for signature, message in BINARY_SIGNATURES:
        if content.startswith(signature):
            raise Unsupported(message)
    sample = content[:8192]
    bom = sample.startswith((b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff'))
    if b'\x00' in sample[:4096] and not bom and not _utf16_guess(sample[:4096]):
        raise Unsupported(MSG_NOT_EXCEL)
    head = _decode_head(sample).lstrip('\ufeff \t\r\n')
    probe = head[:4096]
    if probe and sum(1 for ch in probe if ord(ch) < 32 and ch not in '\t\r\n\f\v') > 0.05 * len(probe):
        raise Unsupported(MSG_NOT_EXCEL)
    if _is_mime(head):
        return 'mhtml'
    if head[:1] in ('{', '['):
        return 'json'             # a text file that only starts like JSON is retried as CSV (_read_inline)
    if head.startswith('<'):
        return _markup_kind(head, content, filename)
    text_name = filename.lower().endswith(TEXT_NAMES)
    if not text_name and HTML_TABLE.search(head) and HTML_CELL.search(head):
        return 'html'             # an "Excel" web export that starts with a line of text before its <table>
    if text_name or any(d in head for d in DELIMITERS):
        return 'csv'
    raise Unsupported(MSG_NOT_EXCEL)


# --- reading (isolated by default) --------------------------------------------------------------------
def _inline_requested():
    return os.environ.get(INLINE_ENV, '').strip().lower() in ('1', 'true', 'yes')


def read(content, filename=''):
    """(format, sheets) for an upload. Raises Unsupported or ValueError with a user-facing reason.

    The file is parsed in a separate worker process with a time limit and a memory cap (see
    backend/isolation.py), so a hostile or broken file cannot stall or exhaust the server. With
    POPPYS_INLINE_PARSE=1 it is parsed in this process instead (tests, debugging). If the operating
    system refuses to start a worker at all, the file is parsed in this process and the failure logged."""
    if len(content) > MAX_BYTES:
        raise Unreadable(MSG_TOO_BIG)
    if _inline_requested():
        stats['inline'] += 1
        return _read_inline(content, filename)
    try:
        result = isolation.run(_worker_read, (bytes(content), str(filename or '')), timeout=PARSE_TIMEOUT,
                               memory=PARSE_MEMORY, passthrough=(Unsupported, Unreadable))
        kind, sheets = _checked(result)
    except isolation.WorkerFailed as exc:
        if exc.reason == 'spawn':
            log.error('Could not start an upload worker (%s); reading %r in the server process.', exc.detail, filename)
            stats['inline_fallback'] += 1
            return _read_inline(content, filename)
        log.warning('Upload worker failed for %r: %s', filename, exc)
        raise Unreadable(WORKER_MESSAGES.get(exc.reason, WORKER_MESSAGES['crashed'])) from None
    stats['isolated'] += 1
    return kind, sheets


def _checked(result):
    """The worker's answer in the shape read() promises, or WorkerFailed (never a raw unpacking error)."""
    if (isinstance(result, list) and len(result) == 2 and result[0] in LABELS and isinstance(result[1], list)
            and all(isinstance(s, dict) and isinstance(s.get('cells'), list) for s in result[1])):
        return result[0], result[1]
    raise isolation.WorkerFailed('error', 'worker answer has the wrong shape')


def _worker_read(content, filename):
    """Runs in the worker process: the same read, returned as JSON-ready data."""
    kind, sheets = _read_inline(content, filename)
    return [kind, sheets]


def _read_inline(content, filename=''):
    if len(content) > MAX_BYTES:
        raise Unreadable(MSG_TOO_BIG)
    kind = sniff(content, filename)
    try:
        try:
            sheets = READERS[kind](bytes(content))
        except _NotJson:
            if not filename.lower().endswith(TEXT_NAMES):
                raise
            kind, sheets = 'csv', read_csv(bytes(content))    # "[PO No],[Style]" in a .csv is a CSV header
    except (Unsupported, Unreadable):
        raise
    except MemoryError:
        raise Unreadable(WORKER_MESSAGES['memory']) from None
    except RecursionError:
        raise Unreadable(f'This {NOUNS[kind]} is nested too deeply to read. Save it again from the program that made it and upload.') from None
    except INTERRUPTS:
        raise
    except BaseException as exc:   # library errors of every kind (zip, compound document, assertions, csv, XML)
        log.warning('%s reader failed: %s: %s', kind, type(exc).__name__, str(exc)[:300])   # and native panics
        raise Unreadable(f'This {NOUNS[kind]} could not be read. Open it in Excel, save it again as an Excel Workbook (.xlsx) and upload.') from exc
    sheets = [s for s in sheets if s['cells'] or not s['hidden']]
    if not sheets:
        raise Unreadable('The workbook has no sheets with data. Check that you uploaded the right file.')
    if len(sheets) > MAX_SHEETS:
        raise Unreadable(f'The workbook has more than {MAX_SHEETS} sheets. Delete the sheets that are not part of the order, save it and upload again.')
    if sum(len(s['cells']) for s in sheets) > MAX_TOTAL_CELLS:
        raise Unreadable(f'The workbook has more than {MAX_TOTAL_CELLS:,} filled cells. Split it into smaller workbooks and upload them one at a time.')
    if _text_size(sheets) > MAX_TEXT:
        raise Unreadable(MSG_TOO_MUCH_TEXT)
    formulas.fill_missing(sheets)
    return kind, sheets


def _text_size(sheets):
    """Characters of text in the cell model. A tiny workbook can make one long shared string (or format
    code) appear in every cell; the server would receive a copy per cell, so the total is bounded."""
    total = 0
    for s in sheets:
        for c in s['cells']:
            value = c['value']
            total += (len(value) if isinstance(value, str) else 0) + len(c['formula'] or '') + len(c.get('fmt') or '')
    return total


# --- zip containers: real sizes before any parser runs --------------------------------------------------
def _inflated_size(view, info, limit):
    """Real size of one zip member, inflating its raw data with a running cap (nothing is kept)."""
    offset = info.header_offset
    header = bytes(view[offset:offset + 30])
    if len(header) < 30 or header[:4] != ZIP:
        raise Unsupported(MSG_DAMAGED)
    name_length, extra_length = struct.unpack('<HH', header[26:30])
    start = offset + 30 + name_length + extra_length
    end = start + info.compress_size
    if end > len(view):
        raise Unsupported(MSG_DAMAGED)
    if info.compress_type == zipfile.ZIP_STORED:
        size = info.compress_size
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        inflater = zlib.decompressobj(-15); size = 0; pos = start
        try:
            while pos < end and not inflater.eof:
                chunk = view[pos:min(pos + 65536, end)]; pos += len(chunk)
                while chunk and not inflater.eof:
                    size += len(inflater.decompress(chunk, 1 << 20))
                    if size > info.file_size:
                        raise Unsupported(MSG_LYING)
                    if size > limit:
                        raise Unreadable(MSG_EXPANDS)
                    chunk = inflater.unconsumed_tail
        except zlib.error as exc:
            raise Unsupported(MSG_DAMAGED) from exc
        if not inflater.eof:
            raise Unsupported(MSG_DAMAGED)
    else:
        raise Unsupported('This workbook uses a compression method Excel never writes, so it was not opened. '
                          'Open it in Excel, save it again and upload.')
    if size > info.file_size:
        raise Unsupported(MSG_LYING)
    if size > limit:
        raise Unreadable(MSG_EXPANDS)
    return size


def _zip_guard(content):
    """{part name: real size} for a zip-based workbook. Declared sizes cannot be trusted:
    python-calamine's zip library (zip 8.6) does not enforce them, so every part is measured by
    inflating it with a cap before openpyxl or calamine sees the file."""
    infos = _zip_infos(content)
    view = memoryview(content); total = 0; sizes = {}
    for info in infos:
        name = _zname(info.filename)
        if name in sizes:
            raise Unsupported(MSG_DAMAGED)              # two parts with the same name
        if info.flag_bits & 0x1:
            raise Unsupported(MSG_PASSWORD)             # zip-level encryption
        size = _inflated_size(view, info, MAX_EXPANDED - total)
        if size > MAX_PART:
            raise Unreadable(MSG_PART)
        total += size; sizes[name] = size
    return sizes


def _zip_part(content, wanted, limit=4096):
    """The first `limit` bytes of one part (by normalised name), or None."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            info = next((i for i in archive.infolist() if _zname(i.filename) == wanted), None)
            if info is None:
                return None
            with archive.open(info) as stream:
                return stream.read(limit)
    except Exception:
        return None


# --- .xlsx .xlsm .xltx .xltm ---------------------------------------------------------------------------
def _column_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _merge_box(ref):
    """(r0, c0, r1, c1) for one merged-range reference as files write it - 'A1:C3', '$A$1:$C$3', 'C3:A1',
    LibreOffice's whole rows '1:3' and whole columns 'A:C' - clamped to Excel's grid; None if unreadable."""
    parts = ref.replace('$', '').strip().upper().split(':')
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2:
        return None
    found = [re.fullmatch(r'([A-Z]{0,3})(\d{0,7})', p) for p in parts]
    if not all(found) or any(not (m.group(1) or m.group(2)) for m in found):
        return None
    (ca, ra), (cb, rb) = found[0].groups(), found[1].groups()
    if bool(ca) != bool(cb) or bool(ra) != bool(rb):
        return None                                          # 'A1:3' mixes the two forms
    c0, c1 = (_column_number(ca), _column_number(cb)) if ca else (1, EXCEL_COLS)
    r0, r1 = (int(ra), int(rb)) if ra else (1, EXCEL_ROWS)
    r0, r1 = sorted((r0, r1)); c0, c1 = sorted((c0, c1))
    if r0 < 1 or c0 < 1 or r0 > EXCEL_ROWS or c0 > EXCEL_COLS:
        return None
    return r0, c0, min(r1, EXCEL_ROWS), min(c1, EXCEL_COLS)


def _merge_block(xml):
    """(start, end, prefix) of a worksheet's <mergeCells> element (any namespace prefix), or None."""
    at = 0
    while True:
        i = xml.find(b'mergeCells', at)
        if i < 0:
            return None
        at = i + 10
        lt = xml.rfind(b'<', max(0, i - 64), i)
        m = re.fullmatch(rb'<((?:[A-Za-z_][\w.\-]*:)?)', xml[lt:i]) if lt >= 0 else None
        if not m:
            continue
        prefix = m.group(1); gt = xml.find(b'>', i)
        if gt < 0:
            return None
        if xml[gt - 1:gt] == b'/':
            return lt, gt + 1, prefix
        close = xml.find(b'</' + prefix + b'mergeCells', gt)
        end = xml.find(b'>', close) if close >= 0 else -1
        return (lt, end + 1, prefix) if end >= 0 else None


def _sanitize_merges(xml):
    """(new sheet XML or None when nothing changes, {'large', 'excess', 'bad'} counts). openpyxl turns every
    cell of a merged range into an object while it loads (A1:XFD1048576 is 17 billion of them), and
    openpyxl and calamine both refuse a whole workbook over one reference such as LibreOffice's '1:1'.
    Ranges are normalised and kept by the same rules as Builder.merge, before either library runs."""
    counts = dict(large=0, excess=0, bad=0)
    block = _merge_block(xml)
    if block is None:
        return None, counts
    start, end, prefix = block
    refs = [m.group(2).decode('ascii', 'replace') for m in re.finditer(rb'\bref\s*=\s*(["\'])([^"\']{0,400})\1', xml[start:end])]
    kept, seen, area_total, changed = [], set(), 0, False
    for ref in refs:
        pieces = ref.split() or ['']                        # 'A1:B2 D1:E2' (a list) is two ranges
        changed = changed or pieces != [ref]
        for one in pieces:
            box = _merge_box(one)
            if box is None:
                counts['bad'] += 1; changed = True; continue
            r0, c0, r1, c1 = box
            area = (r1 - r0 + 1) * (c1 - c0 + 1)
            if area == 1 or box in seen:
                changed = True; continue
            if area > MAX_MERGE_AREA:
                counts['large'] += 1; changed = True; continue
            if len(kept) >= MAX_MERGES or area_total + area > MAX_MERGED_CELLS:
                counts['excess'] += 1; changed = True; continue
            normal = f'{address(c0, r0)}:{address(c1, r1)}'
            changed = changed or normal != one
            kept.append(normal); seen.add(box); area_total += area
    if not changed:
        return None, counts
    p = prefix.decode('ascii')
    new = (f'<{p}mergeCells count="{len(kept)}">' + ''.join(f'<{p}mergeCell ref="{r}"/>' for r in kept) + f'</{p}mergeCells>') if kept else ''
    return xml[:start] + new.encode('ascii') + xml[end:], counts


def _ooxml_sheet_names(archive, names):
    """{normalised worksheet part name: sheet name} from the workbook part and its relationships."""
    from defusedxml.ElementTree import fromstring
    local = lambda tag: tag.rsplit('}', 1)[-1]
    try:
        targets = {el.get('Id'): el.get('Target') or '' for el in fromstring(archive.read(names['xl/_rels/workbook.xml.rels'])).iter()
                   if local(el.tag) == 'Relationship'}
        out = {}
        for el in fromstring(archive.read(names['xl/workbook.xml'])).iter():
            if local(el.tag) == 'sheet':
                rid = next((v for k, v in el.attrib.items() if local(k) == 'id'), None)
                target = targets.get(rid, '').replace('\\', '/')
                if target:
                    path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                    out[_zname(posixpath.normpath(path))] = el.get('name') or ''
        return out
    except Exception:            # the reader itself reports a damaged workbook; this map only places notes
        return {}


def _ooxml_prepare(content):
    """(content for the readers, {sheet name: merge counts}) after checking each worksheet part: absurd
    cell counts are refused before openpyxl spends a minute on them, and merged ranges are made safe."""
    replaced, counts = {}, {}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        infos = archive.infolist()
        names = {_zname(i.filename): i for i in infos}
        sheet_names = None
        for info in infos:
            name = _zname(info.filename)
            if not (name.startswith('xl/worksheets/') and name.endswith('.xml')):
                continue
            xml = archive.read(info)
            if sheet_names is None:
                sheet_names = _ooxml_sheet_names(archive, names)
            label = sheet_names.get(name) or name.rsplit('/', 1)[-1]
            cells = xml.count(b'<c ') + xml.count(b'<c>') + xml.count(b':c ')
            rows = xml.count(b'<row ') + xml.count(b'<row>') + xml.count(b':row ')
            if cells > MAX_CELLS * 4 or rows > MAX_CELLS * 4:     # each becomes an object in openpyxl, twice
                raise Unreadable(f'{label}: sheet is too large; trim unused formatting and upload again.')
            new, found = _sanitize_merges(xml)
            if new is not None:
                replaced[info.filename] = new
            if any(found.values()):
                counts[label[:120]] = found
        if not replaced:
            return content, counts
        out = BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as rebuilt:     # stored: no CPU spent compressing
            for info in infos:
                rebuilt.writestr(info.filename, replaced[info.filename] if info.filename in replaced else archive.read(info))
    return out.getvalue(), counts


def _apply_merge_counts(builder, counts):
    found = counts.get(builder.name) or {}
    builder.dropped_merges += found.get('large', 0); builder.excess_merges += found.get('excess', 0)
    builder.bad_merges += found.get('bad', 0)


def read_ooxml(content):
    _zip_guard(content)
    content, merges = _ooxml_prepare(content)
    head = _zip_part(content, 'xl/workbook.xml') or b''
    if b'purl.oclc.org/ooxml' in head or b'conformance="strict"' in head:
        return _calamine_fallback(content, 'it is saved as Strict Open XML', merges)
    try:
        sheets = _openpyxl_sheets(content, merges)
    except (Unsupported, Unreadable):
        raise
    except Exception as exc:     # openpyxl raises many types for damaged files
        log.info('openpyxl could not read the workbook (%s: %s); trying calamine', type(exc).__name__, str(exc)[:200])
        return _calamine_fallback(content, 'the file is damaged or was written by an unusual program', merges)
    if not sheets:
        return _calamine_fallback(content, 'the main reader found no worksheets', merges)
    return sheets


def _openpyxl_sheets(content, merges=None):
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        raw = load_workbook(BytesIO(content), data_only=False, keep_links=False, keep_vba=False)
        cached = load_workbook(BytesIO(content), data_only=True, keep_links=False, keep_vba=False)
    try:
        if len(raw.worksheets) > MAX_SHEETS:
            raise Unreadable(f'The workbook has more than {MAX_SHEETS} sheets. Delete the sheets that are not part of the order, save it and upload again.')
        sheets = []
        for ws in raw.worksheets:
            # Walk the cells the file stores, not openpyxl's max_row x max_column rectangle: buyer sheets
            # often carry formatting down to row 1,048,576 around a few hundred filled cells.
            stored = getattr(ws, '_cells', None)
            if not isinstance(stored, dict):
                if ws.max_row * ws.max_column > MAX_CELLS * 4:
                    raise Unreadable(f'{ws.title}: sheet is too large; trim unused formatting and upload again.')
                stored = {(c.row, c.column): c for row in ws.iter_rows() for c in row}
            if len(stored) > MAX_CELLS * 4:
                raise Unreadable(f'{ws.title}: sheet is too large; trim unused formatting and upload again.')
            b = Builder(ws.title, ws.sheet_state != 'visible', FULL_CAPS, 'openpyxl')
            _apply_merge_counts(b, merges or {})
            for r, d in ws.row_dimensions.items():
                if d.hidden or d.ht == 0:                # a zero-height row is hidden too
                    b.hidden_rows.add(r)
            for key, d in ws.column_dimensions.items():
                if d.hidden or d.width == 0:             # one entry can cover a range of columns (min..max)
                    first = d.min or column_index_from_string(key)
                    b.hidden_cols.add(first, d.max or first)
            values = cached[ws.title]
            for key in sorted(stored):
                cell = stored[key]
                if cell.value is None:
                    continue
                formula = cell.value if cell.data_type == 'f' else None
                source = values[cell.coordinate] if formula is not None else cell
                if formula is not None:
                    formula = str(getattr(formula, 'text', None) or formula)
                    if source.value is None:             # keep uncalculated formulas visible to the parser
                        b.put_formula(cell.row, cell.column, formula)
                        continue
                value, extras = _formatted(source.value, source.number_format)
                b.put(cell.row, cell.column, value, formula, source.data_type == 'e', **extras)
            if b.cells and max(r for r, _ in b.cells) * max(c for _, c in b.cells) > MAX_CELLS * 4:
                raise Unreadable(f'{ws.title}: sheet is too large; its values are spread over too wide an area. '
                                 'Delete unused rows and columns, save it and upload again.')
            for rng in ws.merged_cells.ranges:
                b.merge(rng.min_row, rng.min_col, rng.max_row, rng.max_col)
            sheets.append(b.sheet())
        return sheets
    finally:
        raw.close(); cached.close()


def _calamine_fallback(content, why, merges=None):
    try:
        return read_calamine(content, note=MSG_BACKUP_READER.format(why=why), merges=merges)
    except Unsupported:
        raise
    except Exception as exc:
        raise Unreadable(MSG_CANNOT_OPEN) from exc


# --- .xls ---------------------------------------------------------------------------------------------
def read_xls(content):
    """xlrd with formats, then xlrd in lenient mode (files from ERP and report tools often have small
    compound-document faults), then without formats, then python-calamine."""
    import xlrd
    failures = []
    for formatting, lenient in ((True, False), (True, True), (False, True)):
        try:
            return _xls_sheets(content, xlrd, formatting, lenient)
        except (Unsupported, Unreadable):
            raise
        except xlrd.XLRDError as exc:
            if 'encrypt' in str(exc).lower():
                raise Unsupported(MSG_XLS_PASSWORD) from exc
            failures.append(f'{type(exc).__name__}: {exc}')
        except Exception as exc:     # CompDocError, AssertionError, struct.error, IndexError ...
            failures.append(f'{type(exc).__name__}: {exc}')
    log.info('xlrd could not read the workbook (%s); trying calamine', '; '.join(f[:120] for f in failures))
    try:
        return read_calamine(content, note=MSG_BACKUP_READER.format(why='the file is damaged or very old'))
    except Unsupported:
        raise
    except Exception as exc:
        raise Unreadable(MSG_XLS_DAMAGED) from exc


def _xls_sheets(content, xlrd, formatting, lenient):
    book = xlrd.open_workbook(file_contents=content, formatting_info=formatting, on_demand=True,
                              logfile=io.StringIO(), ignore_workbook_corruption=lenient)
    try:
        if book.nsheets > MAX_SHEETS:
            raise Unreadable(f'The workbook has more than {MAX_SHEETS} sheets. Delete the sheets that are not part of the order, save it and upload again.')
        caps = dict(merged=formatting, hidden_rows=formatting, hidden_cols=formatting, number_formats=formatting, formulas=False)
        sheets = []
        for index in range(book.nsheets):
            ws = book.sheet_by_index(index)
            b = Builder(ws.name, ws.visibility != 0, caps, 'xlrd')
            if not formatting:
                b.notes.append(MSG_BACKUP_READER.format(why='its formatting records are damaged'))
            for r, info in ws.rowinfo_map.items():
                if info.hidden or getattr(info, 'height', None) == 0:
                    b.hidden_rows.add(r + 1)
            for c, info in ws.colinfo_map.items():
                if info.hidden or getattr(info, 'width', None) == 0:
                    b.hidden_cols.add(c + 1)
            for r in range(ws.nrows):
                for c, kind in enumerate(ws.row_types(r)):
                    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    cell = ws.cell(r, c)
                    value, extras = _xls_value(book, cell, xlrd)
                    b.put(r + 1, c + 1, value, error=cell.ctype == xlrd.XL_CELL_ERROR, **extras)
            for r0, r1, c0, c1 in ws.merged_cells:
                b.merge(r0 + 1, c0 + 1, r1, c1)
            sheets.append(b.sheet())
            book.unload_sheet(index)
        return sheets
    finally:
        book.release_resources()


def _xls_value(book, cell, xlrd):
    t = cell.ctype
    if t == xlrd.XL_CELL_TEXT:
        return cell.value, {}
    if t == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value), {}
    if t == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value, '#ERROR'), {}
    xf = book.xf_list[cell.xf_index] if cell.xf_index is not None and cell.xf_index < len(book.xf_list) else None
    fmt = book.format_map.get(xf.format_key) if xf is not None else None
    code = fmt.format_str if fmt is not None else None
    value = cell.value
    if t == xlrd.XL_CELL_DATE:
        try:
            value = xlrd.xldate_as_datetime(cell.value, book.datemode)
        except (ValueError, OverflowError, TypeError):
            return cell.value, {}
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    return _formatted(value, code)


# --- .xlsb .ods (calamine) ---------------------------------------------------------------------------
INTERRUPTS = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _calamine_builders(content, note=None, merges=None):
    """Builders for every worksheet calamine reads. A panic inside the Rust library (pyo3's
    PanicException derives from BaseException, not Exception) becomes a plain refusal."""
    try:
        return _calamine_sheets(content, note, merges or {})
    except INTERRUPTS:
        raise
    except Exception:
        raise
    except BaseException as exc:
        log.warning('calamine panicked: %s', str(exc)[:200])
        raise Unreadable(MSG_CANNOT_OPEN) from exc


def _calamine_sheets(content, note, merge_counts):
    import python_calamine as pc
    try:
        book = pc.CalamineWorkbook.from_filelike(BytesIO(content))
    except pc.PasswordError as exc:
        raise Unsupported(MSG_PASSWORD) from exc
    except Exception as exc:
        raise Unreadable(MSG_CANNOT_OPEN) from exc
    builders = []
    try:
        for meta in book.sheets_metadata:
            if meta.typ != pc.SheetTypeEnum.WorkSheet:
                continue                              # chart, dialog and macro sheets hold no order cells
            if len(builders) > MAX_SHEETS:
                break                                 # read() reports "more than 30 sheets"
            ws = book.get_sheet_by_name(meta.name)
            if (ws.total_height or 0) * (ws.total_width or 0) > MAX_CELLS * 8:
                raise Unreadable(f'{meta.name}: sheet is too large; trim it and upload again.')
            merges = ws.merged_cell_ranges            # None where calamine cannot read merges (xlsb, ods)
            b = Builder(meta.name, meta.visible != pc.SheetVisibleEnum.Visible,
                        dict(VALUES_ONLY, merged=merges is not None), 'calamine')
            if note:
                b.notes.append(note)
            _apply_merge_counts(b, merge_counts)
            for r, row in enumerate(ws.to_python(skip_empty_area=False), 1):
                for c, value in enumerate(row, 1):
                    if value is not None and value != '':
                        b.put(r, c, value)
            for rng in merges or []:
                try:
                    (r0, c0), (r1, c1) = rng
                    b.merge(r0 + 1, c0 + 1, r1 + 1, c1 + 1)
                except (TypeError, ValueError):
                    continue
            builders.append(b)
    finally:
        book.close()
    return builders


def read_calamine(content, note=None, merges=None):
    return [b.sheet() for b in _calamine_builders(content, note, merges)]


def read_zip_calamine(content):
    _zip_guard(content)
    return read_calamine(content)


def read_ods(content):
    """calamine for values; a capped pass over styles.xml and content.xml for merges, hidden rows and
    columns and digits-only number styles (codes such as 0008), which calamine does not give for ODS."""
    _zip_guard(content)
    builders = _calamine_builders(content)
    try:
        layout = _ods_layout(content)
    except (Unsupported, Unreadable):
        raise
    except Exception as exc:          # values are already read; the layout is a bonus
        log.info('ODS layout pass failed (%s: %s)', type(exc).__name__, str(exc)[:200])
        return [b.sheet() for b in builders]
    for b in builders:
        b.capabilities.update(merged=True, hidden_rows=True, hidden_cols=True, number_formats=True)
        b.reader = 'calamine+layout'
        info = layout.get(b.name)
        if not info:
            continue
        for r0, c0, r1, c1 in info['merges']:
            b.merge(r0, c0, r1, c1)
        for first, last in info['rows']:
            b.hidden_rows.add(first, last)
        for first, last in info['cols']:
            b.hidden_cols.add(first, last)
        for key, code in info['formats'].items():
            cell = b.cells.get(key)
            if cell is not None and isinstance(cell['value'], (int, float)) and not isinstance(cell['value'], bool):
                value, extras = _formatted(cell['value'], code)
                cell['value'] = value; cell.update(extras)
    return [b.sheet() for b in builders]


def _repeat(value, limit):
    try:
        return max(1, min(int(value), limit))
    except (TypeError, ValueError):
        return 1


ODF_TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
ODF_STYLE = '{urn:oasis:names:tc:opendocument:xmlns:style:1.0}'
ODF_NUMBER = '{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}'
ODF_OFFICE = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
MAX_STYLES = 10_000


def _ods_code(style):
    """An Excel-style format code for a digits-only ODS number style (min-integer-digits, embedded
    and leading/trailing text), or None for anything else (decimals, grouping, conditions, ...)."""
    parts, numbers = [], 0
    for child in style:
        if child.tag == ODF_NUMBER + 'text':
            text = ''.join(child.itertext()).replace('"', '')
            if text:
                parts.append(f'"{text}"')
        elif child.tag == ODF_NUMBER + 'number':
            if child.get(ODF_NUMBER + 'grouping') == 'true' or (child.get(ODF_NUMBER + 'decimal-places') or '0') != '0':
                return None
            numbers += 1
            digits = _repeat(child.get(ODF_NUMBER + 'min-integer-digits'), 30)
            texts = {}
            for embedded in child.findall(ODF_NUMBER + 'embedded-text'):
                position = _repeat(embedded.get(ODF_NUMBER + 'position'), 30)
                texts[min(position, digits)] = ''.join(embedded.itertext()).replace('"', '')
            body = [f'"{texts[digits]}"'] if texts.get(digits) else []
            for i in range(digits):
                body.append('0')
                right = digits - i - 1
                if right and texts.get(right):
                    body.append(f'"{texts[right]}"')
            parts.append(''.join(body))
        elif child.tag in (ODF_NUMBER + 'scientific-number', ODF_NUMBER + 'fraction', ODF_STYLE + 'map'):
            return None
    return ''.join(parts) if numbers == 1 else None


def _ods_style_event(el, numbers, cells):
    """Record a finished number style or table-cell style element; True when it was one."""
    if el.tag == ODF_NUMBER + 'number-style' and len(numbers) < MAX_STYLES:
        numbers[el.get(ODF_STYLE + 'name')] = _ods_code(el)
    elif el.tag == ODF_STYLE + 'style' and el.get(ODF_STYLE + 'family') == 'table-cell' and len(cells) < MAX_STYLES:
        cells[el.get(ODF_STYLE + 'name')] = (el.get(ODF_STYLE + 'data-style-name'), el.get(ODF_STYLE + 'parent-style-name'))
    else:
        return False
    return True


def _ods_layout(content):
    """{sheet name: {'merges': [(r0, c0, r1, c1)], 'rows': [(first, last)], 'cols': [(first, last)],
    'formats': {(row, col): code}}} from streaming defusedxml passes; repeated rows and columns are
    counted, never expanded."""
    from defusedxml.ElementTree import iterparse
    T = ODF_TABLE
    TABLE, COLUMN, ROW, CELL, COVERED = T + 'table', T + 'table-column', T + 'table-row', T + 'table-cell', T + 'covered-table-cell'
    HIDDEN = ('collapse', 'filter')
    numbers, cell_styles, resolved = {}, {}, {}

    def code_for(style):
        """The digits-only code of a cell style, following parent styles to the first data style."""
        if style not in resolved:
            code, name, seen = None, style, set()
            while name and name not in seen and len(seen) < 20:
                seen.add(name)
                data_style, parent = cell_styles.get(name, (None, None))
                if data_style:
                    code = numbers.get(data_style)
                    break
                name = parent
            resolved[style] = code
        return resolved[style]

    layouts = {}; current = None; depth = 0; row = 0; repeat_rows = 1; row_style = None; col = 0; colpos = 0; stack = []
    with zipfile.ZipFile(BytesIO(content)) as archive:
        members = {_zname(i.filename): i for i in archive.infolist()}
        if 'styles.xml' in members:
            with archive.open(members['styles.xml']) as stream:
                for _, el in iterparse(stream, events=('end',), forbid_dtd=True):
                    if _ods_style_event(el, numbers, cell_styles):
                        el.clear()
        if 'content.xml' not in members:
            return {}
        with archive.open(members['content.xml']) as stream:
            for event, el in iterparse(stream, events=('start', 'end'), forbid_dtd=True):
                tag = el.tag
                if event == 'start':
                    stack.append(el)
                    if tag == TABLE:
                        depth += 1
                        if depth == 1:
                            name = (el.get(T + 'name') or 'Sheet1')[:120]
                            current = layouts.setdefault(name, {'merges': [], 'rows': [], 'cols': [], 'formats': {}, 'colstyles': [], 'colstarts': []})
                            row = 0; colpos = 0
                        continue
                    if current is None or depth != 1:
                        continue
                    if tag == COLUMN:
                        n = _repeat(el.get(T + 'number-columns-repeated'), EXCEL_COLS)
                        if el.get(T + 'visibility') in HIDDEN and colpos < EXCEL_COLS:
                            current['cols'].append((colpos + 1, min(colpos + n, EXCEL_COLS)))
                        if el.get(T + 'default-cell-style-name') and len(current['colstyles']) < MAX_STYLES and colpos < EXCEL_COLS:
                            current['colstarts'].append(colpos + 1)       # increasing: columns come in order
                            current['colstyles'].append((min(colpos + n, EXCEL_COLS), el.get(T + 'default-cell-style-name')))
                        colpos = min(colpos + n, EXCEL_COLS)
                    elif tag == ROW:
                        repeat_rows = _repeat(el.get(T + 'number-rows-repeated'), EXCEL_ROWS); col = 0
                        row_style = el.get(T + 'default-cell-style-name')
                        if el.get(T + 'visibility') in HIDDEN and row < EXCEL_ROWS:
                            current['rows'].append((row + 1, min(row + repeat_rows, EXCEL_ROWS)))
                    elif tag in (CELL, COVERED):
                        n = _repeat(el.get(T + 'number-columns-repeated'), EXCEL_COLS)
                        if tag == CELL and row < EXCEL_ROWS and col < EXCEL_COLS:
                            # A repeated row repeats its cells too (calamine expands their values), so its
                            # merges and number formats apply to every repetition, within the caps.
                            repeats = min(repeat_rows, EXCEL_ROWS - row)
                            across = _repeat(el.get(T + 'number-columns-spanned'), EXCEL_COLS)
                            down = _repeat(el.get(T + 'number-rows-spanned'), EXCEL_ROWS)
                            if across > 1 or down > 1:
                                for k in range(min(repeats, MAX_MERGES * 2 - len(current['merges']))):
                                    first = row + 1 + k
                                    current['merges'].append((first, col + 1, min(first + down - 1, EXCEL_ROWS), min(col + across, EXCEL_COLS)))
                            if el.get(ODF_OFFICE + 'value-type') == 'float' and len(current['formats']) < MAX_CELLS:
                                style = el.get(T + 'style-name') or row_style
                                if not style:
                                    i = bisect.bisect_right(current['colstarts'], col + 1) - 1
                                    if i >= 0 and col + 1 <= current['colstyles'][i][0]:
                                        style = current['colstyles'][i][1]
                                code = code_for(style) if style else None
                                if code:
                                    budget = MAX_CELLS - len(current['formats'])
                                    for r_k in range(min(repeats, budget)):
                                        for k in range(min(n, EXCEL_COLS - col, MAX_CELLS - len(current['formats']))):
                                            current['formats'][(row + 1 + r_k, col + 1 + k)] = code
                        col = min(col + n, EXCEL_COLS)
                else:
                    stack.pop()
                    if tag == TABLE:
                        depth -= 1
                        if depth == 0:
                            current = None
                    elif tag == ROW and depth == 1 and current is not None:
                        row = min(row + repeat_rows, EXCEL_ROWS)
                    elif depth == 0 and _ods_style_event(el, numbers, cell_styles):
                        resolved.clear()                # automatic styles come before the tables
                    if tag in (TABLE, ROW):
                        el.clear()
                        if stack:
                            stack[-1].remove(el)       # free finished rows as we go
    return layouts


# --- Excel 2003 XML Spreadsheet ---------------------------------------------------------------------
XML_CAPS = dict(FULL_CAPS)


def _parse_xml(content):
    from defusedxml import ElementTree as ET
    from defusedxml import DefusedXmlException
    try:
        return ET.fromstring(content)
    except DefusedXmlException as exc:
        raise Unreadable('This XML file declares entities or a document type (a feature used by malicious files), so it '
                         'was not read. Save it again from Excel as "XML Spreadsheet 2003" or .xlsx and upload.') from exc
    except ET.ParseError:
        pass
    # Expat reads UTF-8/UTF-16 with a BOM or declaration; anything else is decoded here and re-encoded.
    text = re.sub(r'^(\s*<\?xml[^>]*?)\s+encoding\s*=\s*["\'][^"\']*["\']', r'\1', _decode(content), count=1)
    try:
        return ET.fromstring(text.encode('utf-8'))
    except DefusedXmlException as exc:
        raise Unreadable('This XML file declares entities or a document type (a feature used by malicious files), so it '
                         'was not read. Save it again from Excel as "XML Spreadsheet 2003" or .xlsx and upload.') from exc
    except ET.ParseError as exc:
        raise Unreadable('This Excel 2003 XML file is damaged and could not be read. Open it in Excel, save it as .xlsx and upload.') from exc


def _index(value, current):
    """ss:Index: a 1-based position; a missing, garbage or non-positive value means the next position."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return current + 1
    return n if n >= 1 else current + 1


def _count(value, limit):
    """ss:Span / ss:MergeAcross / ss:MergeDown: a non-negative count, clamped to the grid."""
    try:
        return max(0, min(int(value), limit))
    except (TypeError, ValueError):
        return 0


def read_xml2003(content):
    root = _parse_xml(content)
    ss = '{%s}' % SS_NS; x = '{urn:schemas-microsoft-com:office:excel}'
    if root.tag != ss + 'Workbook':
        raise Unsupported('This XML file is not an Excel spreadsheet. Upload the buyer file as .xlsx, .xls, .csv or .json.')
    styles = _xml_styles(root, ss)
    sheets = []
    for ws in root.iter(ss + 'Worksheet'):
        visible = ws.find(f'{x}WorksheetOptions/{x}Visible')
        b = Builder(ws.get(ss + 'Name') or f'Sheet{len(sheets) + 1}', visible is not None and 'hidden' in (visible.text or '').lower(),
                    XML_CAPS, 'xml2003')
        table = ws.find(ss + 'Table')
        if table is None:
            sheets.append(b.sheet()); continue
        column_styles = []; col = 0; outside = False
        for column in table.findall(ss + 'Column'):
            col = _index(column.get(ss + 'Index'), col)
            if col > EXCEL_COLS:
                break
            span = _count(column.get(ss + 'Span'), EXCEL_COLS - col)
            if column.get(ss + 'Hidden') == '1' or column.get(ss + 'Width') in ('0', '0.0'):
                b.hidden_cols.add(col, col + span)
            if column.get(ss + 'StyleID') and len(column_styles) < MAX_STYLES:
                column_styles.append((col, col + span, column.get(ss + 'StyleID')))
            col += span
        column_styles.sort(key=lambda s: s[0])             # looked up by bisection: one step per cell
        column_starts = [s[0] for s in column_styles]

        def column_style(c, column_starts=column_starts, column_styles=column_styles):
            i = bisect.bisect_right(column_starts, c) - 1
            return column_styles[i][2] if i >= 0 and c <= column_styles[i][1] else None
        r = 0
        for row in table.findall(ss + 'Row'):
            r = _index(row.get(ss + 'Index'), r)
            if r > EXCEL_ROWS:
                outside = True
                break
            span = _count(row.get(ss + 'Span'), EXCEL_ROWS - r)     # this row repeats `span` more times
            if row.get(ss + 'Hidden') == '1':
                b.hidden_rows.add(r, r + span)
            row_style = row.get(ss + 'StyleID')
            c = 0
            for cell in row.findall(ss + 'Cell'):
                c = _index(cell.get(ss + 'Index'), c)
                if c > EXCEL_COLS:
                    outside = True
                    break
                across = _count(cell.get(ss + 'MergeAcross'), EXCEL_COLS - c)
                down = _count(cell.get(ss + 'MergeDown'), EXCEL_ROWS - r)
                data = cell.find(ss + 'Data')
                if data is not None:
                    text = ''.join(data.itertext()); kind = data.get(ss + 'Type', 'String')
                    extras = {}
                    if kind == 'Number':
                        value = typed(text) if NUMBER.match(text.strip() or 'x') else _float(text)
                        style = cell.get(ss + 'StyleID') or row_style or column_style(c) or 'Default'
                        value, extras = _formatted(value, styles.get(style))
                    elif kind == 'DateTime':
                        value = _iso(text)
                    elif kind == 'Boolean':
                        value = text.strip() in ('1', 'true', 'TRUE')
                    else:
                        value = text
                    b.put(r, c, value, cell.get(ss + 'Formula'), kind == 'Error', **extras)
                if across or down:
                    b.merge(r, c, r + down, c + across)
                c += across
            r += span
        if outside:
            b.notes.append(f'Cells placed beyond Excel\'s last row ({EXCEL_ROWS:,}) or column ({EXCEL_COLS:,}) were ignored.')
        sheets.append(b.sheet())
    if not sheets:
        raise Unsupported('This XML file is not an Excel spreadsheet. Upload the buyer file as .xlsx, .xls, .csv or .json.')
    return sheets


def _xml_styles(root, ss):
    """{style id: number-format code}, following ss:Parent inheritance."""
    found = {}
    for style in root.iter(ss + 'Style'):
        sid = style.get(ss + 'ID')
        if not sid:
            continue
        nf = style.find(ss + 'NumberFormat')
        found[sid] = (nf.get(ss + 'Format') if nf is not None else None, style.get(ss + 'Parent'))
    resolved = {}
    for sid in found:
        code, parent, seen = found[sid][0], found[sid][1], {sid}
        while code is None and parent in found and parent not in seen and len(seen) < 20:
            seen.add(parent); code, parent = found[parent]
        resolved[sid] = code
    return resolved


def _float(text):
    try:
        v = float(text)
        if not math.isfinite(v):
            return text
        return int(v) if v.is_integer() else v
    except (TypeError, ValueError):
        return text


def _iso(text):
    try:
        return datetime.fromisoformat(text.strip()[:19])
    except ValueError:
        return text


# --- HTML tables saved as .xls / .htm, and MHTML single-file pages ----------------------------------
HTML_CAPS = dict(merged=True, hidden_rows=True, hidden_cols=True, number_formats=True, formulas=True)


def _css_unescape(text):
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and i + 1 < len(text):
            m = re.match(r'[0-9a-fA-F]{1,6}\s?', text[i + 1:i + 8])
            if m:
                try:
                    out.append(chr(int(m.group(0).strip(), 16)))
                except (ValueError, OverflowError):
                    pass
                i += 1 + len(m.group(0)); continue
            out.append(text[i + 1]); i += 2; continue
        out.append(ch); i += 1
    return ''.join(out)


def _css_number_format(style):
    """The mso-number-format code in a CSS declaration block, or None."""
    if not style:
        return None
    at = style.lower().find('mso-number-format')
    if at < 0:
        return None
    rest = style[at + len('mso-number-format'):].lstrip()
    if not rest.startswith(':'):
        return None
    rest = rest[1:].lstrip()
    if rest[:1] in ('"', "'"):
        quote, i, value = rest[0], 1, []
        while i < len(rest) and rest[i] != quote:
            if rest[i] == '\\' and i + 1 < len(rest):
                value.append(rest[i:i + 2]); i += 2; continue
            value.append(rest[i]); i += 1
        code = ''.join(value)
    else:
        code = rest.split(';', 1)[0].strip()
    code = _css_unescape(code).strip()
    return code or None


def _css_hidden(style):
    return bool(style) and 'display:none' in style.replace(' ', '').lower()


def _css_rules(css):
    """{class name: {'fmt': code, 'hidden': bool}} from a style sheet. Linear-time: no backtracking regex."""
    css = css[:1_000_000]
    text, i = [], 0
    while True:                                   # drop /* comments */
        start = css.find('/*', i)
        if start < 0:
            text.append(css[i:]); break
        text.append(css[i:start])
        end = css.find('*/', start + 2)
        if end < 0:
            break
        i = end + 2
    rules = {}
    for chunk in ''.join(text).split('}'):
        selectors, _, body = chunk.partition('{')
        if not body:
            continue
        fmt, hidden = _css_number_format(body), _css_hidden(body)
        if fmt is None and not hidden:
            continue
        for selector in selectors.replace('<!--', ' ').split(','):
            m = re.fullmatch(r'\s*(?:[A-Za-z0-9]*)\.([\w-]+)\s*', selector)
            if m and len(rules) < 10_000:
                rules[m.group(1)] = {'fmt': fmt, 'hidden': hidden}
    return rules


class _Tables(HTMLParser):
    """Lays HTML out the way Excel does when it opens an HTML 'xls': text blocks and tables go down
    column A onwards in document order; colspan/rowspan become merged cells; display:none rows and
    columns are hidden; mso-number-format "\\@" (Text) and x:str cells keep their text."""
    BLOCK = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'caption', 'title'}

    def __init__(self, name='Sheet1', css=''):
        super().__init__(convert_charrefs=True)
        self.b = Builder(name, capabilities=HTML_CAPS, reader='html'); self.row = 0; self.skip = 0; self.depth = 0
        self.text = []; self.cell = None; self.taken = set(); self.r = 0; self.c = 0; self.colpos = 0
        self.spanned = 0; self.style = None; self.classes = _css_rules(css) if css else {}

    def _class_rule(self, attrs, key):
        for name in (attrs.get('class') or '').split():
            value = self.classes.get(name, {}).get(key)
            if value:
                return value
        return None

    def _flush_text(self):
        t = ' '.join(' '.join(self.text).split())
        if t and self.depth == 0:
            self.row += 1; self.b.put(self.row, 1, t)
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'style':
            self.style = []; self.skip += 1; return
        if tag in ('script', 'head'):
            self.skip += 1; return
        if tag == 'table':
            if self.depth == 0:
                self._flush_text()
                if self.row:
                    self.row += 1          # one empty row between blocks, as Excel shows it
                self.r = self.row; self.colpos = 0
            self.depth += 1
        elif self.depth == 1 and tag == 'col':
            span = max(1, min(_int(a.get('span')), EXCEL_COLS))
            if _css_hidden(a.get('style')) or self._class_rule(a, 'hidden') or (a.get('width') or '').strip() == '0':
                self.b.hidden_cols.add(self.colpos + 1, self.colpos + span)
            self.colpos += span
        elif self.depth == 1 and tag == 'tr':
            self.r += 1; self.c = 0
            if _css_hidden(a.get('style')) or self._class_rule(a, 'hidden') or (a.get('height') or '').strip() == '0':
                self.b.hidden_rows.add(self.r)
        elif self.depth == 1 and tag in ('td', 'th'):
            self.c += 1
            while (self.r, self.c) in self.taken:
                self.c += 1
            span_c = max(1, min(_int(a.get('colspan')), 200)); span_r = max(1, min(_int(a.get('rowspan')), 500))
            if span_c * span_r > MAX_MERGE_AREA:
                span_r = max(1, MAX_MERGE_AREA // span_c)
            fmt = _css_number_format(a.get('style')) or self._class_rule(a, 'fmt')
            self.cell = dict(row=self.r, col=self.c, span_c=span_c, span_r=span_r, text=[], fmt=fmt,
                             textual='x:str' in a or numfmt.is_text_format(fmt), formula=a.get('x:fmla') or None,
                             num=a.get('x:num') if a.get('x:num') not in (None, '') else a.get('sdval'))
        elif tag == 'br' and self.cell is not None:
            self.cell['text'].append(' ')
        elif tag in self.BLOCK and self.depth == 0:
            self._flush_text()

    def handle_endtag(self, tag):
        if tag == 'style' and self.style is not None:
            self.classes.update(_css_rules(''.join(self.style))); self.style = None
            self.skip = max(0, self.skip - 1); return
        if tag in ('script', 'head'):
            self.skip = max(0, self.skip - 1); return
        if tag in ('td', 'th') and self.depth == 1 and self.cell is not None:
            cell = self.cell; self.cell = None
            text = ' '.join(''.join(cell['text']).split())
            extras = {'fmt': cell['fmt']} if cell['fmt'] and cell['fmt'] != 'General' else {}
            if cell['textual']:
                value = text                             # Excel showed it as text: keep it exactly
            else:
                # Excel/LibreOffice store the raw number beside the shown text (x:num / sdval). Trust it only
                # when the shown text is itself a number: for dates the raw value is a serial day number.
                shown = re.sub(r'[\s,%₹$€£]', '', text)
                numeric = bool(re.fullmatch(r'[-+]?\d+(\.\d+)?', shown))
                if cell['num'] not in (None, '') and numeric:
                    if re.fullmatch(r'0\d+', text):     # a code shown with leading zeros (0008): keep the text
                        value = text; extras['raw'] = clean(_float(cell['num']))
                    else:
                        value = _float(cell['num'])
                else:
                    value, flags = _text_value(text); extras.update(flags)
            self.b.put(cell['row'], cell['col'], value, cell['formula'], **extras)
            if cell['span_r'] > 1:
                self.spanned += cell['span_c'] * (cell['span_r'] - 1)
                if self.spanned > MAX_SPAN_CELLS:
                    raise Unreadable('This web-page export has too many merged cells to read. Open it in Excel, save it as .xlsx and upload that.')
                for rr in range(cell['row'] + 1, cell['row'] + cell['span_r']):
                    for cc in range(cell['col'], cell['col'] + cell['span_c']):
                        self.taken.add((rr, cc))
            if cell['span_c'] > 1 or cell['span_r'] > 1:
                self.b.merge(cell['row'], cell['col'], cell['row'] + cell['span_r'] - 1, cell['col'] + cell['span_c'] - 1)
            self.c = cell['col'] + cell['span_c'] - 1
        elif tag == 'table' and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.row = max(self.r, max((r for r, _ in self.taken), default=self.r)); self.taken = set()
        elif tag in self.BLOCK and self.depth == 0:
            self._flush_text()

    def handle_data(self, data):
        if self.style is not None:
            if sum(map(len, self.style)) < 1_000_000:
                self.style.append(data)
            return
        if self.skip:
            return
        if self.cell is not None:
            self.cell['text'].append(data)
        elif self.depth == 0:
            self.text.append(data)


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 1


def _meta_charset(content):
    m = re.search(rb'<meta[^>]{0,500}?charset\s*=\s*["\']?\s*([\w.:-]{1,40})', content[:4096], re.I)
    return m.group(1).decode('ascii', 'replace') if m else None


def _decode_html(content, declared=None):
    """BOM first, then the transport charset (MHTML part), then <meta charset>, then detection."""
    return _decode(content, declared if _text_codec(declared) else _meta_charset(content))


def _html_sheet(text, name='Sheet1', css=''):
    parser = _Tables(name, css)
    parser.feed(text); parser.close(); parser._flush_text()
    return parser.b.sheet()


def _own_sheet_name(text):
    """The sheet name in a page that is one Excel worksheet (its <x:ExcelWorksheet> block names no other page)."""
    head = text[:200_000]
    lower = head.lower()
    if lower.count('<x:excelworksheet>') != 1 or 'worksheetsource' in lower:
        return None
    m = re.search(r'<x:Name>([^<]{1,200})</x:Name>', head, re.I)
    if not m:
        return None
    return unescape(m.group(1)).strip() or None


def read_html(content):
    text = _decode_html(content)
    sheet = _html_sheet(text, _own_sheet_name(text) or 'Sheet1')
    if not sheet['cells']:
        raise Unsupported('This HTML file has no table data. Upload the buyer file as .xlsx, .xls, .csv or .json.')
    return [sheet]


def _mhtml_sheet_names(pages):
    """{page file name: sheet name} from Excel's <x:ExcelWorksheets> list in the frameset page."""
    names = {}
    for _, text in pages:
        lower = text.lower(); at = 0
        while len(names) <= MAX_SHEETS * 2:
            start = lower.find('<x:excelworksheet>', at)
            if start < 0:
                break
            end = lower.find('</x:excelworksheet>', start)
            if end < 0:
                break
            block = text[start:end]; at = end
            name = re.search(r'<x:Name>([^<]{1,200})</x:Name>', block, re.I)
            href = re.search(r'<x:WorksheetSource\s+HRef\s*=\s*["\']?([^"\'\s>]{1,500})', block, re.I)
            if name and href:
                names[href.group(1).replace('\\', '/').rsplit('/', 1)[-1].lower()] = unescape(name.group(1)).strip()
    return names


def read_mhtml(content):
    """MHTML ("single file web page", what some ERPs and Excel itself write as .mht or .xls): one sheet
    per HTML part that holds a table, named from Excel's sheet list when the page has one."""
    import email
    from email import policy
    message = email.message_from_bytes(content, policy=policy.compat32)
    pages, css, attachments = [], [], 0
    for part in list(message.walk())[:MAX_SHEETS * 4 + 20]:
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype not in ('text/html', 'text/css'):
            attachments += part.get_filename() is not None or ctype.startswith('application/')
            continue
        data = part.get_payload(decode=True) or b''
        if ctype == 'text/css':
            css.append(_decode(data, part.get_content_charset()))
        else:
            location = (part.get('Content-Location') or '').strip()
            pages.append((location.replace('\\', '/').rsplit('/', 1)[-1].lower(), _decode_html(data, part.get_content_charset())))
    names = _mhtml_sheet_names(pages)
    sheets = []
    for key, text in pages:
        if names and key not in names:
            continue                            # Excel's frameset and tab-strip pages hold no order data
        if not re.search(r'<\s*table[\s>]', text, re.I):
            continue
        sheet = _html_sheet(text, names.get(key) or _own_sheet_name(text) or f'Sheet{len(sheets) + 1}', '\n'.join(css))
        if sheet['cells']:
            sheets.append(sheet)
    if not sheets:
        raise Unsupported(MSG_EMAIL if attachments else 'This web page has no table data. Upload the buyer file as .xlsx, .xls, .csv or .json.')
    return sheets


# --- CSV / TSV / Unicode text ------------------------------------------------------------------------
DELIMITERS = (',', ';', '\t', '|')
EXCEL_TEXT = re.compile(r'^="((?:[^"]|"")*)"$')        # ="00123": Excel's way to keep a code as text
DATE_LIKE = re.compile(r'^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$')


def _sep_line(text):
    """Excel's optional first line 'sep=;' names the delimiter; it is not data."""
    first, newline, rest = text.partition('\n')
    m = re.fullmatch(r'\ufeff?"?sep=(.)"?\r?', first, re.I)
    return (rest, m.group(1)) if m else (text, None)


def _looks_typed(value, delimiter):
    v = value.strip()
    if not v or DATE_LIKE.match(v) or DECIMAL_COMMA.match(v) or not isinstance(typed(v), str):
        return True
    return not any(d in v for d in DELIMITERS if d != delimiter)


def _delimiter(sample):
    """CleverCSV-style choice: for each candidate, (share of rows with the most common width) x (share
    of cells that look like a value rather than a fragment holding another delimiter)."""
    sample = sample[:sample.rfind('\n') + 1] or sample
    best = (0.0, 0, ',')
    for d in DELIMITERS:
        try:
            rows = [r for r in csv.reader(StringIO(sample), delimiter=d) if any(x.strip() for x in r)][:200]
        except csv.Error:
            continue
        widths = Counter(len(r) for r in rows if len(r) >= 2)     # title and ruler lines have one value
        if not widths:
            continue
        width, count = max(widths.items(), key=lambda kv: (kv[1], kv[0]))
        cells = [x for r in rows for x in r]
        score = (count / len(rows)) * (sum(_looks_typed(x, d) for x in cells) / len(cells))
        if score > best[0] + 1e-9 or (abs(score - best[0]) <= 1e-9 and width > best[1]):
            best = (score, width, d)
    return best[2]


# Files written with a decimal comma (Europe: 12,50) group thousands with dots, so 1.500 there is fifteen
# hundred, not one and a half; 1,500 may be one and a half. Such values are kept as text and flagged.
EU_DECIMAL = re.compile(r'^[-+]?(\d+,\d{1,2}|\d{1,3}(\.\d{3})+,\d+)$')
POINT_DECIMAL = re.compile(r'^[-+]?(\d+\.\d{1,2}|\d{1,3}(,\d{3})+\.\d+|\d{1,2}(,\d{2})+,\d{3}\.\d+)$')
EU_AMBIGUOUS = re.compile(r'^[-+]?(\d{1,3}(\.\d{3})+|\d{1,3},\d{3})$')


def _uses_decimal_comma(values, delimiter):
    """True when more values are written with a decimal comma (12,50) than with a decimal point (12.50);
    a tie goes to semicolon-separated files, the decimal-comma countries' CSV."""
    comma = point = 0
    for raw in values:
        v = raw.strip()
        comma += bool(EU_DECIMAL.match(v)); point += bool(POINT_DECIMAL.match(v))
    return comma > point or (comma == point and delimiter == ';')


def _csv_value(raw, decimal_comma=False):
    m = EXCEL_TEXT.match(raw.strip())
    if m:
        return m.group(1).replace('""', '"'), {}
    if decimal_comma and EU_AMBIGUOUS.match(raw.strip()):
        return clean(raw), {'flag': 'decimal_comma'}
    return _text_value(raw)


def read_csv(content):
    text, delimiter = _sep_line(_decode(content))
    delimiter = delimiter or _delimiter(text[:20000])
    b = Builder('Sheet1', capabilities=TEXT_CAPS, reader='csv')
    reader = csv.reader(StringIO(text), delimiter=delimiter)
    found, filled = [], 0
    try:
        for r, row in enumerate(reader, 1):
            if r > MAX_CELLS:
                raise Unreadable('The text file has too many rows. Split it into smaller files and upload them one at a time.')
            for c, raw in enumerate(row, 1):
                if raw and not raw.isspace():         # ERP exports pad rows with thousands of empty fields
                    filled += 1
                    if filled > MAX_CELLS:
                        raise Unreadable(f'Sheet1: sheet exceeds {MAX_CELLS:,} filled cells; trim it and upload again.')
                    found.append((r, c, raw))
    except csv.Error as exc:
        limit = 'larger than field limit' in str(exc)
        raise Unreadable(f'Line {reader.line_num} of this text file could not be read'
                         + (' because one value is longer than any spreadsheet cell, usually because a quotation mark is not closed. '
                            if limit else f' ({str(exc)[:80]}). ')
                         + 'Open the file in Excel, check that line, save it again as CSV and upload.') from exc
    decimal_comma = _uses_decimal_comma((raw for _, _, raw in found), delimiter)
    for r, c, raw in found:
        value, extras = _csv_value(raw, decimal_comma)
        b.put(r, c, value, **extras)
    return [b.sheet()]


# --- JSON ---------------------------------------------------------------------------------------------
WRAPPER_KEYS = ('data', 'rows', 'records', 'items', 'results')
META_KEYS = {'count', 'total', 'total_count', 'page', 'pages', 'page_size', 'per_page', 'limit', 'offset', 'next',
             'previous', 'prev', 'status', 'success', 'ok', 'message', 'error', 'errors', 'meta', 'links', 'version',
             'generated', 'generated_at', 'timestamp', 'columns', 'fields', 'schema', 'index'}
JSON_CAPS = dict(merged=None, hidden_rows=None, hidden_cols=None, number_formats=None, formulas=None)


class _NotJson(Unreadable):
    """The text is not JSON at all (a .csv whose first value starts with '[' is retried as CSV)."""


def _no_constants(name):
    raise Unreadable(f'This JSON file contains {name}, which is not a number a spreadsheet can hold. Replace it with an '
                     'empty value or a number and upload again.')


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Unreadable(f'This JSON file has the key "{str(key)[:40]}" twice in one record, so it is unclear which '
                             'value is meant. Remove the duplicate and upload again.')
        out[key] = value
    return out


def _json_loads(text):
    return json.loads(text, parse_float=Decimal, parse_constant=_no_constants, object_pairs_hook=_no_duplicates)


def _ndjson(text):
    """Records from newline-delimited JSON (one object per line), or None."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > MAX_CELLS:
        return None
    records = []
    for line in lines:
        try:
            item = _json_loads(line)
        except Unreadable:
            raise
        except ValueError:
            return None
        if not isinstance(item, dict):
            return None
        records.append(item)
    return records


def _unwrap(data):
    """{"data": [...]}, {"rows": [...]}, {"records": [...]} API envelopes -> the records inside."""
    if not isinstance(data, dict):
        return data
    lists = [k for k, v in data.items() if str(k).lower() in WRAPPER_KEYS and isinstance(v, list) and v]
    if len(lists) != 1 or any(str(k).lower() not in META_KEYS for k in data if k != lists[0]):
        return data
    rows = data[lists[0]]
    columns = next((data[k] for k in data if str(k).lower() in ('columns', 'fields') and isinstance(data[k], list)), None)
    if columns and all(isinstance(r, list) for r in rows):
        header = [c.get('name') if isinstance(c, dict) else c for c in columns]
        if all(isinstance(h, str) for h in header):
            return [header] + rows
    return rows


def read_json(content):
    text = _decode(content)
    try:
        data = _json_loads(text)
    except Unreadable:
        raise
    except RecursionError:
        raise Unreadable('This JSON file is nested too deeply. Export the data again as a flat list of records and upload.') from None
    except ValueError as exc:
        data = _ndjson(text) if 'Extra data' in str(exc) else None
        if data is None:
            reason = 'a number has more than 4,300 digits' if 'integer string conversion' in str(exc) else getattr(exc, 'msg', str(exc))[:80]
            raise _NotJson(f'This JSON file is not valid JSON ({reason}). Fix it or export the data again, and upload.') from exc
    _depth(data)
    if isinstance(data, dict) and isinstance(data.get('sheets'), list):
        return [_json_sheet(s) for s in data['sheets'][:MAX_SHEETS + 1] if isinstance(s, dict)]
    data = _unwrap(data)
    if isinstance(data, dict) and data and all(isinstance(v, list) and v and all(isinstance(x, (dict, list)) for x in v) for v in data.values()):
        return [_grid_from(name, rows) for name, rows in list(data.items())[:MAX_SHEETS + 1]]
    if isinstance(data, list):
        return [_grid_from('Data', data)]
    if isinstance(data, dict):
        b = Builder('Data', capabilities=JSON_CAPS, reader='json'); _write_document(b, data, [0]); return [b.sheet()]
    raise Unsupported('This JSON file holds a single value, not order data. Upload a list of records or the buyer Excel file.')


def _depth(value, level=0):
    if level > MAX_JSON_DEPTH:
        raise Unreadable('This JSON file is nested too deeply. Export the data again as a flat list of records and upload.')
    if isinstance(value, dict):
        for v in value.values():
            _depth(v, level + 1)
    elif isinstance(value, list):
        for v in value:
            _depth(v, level + 1)


def _scalar(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=clean)[:500]
    return v


def _json_sheet(spec):
    b = Builder(spec.get('name') or 'Sheet', bool(spec.get('hidden')), JSON_CAPS, 'json')
    if isinstance(spec.get('rows'), list):
        for r, row in enumerate(spec['rows'], 1):
            for c, v in enumerate(row if isinstance(row, list) else [row], 1):
                b.put(r, c, _scalar(v))
    for cell in spec.get('cells') or []:
        if isinstance(cell, dict):
            row, col = cell.get('row'), cell.get('col')
            if (row is None or col is None) and isinstance(cell.get('cell'), str):
                m = re.fullmatch(r'([A-Za-z]{1,3})(\d{1,7})', cell['cell'].strip())
                if m:
                    col = 0
                    for ch in m.group(1).upper():
                        col = col * 26 + ord(ch) - 64
                    row = int(m.group(2))
            if isinstance(row, int) and isinstance(col, int) and 0 < row <= EXCEL_ROWS and 0 < col <= EXCEL_COLS:
                b.put(row, col, _scalar(cell.get('value')))
    return b.sheet()


def _key_columns(records):
    """{key: column} in first-seen order. Records are then laid out by their own keys, so records with
    many different keys cost what they hold, not records x keys."""
    columns = {}
    for x in records:
        for k in x:
            if k not in columns:
                columns[k] = len(columns) + 1
    return columns


def _put_records(b, records, header_row):
    columns = _key_columns(records)
    for k, c in columns.items():
        b.put(header_row, c, str(k))
    for r, x in enumerate(records, header_row + 1):
        for k, v in x.items():
            b.put(r, columns[k], _scalar(v))
    return header_row + len(records)


def _grid_from(name, rows):
    b = Builder(name, capabilities=JSON_CAPS, reader='json')
    if rows and all(isinstance(x, dict) for x in rows):
        _put_records(b, rows, 1)
    else:
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row if isinstance(row, list) else [row], 1):
                b.put(r, c, _scalar(v))
    return b.sheet()


def _write_document(b, doc, cursor, title=None):
    """A single nested JSON document laid out like a form: 'key | value' rows, then each list of
    records as a titled table — the same shapes the Excel detectors read."""
    if title:
        cursor[0] += 1; b.put(cursor[0], 1, title)
    scalars = [(k, v) for k, v in doc.items() if not isinstance(v, (dict, list))]
    for k, v in scalars:
        cursor[0] += 1; b.put(cursor[0], 1, str(k)); b.put(cursor[0], 2, v)
    for k, v in doc.items():
        if isinstance(v, dict):
            cursor[0] += 1; _write_document(b, v, cursor, str(k))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            cursor[0] += 2; b.put(cursor[0], 1, str(k))
            cursor[0] = _put_records(b, v, cursor[0] + 1)
        elif isinstance(v, list):
            cursor[0] += 1; b.put(cursor[0], 1, str(k))
            for c, x in enumerate(v, 2):
                b.put(cursor[0], c, _scalar(x))


# --- platform self-check (health page) --------------------------------------------------------------
PACKAGES = (('openpyxl', 'openpyxl'), ('xlrd', 'xlrd'), ('python_calamine', 'python-calamine'),
            ('defusedxml', 'defusedxml'), ('olefile', 'olefile'), ('charset_normalizer', 'charset-normalizer'))


def selfcheck(probe=False):
    """{'ok': bool, 'checks': [{name, version, ok, message}]} for the health page.

    Checks the Python version (3.13.7 or later carries the html.parser fix for CVE-2025-6069), the
    Expat XML parser (2.7.2 or later recommended: older versions can be made to use excessive memory
    by small crafted XML files), the reader packages, and how uploads are isolated. probe=True also
    starts one worker process to prove isolation works (about a second)."""
    import importlib
    from importlib import metadata
    import pyexpat
    checks = []
    py = sys.version_info[:3]
    checks.append(dict(name='Python', version='.'.join(map(str, py)), ok=py >= (3, 13, 7),
                       message='OK' if py >= (3, 13, 7) else
                       'Python is older than 3.13.7, which fixes a slow-down attack on web-page (HTML) exports. Install '
                       'the latest Python 3.13 from python.org and run setup.ps1 again.'))
    expat = tuple(pyexpat.version_info)
    checks.append(dict(name='XML parser (Expat)', version='.'.join(map(str, expat)), ok=expat >= (2, 7, 2),
                       message='OK' if expat >= (2, 7, 2) else
                       'The Expat XML parser is older than 2.7.2; newer versions stop small crafted XML files from using '
                       'excessive memory. Uploads are still size-limited and read in a separate worker. Install the latest '
                       'Python 3.13 from python.org (it includes a current Expat) and run setup.ps1 again.'))
    for module, dist in PACKAGES:
        try:
            importlib.import_module(module)
            version, ok, message = metadata.version(dist), True, 'OK'
        except Exception:
            version, ok, message = '', False, f'{dist} is missing or broken, so some upload formats cannot be read. Run setup.ps1 again.'
        checks.append(dict(name=dist, version=version, ok=ok, message=message))
    if _inline_requested():
        checks.append(dict(name='Upload isolation', version='inline', ok=False,
                           message=f'Uploads are read inside the server process because {INLINE_ENV}=1 is set. Remove that '
                                   'setting outside testing so a damaged file cannot slow down or stop the server.'))
    elif probe:
        try:
            limits = isolation.run(isolation.diagnose, ('limits',), timeout=30)
            checks.append(dict(name='Upload isolation', version='worker process', ok=True,
                               message=f'OK: uploads are read in a separate process with a {PARSE_TIMEOUT}-second limit '
                                       f'and a memory cap ({json.dumps(limits)}).'))
        except Exception as exc:
            checks.append(dict(name='Upload isolation', version='unavailable', ok=False,
                               message=f'Upload workers could not start ({exc}); files are read inside the server process. '
                                       'Restart POPPYS AI; if this stays, run setup.ps1 again.'))
    else:
        failed = stats['inline_fallback'] > 0
        checks.append(dict(name='Upload isolation', version='worker process', ok=not failed,
                           message='Upload workers could not start for some uploads, so they were read inside the server '
                                   'process. Restart POPPYS AI; if this stays, run setup.ps1 again.' if failed else
                                   f'OK: uploads are read in a separate process with a {PARSE_TIMEOUT}-second limit and a memory cap.'))
    return dict(ok=all(c['ok'] for c in checks), checks=checks)


READERS = {'ooxml': read_ooxml, 'xls': read_xls, 'xlsb': read_zip_calamine, 'ods': read_ods,
           'xml2003': read_xml2003, 'html': read_html, 'mhtml': read_mhtml, 'csv': read_csv, 'json': read_json}
