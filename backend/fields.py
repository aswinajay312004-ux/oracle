"""Canonical business fields of an order (docs/EXCEL_UNDERSTANDING.md §2).

Defaults live here; an administrator can add or remove buyer aliases and make unlocked fields
required (saved in local_store.field_settings). Effective fields = defaults + saved changes, and
the parser, matcher, validator and planner all receive them explicitly — there is no module-level
mutable state.
"""
from __future__ import annotations
import re
import unicodedata

KINDS = ('text', 'code', 'date', 'number', 'size', 'quantity', 'colour', 'unit')


def _f(key, label, kind, where, aliases, locked=False, column=False):
    return dict(key=key, label=label, kind=kind, where=where, aliases=list(aliases), locked=locked,
                required=locked, column=column or where == 'table')


# Order matters: it is the order of the Setup screen and of header-field detection.
DEFAULTS = (
    _f('buyer', 'Buyer', 'text', 'header', ['buyer', 'buyer name', 'customer', 'customer name', 'brand'], True, True),
    _f('style', 'Style #', 'code', 'header', ['style', 'style no', 'style number', 'item', 'item number', 'style #',
                                              'item no', 'article', 'article no'], True, True),
    _f('description', 'Description', 'text', 'header', ['description', 'garment description', 'item description',
                                                         'style description'], True, True),
    _f('po_number', 'PO / Contract No', 'code', 'header', ['po', 'po number', 'po no', 'contract', 'contract no', 'order no',
                                                           'purchase order no', 'po #'], True, True),
    _f('order_ref', 'OC No / Order reference', 'code', 'header', ['oc no', 'order reference', 'order ref', 'docid', 'ocn'], True, True),
    _f('order_date', 'Ord Confm Dt', 'date', 'header', ['order date', 'ord confm dt', 'order confirmation date',
                                                        'confirmation date', 'oc date'], True),
    _f('ship_date', 'Ship Dt', 'date', 'header', ['ship date', 'ship dt', 'delivery date', 'ex factory date', 'ex-factory date',
                                                  'fob date', 'etd'], True, True),
    _f('sample_type', 'Sample type', 'text', 'header', ['sample type'], True),
    _f('exporter', 'Exporter', 'text', 'header', ['exporter', 'exporter name'], True),
    _f('season', 'Season', 'text', 'header', ['season'], column=True),
    _f('merch', 'Merchandiser', 'text', 'header', ['merchandiser', 'merch', 'merchant'], column=True),
    _f('department', 'Department', 'text', 'header', ['department', 'dept']),
    _f('gender', 'Gender', 'text', 'header', ['gender']),
    _f('against', 'Against', 'text', 'header', ['against', 'order against']),
    _f('packing_type', 'Packing type', 'text', 'header', ['packing type', 'pack type']),
    _f('components', 'Components', 'number', 'header', ['components', 'no of components', 'component count']),
    _f('size', 'Size', 'size', 'table', ['size', 'sizes'], True),
    _f('quantity', 'Quantity', 'quantity', 'table', ['quantity', 'qty', 'sample qty', 'order qty', 'pcs', 'pieces'], True),
    _f('color', 'Colour / combo', 'colour', 'table', ['colour', 'color', 'combo', 'colour description', 'color description',
                                                      'colour name', 'color name', 'shade'], True),
    _f('qty_unit', 'Quantity unit (pieces / packs)', 'unit', 'header', ['unit', 'uom', 'qty unit', 'quantity unit',
                                                                         'unit of measure'], True),
)
MAX_ALIASES = 50


_ASCII_NORM = re.compile(r'[^a-z0-9]+')


def norm(value):
    """The words of a label, lower-case, punctuation as single spaces. Letters, digits and vowel signs of
    every script are kept (NFKC first), so Tamil labels such as “வாங்குபவர்” stay distinct words instead of
    all normalising to the same empty text."""
    s = str(value or '')
    if s.isascii():
        return _ASCII_NORM.sub(' ', s.lower()).strip()
    s = unicodedata.normalize('NFKC', s).lower()
    if s.isascii():
        return _ASCII_NORM.sub(' ', s).strip()
    return ' '.join(''.join(ch if ch.isalnum() or unicodedata.category(ch)[0] == 'M' else ' ' for ch in s).split())


