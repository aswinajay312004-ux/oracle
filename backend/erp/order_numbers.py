"""OC numbers (the order reference, ORDMAS.DOCID) given by POPPYS AI when the person leaves the reference empty.

The company's series is set in config/company.json → "order_number", e.g. {"format": "PK/{fy}/S/{n}"} gives
PK/26-27/S/1320: {fy} is the financial year of the day the order is saved (April–March unless "year_starts_month" says
otherwise), {n} the running number. The number is the next one after the highest number of the series already in
ORDMAS (Axpert's own orders and POPPYS's), taken while the writer holds PP_WRITE_LOCK, so two POPPYS saves never get
the same number; PP_ORDER_KEYS refuses a duplicate in any case.

Until the save, the plan carries a stand-in (AUTO-OC#1#) so the preview hash does not depend on a number that another
order could take in the meantime."""
from __future__ import annotations
import re
from datetime import date

SAFE = re.compile(r'[A-Za-z0-9/\-. ]*')     # also keeps the prefix usable in a LIKE pattern (no % or _)
MARK = re.compile(r'AUTO-OC#\d+#')


def series(company=None):
    """The configured series ({format, year_starts_month, digits}) or None when references are typed by people.
    A malformed series raises ValueError: saving must not guess a numbering."""
    if company is None:
        from ..config import COMPANY as company
    spec = (company or {}).get('order_number') or {}
    fmt = str(spec.get('format') or '').strip()
    if not fmt:
        return None
    start = spec.get('year_starts_month', 4); digits = spec.get('digits', 0)
    head = fmt[:-3] if fmt.endswith('{n}') else None
    if head is None or '{n}' in head or not SAFE.fullmatch(head.replace('{fy}', '')) or len(fmt) > 24 \
            or not isinstance(start, int) or not 1 <= start <= 12 or not isinstance(digits, int) or not 0 <= digits <= 8:
        raise ValueError('The OC number series in config/company.json is not valid: "format" must end with {n} and use only '
                         'letters, digits, / - . and {fy}; "year_starts_month" 1–12; "digits" 0–8.')
    return dict(format=fmt, year_starts_month=start, digits=digits)


def financial_year(day: date, start_month=4):
    first = day.year if day.month >= start_month else day.year - 1
    return f'{first % 100:02d}-{(first + 1) % 100:02d}'


def prefix(spec, day: date):
    """Everything before the running number, e.g. 'PK/26-27/S/'."""
    return spec['format'][:-3].replace('{fy}', financial_year(day, spec['year_starts_month']))


def example(spec, day: date):
    return prefix(spec, day) + ('1'.zfill(spec['digits']) if spec['digits'] else 'N')


def marker(index):
    """The stand-in for order `index` (0-based) of a draft until the number is given."""
    return f'AUTO-OC#{index + 1}#'


def is_marker(value):
    return isinstance(value, str) and bool(MARK.fullmatch(value))


def following(existing, head, count, digits=0):
    """`count` numbers after the highest number of the series `head` among `existing` references."""
    last = 0; size = len(head); up = head.upper()
    for ref in existing:
        ref = str(ref or '').strip()
        tail = ref[size:]
        if ref[:size].upper() == up and tail.isdigit():
            last = max(last, int(tail))
    return [head + str(last + k).zfill(digits) for k in range(1, count + 1)]


def assign(orders, existing, spec, day: date):
    """{marker: number} for the orders saved without a reference, in their order in the draft."""
    empty = [i for i, o in enumerate(orders) if not str(o.get('order_ref') or '').strip()]
    if not empty:
        return {}
    if spec is None:
        raise ValueError('An order has no OC number and no automatic series is set in config/company.json.')
    numbers = following(existing, prefix(spec, day), len(empty), spec['digits'])
    return {marker(i): n for i, n in zip(empty, numbers)}


def fill(value, assigned):
    """The value with every stand-in replaced by its number (a template may quote the reference inside other text)."""
    if not assigned or not isinstance(value, str) or 'AUTO-OC#' not in value:
        return value
    return MARK.sub(lambda m: assigned.get(m.group(0), m.group(0)), value)
