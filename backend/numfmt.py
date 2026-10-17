"""A deliberately narrow Excel number-format renderer: only what keeps buyer codes intact.

Excel shows the number 8 formatted ``0000`` as ``0008``, 123 formatted ``000-000`` as ``000-123`` and 8
formatted ``"ST-"0000`` as ``ST-0008``. Style, colour and article codes are often stored that way, so a
reader must return the text Excel shows, not the bare number.

Only the first format section is used (the one Excel applies to positive numbers and zero), and only
for whole, non-negative numbers. A section may contain digit placeholders (``0`` and ``#``), quoted
literals, backslash escapes, the characters Excel shows without quotes (``$ - + ( ) : ! ^ & ' ~ { } < >
= space``), ``[$...-locale]`` currency tokens and colour tokens. Anything else - decimals, thousands
separators, percent, scientific, fractions, dates, times, text sections, conditions - is not rendered:
``render()`` returns None and the caller keeps the raw number (and may record the format code).
"""
from __future__ import annotations

LITERAL = set(' $-+():!^&\'~{}<>=')
COLOURS = {'black', 'blue', 'cyan', 'green', 'magenta', 'red', 'white', 'yellow'}
MAX_CODE = 255          # Excel's own limit for a format code
MAX_VALUE = 10 ** 15    # Excel keeps 15 significant digits; larger "numbers" are damaged codes


def first_section(code):
    """The first ';'-separated section of a format code, honouring quotes and escapes."""
    out, quoted, escaped = [], False, False
    for ch in code:
        if escaped:
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif ch == ';' and not quoted:
            break
        out.append(ch)
    return ''.join(out)


def _tokens(section):
    """[('0' | '#' | 'lit', text)] for a digits-only section, or None when it is anything else."""
    tokens, i, n = [], 0, len(section)
    while i < n:
        ch = section[i]
        if ch == '"':
            end = section.find('"', i + 1)
            if end < 0:
                return None
            tokens.append(('lit', section[i + 1:end])); i = end + 1; continue
        if ch == '\\':
            if i + 1 >= n:
                return None
            tokens.append(('lit', section[i + 1])); i += 2; continue
        if ch in '_*':            # _x pads with the width of x, *x repeats x to fill the cell: no text
            i += 2; continue
        if ch == '[':
            end = section.find(']', i + 1)
            if end < 0:
                return None
            body = section[i + 1:end]
            if body.startswith('$'):                      # [$₹-4009] currency symbol with a locale
                tokens.append(('lit', body[1:].split('-', 1)[0]))
            elif body.lower() not in COLOURS and not body.lower().startswith('color'):
                return None                               # conditions, elapsed time, DBNum, ...
            i = end + 1; continue
        if ch in '0#':
            tokens.append((ch, ch))
        elif ch in LITERAL or (ord(ch) > 127 and ch.isprintable()):
            tokens.append(('lit', ch))
        else:
            return None   # . , % E / ? @ letters (General, dates, times): not a digits-only format
        i += 1
    return tokens


def render(value, code):
    """The text Excel shows for a whole, non-negative number in a digits-only format, or None when
    the format is not one this renderer handles or adds nothing (the raw number is then kept)."""
    if not code or len(code) > MAX_CODE or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    number = int(value)
    if number < 0 or number >= MAX_VALUE:
        return None
    tokens = _tokens(first_section(str(code)))
    if not tokens or not any(kind == '0' for kind, _ in tokens):
        return None
    digits = str(number)
    places = [i for i, (kind, _) in enumerate(tokens) if kind in '0#']
    shown = [text if kind == 'lit' else '' for kind, text in tokens]
    remaining = digits
    for i in reversed(places):                            # Excel fills placeholders from the right
        if remaining:
            shown[i] = remaining[-1]; remaining = remaining[:-1]
        elif tokens[i][0] == '0':
            shown[i] = '0'
    if remaining:                                         # more digits than placeholders: all shown
        shown[places[0]] = remaining + shown[places[0]]
    text = ''.join(shown).strip()
    return text if text != digits else None


def fixed(value, code):
    """The text Excel shows for a non-negative number in a fixed-decimals format (``0.00``, ``#0.000``), or None for any
    other format. Codes stored as numbers keep their trailing zeros this way (5012.1 formatted 0.00 is 5012.10)."""
    if not code or len(code) > MAX_CODE or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    section = first_section(str(code)).strip()
    if not section or any(ch not in '#0.' for ch in section) or section.count('.') != 1:
        return None
    whole, decimals = section.split('.')
    if not whole or set(decimals) != {'0'} or not 0 <= value < MAX_VALUE:
        return None
    return f'{value:.{len(decimals)}f}'


def is_text_format(code):
    """True for Excel's Text format ('@'), which keeps typed digits as text."""
    if not code:
        return False
    section = first_section(str(code)).replace('"', '').replace('\\', '').strip()
    return section == '@'