def effective(saved=None):
    """Defaults + saved changes ({key: {required, aliases}}) → list of field dicts (fresh copies)."""
    saved = saved or {}
    out = []
    for d in DEFAULTS:
        s = saved.get(d['key']) or {}
        f = dict(d, aliases=list(d['aliases']), default_aliases=list(d['aliases']))
        if s.get('aliases') is not None:
            f['aliases'] = list(s['aliases'])
        if s.get('required') is not None and not d['locked']:
            f['required'] = bool(s['required'])
        out.append(f)
    return out


class Fields:
    """Read-only view of the effective fields, passed explicitly to parsing, matching and planning."""

    def __init__(self, items=None):
        items = effective() if items is None else items
        known = {d['key']: d for d in DEFAULTS}
        self._items = {}
        for f in items:
            base = known.get(f['key'], {})
            # Field lists may come back from GET /api/fields: fill structural keys from the defaults.
            g = dict(base, **{k: v for k, v in f.items() if v is not None or k not in base})
            g.setdefault('where', 'header'); g.setdefault('kind', 'text'); g.setdefault('label', f['key'])
            g.setdefault('locked', False); g.setdefault('required', g['locked']); g.setdefault('column', g['where'] == 'table')
            g['aliases'] = tuple(g.get('aliases') or ())
            self._items[f['key']] = g
        self.keys = tuple(self._items)
        self.header_keys = tuple(k for k, f in self._items.items() if f['where'] == 'header' and f['kind'] != 'unit')
        self.column_keys = tuple(k for k, f in self._items.items() if f['column'])
        self.label_words = frozenset(norm(a) for f in self._items.values() for a in f['aliases'] if norm(a))
        self.extra_required = tuple(k for k, f in self._items.items() if f['required'] and not f['locked'])

    def __contains__(self, key):
        return key in self._items

    def get(self, key):
        return self._items.get(key)

    def aliases(self, key):
        return list(self._items[key]['aliases']) if key in self._items else []

    def kind(self, key):
        return self._items[key]['kind'] if key in self._items else 'text'

    def label(self, key):
        return self._items[key]['label'] if key in self._items else key.replace('_', ' ')

    def alias_map(self, keys=None):
        return {k: self.aliases(k) for k in (keys or self.column_keys)}

    def owner(self, alias):
        """The field whose aliases include this label (after normalisation), or None."""
        n = norm(alias)
        return next((k for k, f in self._items.items() if n and n in {norm(a) for a in f['aliases']}), None)

    def items(self):
        return [dict(f, aliases=list(f['aliases'])) for f in self._items.values()]


DEFAULT = Fields()


def resolve(fields):
    """None → defaults; a Fields view → itself; a list of field dicts → a view of it."""
    if fields is None:
        return DEFAULT
    return fields if isinstance(fields, Fields) else Fields(fields)


def clean_aliases(key, aliases, current):
    """Validate an alias list for one field against the other fields' aliases (409-style ValueError)."""
    if len(aliases) > MAX_ALIASES:
        raise ValueError(f'At most {MAX_ALIASES} aliases per field.')
    out = []; seen = set()
    for a in aliases:
        text = ' '.join(str(a or '').split())
        n = norm(text)
        if not n or len(text) > 60:
            raise ValueError(f'"{text[:60]}" is not a usable alias: use 1–60 characters with letters or digits.')
        if n in seen:
            continue
        other = next((f for f in current.items() if f['key'] != key and n in {norm(x) for x in f['aliases']}), None)
        if other:
            raise ValueError(f'"{text}" is already an alias of {other["label"]}. An alias can belong to one field only.')
        seen.add(n); out.append(text)
    return out
