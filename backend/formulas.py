"""Results for simple formulas that a file saved without them.

Excel stores each formula's last result next to the formula. Files written by scripts, exports and some
tools store only the formula, so a reader sees an empty cell where Excel would show 200. For a small,
safe subset this module calculates the result the way Excel would:

    numbers, cell references (A1, $A$1, Sheet!A1, 'My Sheet'!A1), + - * / ^, unary minus, brackets,
    SUM / MIN / MAX / AVERAGE / COUNT over cells and ranges, ROUND(x, n), ROUNDUP/ROUNDDOWN(x, n)

Anything else (text functions, lookups, IF, external links, names) is left empty exactly as before.
Nothing in the workbook is executed: the formula text is tokenised and evaluated by this code only.
Every calculated cell gets computed=True so the preview can say "calculated from formula", and the
sheet gets one note so people know which values were not stored in the file.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN
import re

MAX_FORMULA = 2000          # characters of one formula
MAX_RANGE = 100_000         # cells in one range reference
MAX_DEPTH = 60              # chain of formulas referring to formulas
MAX_WORK = 2_000_000        # cell visits for one workbook

_TOKEN = re.compile(r"""\s*(?:
    (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?)
  | (?P<num>\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)
  | (?P<func>[A-Za-z][A-Za-z0-9.]*)\s*\(
  | (?P<op>[-+*/^(),])
)""", re.X)
_CELL = re.compile(r'\$?([A-Za-z]{1,3})\$?(\d{1,7})')
FUNCTIONS = {'SUM', 'MIN', 'MAX', 'AVERAGE', 'COUNT', 'ROUND', 'ROUNDUP', 'ROUNDDOWN'}


class Unsupported(Exception):
    """The formula uses something outside the safe subset; the cell stays empty."""


def _col(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def _tokens(text):
    pos, out = 0, []
    while pos < len(text):
        if text[pos:].strip() == '':
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Unsupported(text[pos:pos + 20])
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Book:
    def __init__(self, sheets):
        self.grid = {s['name'].upper(): {(c['row'], c['col']): c for c in s['cells']} for s in sheets}
        self.work = 0

    def cell(self, sheet, row, col, depth):
        self.work += 1
        if self.work > MAX_WORK:
            raise Unsupported('too much work')
        c = self.grid.get(sheet, {}).get((row, col))
        if c is None:
            return None
        if c.get('value') is None and c.get('formula') and not c.get('error'):
            self.fill(sheet, c, depth + 1)
        if c.get('error'):
            raise Unsupported('error value')
        return c.get('value')

    def fill(self, sheet, c, depth):
        if c.get('_busy') or depth > MAX_DEPTH:
            raise Unsupported('circular or too deep')
        c['_busy'] = True
        try:
            value = _Parser(self, sheet, str(c['formula']), depth).run()
        finally:
            c.pop('_busy', None)
        c['value'] = value
        c['computed'] = True


class _Parser:
    def __init__(self, book, sheet, formula, depth):
        if len(formula) > MAX_FORMULA or not formula.startswith('='):
            raise Unsupported('not a formula')
        self.book, self.sheet, self.depth = book, sheet, depth
        self.toks = _tokens(formula[1:]); self.i = 0

    def run(self):
        v = self.expr()
        if self.i != len(self.toks):
            raise Unsupported('trailing text')
        return _number_out(self.scalar(v))

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, op=None):
        t = self.peek()
        if op is not None and t != ('op', op):
            raise Unsupported(f'expected {op}')
        self.i += 1
        return t

    def expr(self):
        v = self.term()
        while self.peek() in (('op', '+'), ('op', '-')):
            op = self.take()[1]; r = self.scalar(self.term()); v = self.scalar(v)
            v = v + r if op == '+' else v - r
        return v

    def term(self):
        v = self.power()
        while self.peek() in (('op', '*'), ('op', '/')):
            op = self.take()[1]; r = self.scalar(self.power()); v = self.scalar(v)
            if op == '/' and r == 0:
                raise Unsupported('division by zero')
            v = v * r if op == '*' else v / r
        return v

    def power(self):
        v = self.unary()
        if self.peek() == ('op', '^'):
            self.take(); r = self.scalar(self.unary()); b = self.scalar(v)
            if r != r.to_integral_value() or abs(r) > 10:
                raise Unsupported('power')
            return b ** int(r)
        return v

    def unary(self):
        if self.peek() == ('op', '-'):
            self.take(); return -self.scalar(self.unary())
        if self.peek() == ('op', '+'):
            self.take(); return self.scalar(self.unary())
        return self.atom()

    def atom(self):
        kind, text = self.take()
        if kind == 'num':
            return Decimal(text)
        if kind == 'ref':
            return self.ref(text)
        if kind == 'func':
            return self.call(text.upper())
        if (kind, text) == ('op', '('):
            v = self.expr(); self.take(')'); return v
        raise Unsupported(str(text))

    def ref(self, text):
        sheet = self.sheet
        if '!' in text:
            name, text = text.rsplit('!', 1)
            sheet = (name[1:-1].replace("''", "'") if name.startswith("'") else name).upper()
        parts = [_CELL.fullmatch(p) for p in text.split(':')]
        (c0, r0), (c1, r1) = [(_col(m.group(1)), int(m.group(2))) for m in (parts[0], parts[-1])]
        r0, r1 = sorted((r0, r1)); c0, c1 = sorted((c0, c1))
        if len(parts) == 1:
            return ('cell', self.book.cell(sheet, r0, c0, self.depth))
        if (r1 - r0 + 1) * (c1 - c0 + 1) > MAX_RANGE:
            raise Unsupported('range too large')
        grid = self.book.grid.get(sheet, {})
        keys = [k for k in grid if r0 <= k[0] <= r1 and c0 <= k[1] <= c1]
        return ('range', [self.book.cell(sheet, r, c, self.depth) for r, c in sorted(keys)])

    def scalar(self, v):
        if isinstance(v, Decimal):
            return v
        if isinstance(v, tuple) and v[0] == 'cell':
            x = v[1]
            if x is None or x == '':
                return Decimal(0)                   # Excel treats an empty cell as 0 in arithmetic
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise Unsupported('text in arithmetic')
            return Decimal(str(x))
        raise Unsupported('range used as a number')

    def call(self, name):
        if name not in FUNCTIONS:
            raise Unsupported(name)
        args = []
        if self.peek() != ('op', ')'):
            args.append(self.expr())
            while self.peek() == ('op', ','):
                self.take(); args.append(self.expr())
        self.take(')')
        if name.startswith('ROUND'):
            if len(args) != 2:
                raise Unsupported(name)
            x, n = self.scalar(args[0]), self.scalar(args[1])
            mode = {'ROUND': ROUND_HALF_UP, 'ROUNDUP': ROUND_UP, 'ROUNDDOWN': ROUND_DOWN}[name]
            return x.quantize(Decimal(1).scaleb(-int(n)), rounding=mode)
        nums = []
        for a in args:
            if isinstance(a, tuple) and a[0] == 'range':
                nums += [Decimal(str(x)) for x in a[1] if isinstance(x, (int, float)) and not isinstance(x, bool)]
            elif isinstance(a, tuple) and a[0] == 'cell':
                x = a[1]
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    nums.append(Decimal(str(x)))
            else:
                nums.append(self.scalar(a))
        if name == 'SUM':
            return sum(nums, Decimal(0))
        if name == 'COUNT':
            return Decimal(len(nums))
        if not nums:
            if name == 'AVERAGE':
                raise Unsupported('average of nothing')
            return Decimal(0)
        return {'MIN': min, 'MAX': max}[name](nums) if name != 'AVERAGE' else sum(nums) / len(nums)


def _number_out(d):
    d = d.quantize(Decimal('1e-10')) if d == d.to_integral_value() or abs(d.as_tuple().exponent) > 10 else d
    if d == d.to_integral_value():
        return int(d)
    return float(d.normalize())


def fill_missing(sheets):
    """Calculate empty formula cells in place. Returns the number of cells calculated."""
    book = _Book(sheets); done = 0
    for s in sheets:
        name = s['name'].upper(); missing = calculated = 0
        for c in s['cells']:
            if c.get('formula') and c.get('value') is None and not c.get('error'):
                missing += 1
                try:
                    book.fill(name, c, 0)
                    calculated += 1
                except Unsupported:
                    c.pop('_busy', None)
                except (ArithmeticError, ValueError):
                    pass
            elif c.get('computed'):
                calculated += 1
        if missing:
            done += calculated
            left = missing - calculated
            s.setdefault('notes', []).append(
                f'{missing:,} formula{"s" if missing > 1 else ""} on “{s["name"]}” had no saved result in the file. '
                + (f'POPPYS calculated {calculated:,} simple one{"s" if calculated > 1 else ""} (marked “calculated”). ' if calculated else '')
                + (f'{left:,} could not be calculated and stay empty; open the file in Excel and save it to store them.' if left else
                   'Check them against Excel if they matter.'))
    return done
