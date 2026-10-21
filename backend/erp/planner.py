"""Deterministic, registry-driven ERP entry planner.

Turns reviewed business orders into an exact, hashable list of row operations. It never writes.
Business rules (quantities, units, duplicates, masters, relationships, BOM arithmetic) are
code; table/column placement comes from config/oracle_mapping.json and only ``verified``
columns of ``verified`` tables are planned for insert.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from . import masters as masters_index, order_numbers
from .mapping import referenced_tables, fingerprint

LIMITS = dict(orders=50, lines=100, sizes=100, fabrics=100, yarns=10, accessories=300)
CONTENT_FIBRES = ('cotton', 'polyester', 'nylon', 'lycra', 'elastane', 'spandex', 'viscose', 'modal', 'wool', 'linen', 'acrylic', 'bamboo', 'tencel')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def normalize(s):
    return ' '.join(str(s or '').strip().upper().split())


def count(value, name, errors, allow_zero=True):
    """Whole, non-negative quantity. Appends a plain-language error and returns 0 when invalid."""
    try:
        if isinstance(value, bool) or value is None or str(value).strip() == '':
            raise ValueError()
        d = Decimal(str(value))
        if not d.is_finite() or d != d.to_integral_value() or d < 0 or d > 999999999:
            raise ValueError()
        if d == 0 and not allow_zero:
            raise ValueError()
        return int(d)
    except (ValueError, InvalidOperation):
        errors.append(f'{name}: enter a {"non-negative" if allow_zero else "positive"} whole number.')
        return 0


def decimal(value, name, errors, positive=False, required=True):
    try:
        if isinstance(value, bool) or value is None or str(value).strip() == '':
            if not required:
                return None
            raise ValueError()
        d = Decimal(str(value).strip().replace(',', ''))
        if not d.is_finite() or d < 0 or d > Decimal('999999999') or (positive and d == 0):
            raise ValueError()
        return d
    except (ValueError, InvalidOperation):
        errors.append(f'{name}: enter a {"positive" if positive else "non-negative"} number.')
        return None


def q(value, places=3):
    return None if value is None else Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def plain(value):
    """JSON-safe values: Decimals become int or float with their exact decimal text."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class Issues:
    """Errors block approval; warnings must be read. Each keeps a draft path for the UI."""

    def __init__(self):
        self.items = []

    def add(self, severity, message, path=None):
        self.items.append(dict(severity=severity, message=message, path=path))
        return self.items[-1]

    def error(self, message, path=None):
        self.add('error', message, path)

    def warn(self, message, path=None):
        self.add('warning', message, path)

    def sink(self, path=None, severity='error'):
        issues = self

        class _Sink(list):
            def append(self, message):
                issues.add(severity, message, path)
        return _Sink()

    def messages(self, severity):
        return list(dict.fromkeys(i['message'] for i in self.items if i['severity'] == severity))


def _field(ctx, path):
    scope, _, name = path.partition('.')
    value = (ctx.get(scope) or {}).get(name)
    return value.strip() if isinstance(value, str) else value


def _template(ctx, text):
    return re.sub(r'\{([a-z]+\.[a-z_]+)\}', lambda m: str(_field(ctx, m.group(1))), text)


def _line_number(text):
    m = re.search(r'garment\s*-?\s*(\d+)', str(text or ''), re.I)
    return int(m.group(1)) if m else None


def _unit_factor(o, prefix, base, issues):
    """Pieces per source quantity (docs/EXCEL_UNDERSTANDING.md §5): PCS → 1, PACK → pieces per pack ≥ 2,
    undecided → asked on orders.N.qty_unit. Drafts saved before qty_unit existed keep the old rule."""
    if 'qty_unit' not in o:
        return count(o.get('pieces_per_unit'), prefix + ' pieces per source unit', issues.sink(base + '.pieces_per_unit'), False)
    unit = str(o.get('qty_unit') or '').strip().upper(); per = o.get('pieces_per_unit')
    blank = per is None or str(per).strip() == ''
    if unit == 'PCS':
        if not blank and str(per).strip() not in ('1', '1.0'):
            issues.error(f'{prefix}: quantities are pieces, so pieces per unit must be 1 (it says {per}). Clear it or choose Packs.',
                         base + '.pieces_per_unit')
        return 1
    if unit == 'PACK':
        if blank:
            issues.error(f'{prefix}: how many pieces are in one pack?', base + '.pieces_per_unit')
            return 0
        n = count(per, prefix + ' pieces per pack', issues.sink(base + '.pieces_per_unit'), False)
        if n == 1:
            issues.error(f'{prefix}: a pack holds at least 2 pieces — how many pieces are in one pack?', base + '.pieces_per_unit')
        return n
    if unit:
        issues.error(f'{prefix}: quantity unit must be Pieces or Packs.', base + '.qty_unit')
    else:
        issues.error(f'{prefix}: {_unit_question(o)}', base + '.qty_unit')
    return 0


def _unit_question(o):
    """The question recorded when the file was read (it quotes what the file says), else a plain one."""
    q = str(((o.get('provenance') or {}).get('qty_unit') or {}).get('question') or '').strip()
    if not q:
        return 'the file does not say whether these quantities are pieces or packs — which are they?'
    return q[0].lower() + q[1:] if q[:1].isupper() and q[1:2].islower() else q


def plan(catalog, orders, filehash, mapping, required=()):
    """required: extra order fields an administrator made required (keys of backend/fields.py)."""
    from .. import fields as business_fields
    tables = mapping['tables']
    registry = catalog.columns(referenced_tables(mapping))
    writable = {t for t, s in tables.items() if s['status'] == 'verified'}
    insertable = catalog.insertable(referenced_tables(mapping))
    issues = Issues()
    operations = []; known = {}; lookups = {}; reference_only = {}; skipped = {}; derived = {}
    master_names = {m['table']: m['business'] + ' master' for m in mapping['masters'].values()}
    totals = dict(orders=len(orders), lines=0, pieces=0, fabrics=0, fabric_kg=0, yarns=0, accessories=0)

    for table in writable:
        if table not in registry:
            issues.error(f'{table}: table is not visible to POPPYS AI. Check grants with scripts/grant_bom_tables.py.')
        elif table not in insertable:
            issues.error(f'{table}: POPPYS AI has no INSERT permission. Run scripts/grant_bom_tables.py.')
    for table, spec in tables.items():
        skipped[table] = [c for c, s in spec['columns'].items() if s['status'] != 'verified']

    from ..config import OWNER
    if mapping.get('owner') and mapping['owner'].upper() != OWNER:
        issues.error(f'The write mapping was made for the Oracle schema {mapping["owner"]}, but this installation reads {OWNER}. '
                     'Saving stays off until a write mapping for this schema is prepared and verified.')
    ref_target = _written(mapping).get('order.order_ref')
    if not ref_target or ref_target[2] != 'verified':
        issues.error('Saving is off: this installation has no Oracle column set for the OC No. An administrator has to set it '
                     'in config/oracle_mapping.json.')
    if not orders or len(orders) > LIMITS['orders']:
        issues.error('Import must contain 1–50 orders.')
    if catalog.file_committed(filehash):
        issues.error('This exact workbook has already been committed. Open Import history to view it.')

    def add(key, table, values, action='insert', provenance=None, where='', path=None):
        operations.append(dict(key=key, table=table, action=action, values=values, source=provenance or {}, path=path,
                               business=tables.get(table, {}).get('business') or master_names.get(table, ''),
                               where=where))
        return '$' + key


    def master(role, label, where, provenance=None, path=None, required=True, field_path=None):
        """The record ID of a master value: an existing record (exact text, else the same letters and digits,
        backend/erp/masters.py), or a new record when the master allows it (similar existing names are listed)."""
        spec = mapping['masters'][role]
        label = ' '.join(str(label or '').strip().split())
        if not label:
            if required:
                issues.error(f'{where}: {role} is required.', path)
            return None
        table, pk, col = spec['table'], spec['key'], spec['label']
        code = (role, normalize(label))
        if code in known:
            return known[code]
        if table not in registry or pk not in registry[table] or col not in registry[table]:
            issues.error(f'{where}: the {spec["business"].lower()} master ({table}) is not available in the ERP schema. '
                         f'Leave this column unverified or grant access to the master table.', path)
            return None
        if role not in lookups:
            lookups[role] = masters_index.Index(catalog.masters(spec), normalize)
        index = lookups[role]; business = spec['business'].lower(); at = field_path or path
        matches = index.exact.get(normalize(label), [])
        loose = not matches and index.same_letters(label)
        if loose and len({x['id'] for x in loose}) > 1:
            names = ', '.join(f'“{x["label"]}”' for x in loose[:4])
            issues.error(f'{where}: {business} “{label}” could be any of {names} in {table}. Type it exactly as one of them.', at)
            return None
        if loose:
            matches = loose[:1]
            issues.warn(f'{where}: “{label}” is the existing {business} “{matches[0]["label"]}” ({table} record {matches[0]["id"]}); '
                        'only spacing, punctuation or letter case differ, so no new record is made.', at)
        if len(matches) > 1 or (matches and matches[0]['id'] is None):
            issues.error(f'{where}: ambiguous {role} master "{label}". Resolve duplicate master records.', path)
            return None
        key = f'm{len(known) + 1}'
        if matches and matches[0]['cancelled']:
            issues.error(f'{where}: {role} master "{label}" is cancelled.', path)
            return None
        if matches:
            val = matches[0]['id']
            add(key, table, {pk: val, col: matches[0]['label']}, 'reuse', provenance, where)
        elif not spec.get('create'):
            issues.error(f'{where}: {spec["business"].lower()} "{label}" is not in the {table} master. Add it in Axpert first.', path)
            return None
        else:
            if table not in insertable:
                issues.error(f'{where}: new {role} "{label}" needs INSERT permission on {table}.', path)
            near = index.similar(label) if role in masters_index.NAMES else []
            if near:
                names = ', '.join(f'“{x["label"]}”' for _, x in near)
                issues.warn(f'{where}: a new {business} “{label}” will be added to {table}. Similar existing: {names}. '
                            'If it is one of these, change it to that name before approving.', at)
            val = add(key, table, {pk: '$' + key, col: label}, provenance=provenance, where=where)
        known[code] = val
        return val

    def evaluate(table, ctx, where, path, provenance=None):
        values = {}
        for col, c in tables[table]['columns'].items():
            if c['status'] != 'verified':
                continue
            if c.get('key'):
                value = '$' + ctx['self']
            elif 'parent' in c:
                value = '$' + ctx['keys'][c['parent']]
            elif 'copy' in c:
                src, _, name = c['copy'].partition('.')
                value = ctx['ops'][src].get(name)
            else:
                value = c['value'] if 'value' in c else _template(ctx, c['template']) if 'template' in c else _field(ctx, c['field'])
                if c.get('master'):
                    if value is None or str(value).strip() == '':
                        continue                   # blank in the file: no master record, the column stays NULL
                    name = c.get('field', '').partition('.')[2]
                    src = (provenance or {}).get(name) if provenance else None
                    value = master(c['master'], value, where, src, path, c.get('required', True), f'{path}.{name}' if name else path)
                elif c.get('optional') and (value is None or str(value).strip() == ''):
                    continue
            values[col] = plain(value)
        return values

    seen = set()
    try:
        auto = order_numbers.series()
    except ValueError as exc:
        auto = None; issues.error(str(exc))
    for index, o in enumerate(orders):
        prefix = f'Order {index + 1}'; base = f'orders.{index}'
        ref = str(o.get('order_ref', '')).strip()
        if not ref and auto:
            # The OC number is given when the order is saved (oracle_store.commit); until then a stand-in keeps the plan
            # and its hash independent of a number another order could take first.
            ref = order_numbers.marker(index)          # the screen shows the next number; nothing to warn about
        elif not ref:
            issues.error(f'{prefix}: enter a unique sample order reference.', base + '.order_ref')
        elif order_numbers.is_marker(ref):
            issues.error(f'{prefix}: {ref} is reserved for automatic OC numbers; leave the reference empty instead.', base + '.order_ref')
        elif normalize(ref) in seen:
            issues.error(f'{prefix}: duplicate order reference in this workbook.', base + '.order_ref')
        seen.add(normalize(ref))
        if ref and not order_numbers.is_marker(ref) and catalog.order_ref_exists(normalize(ref)):
            issues.error(f'{prefix}: order reference {ref} already exists. Existing orders are not overwritten.', base + '.order_ref')
        factor = _unit_factor(o, prefix, base, issues)
        for key in required or ():
            value = o.get(key)
            if value is None or str(value).strip() == '':
                issues.error(f'{prefix}: {business_fields.DEFAULT.label(key)} is required for every order — enter it.', f'{base}.{key}')
        # A field that is blank in the file stays blank (NULL in Oracle): nothing is filled in for the person. Only a value
        # that is there must make sense.
        dates = {}
        for key in ('ship_date', 'order_date'):
            if not FILLED(o.get(key)):
                continue
            try:
                dates[key] = datetime.strptime(str(o.get(key)).strip()[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
            except ValueError:
                issues.error(f'{prefix}: {key.replace("_", " ")} “{o.get(key)}” is not a date (YYYY-MM-DD).', f'{base}.{key}')
        if len(dates) == 2 and dates['ship_date'] < dates['order_date']:
            issues.warn(f'{prefix}: the ship date is before the order confirmation date.', base + '.ship_date')

        # Quantities first, so header totals are known before any row is planned.
        lines = o.get('lines', [])
        if len(lines) > LIMITS['lines']:
            issues.error(f'{prefix}: at most {LIMITS["lines"]} colour lines can be saved in one order.', base + '.lines')
        prepared = []
        for li, line in enumerate(lines):
            where = f'{prefix}, line {li + 1}'; lpath = f'{base}.lines.{li}'
            sizes = line.get('sizes', []); names = set(); sizedata = []; total = 0
            if len(sizes) > LIMITS['sizes']:
                issues.error(f'{where}: at most {LIMITS["sizes"]} sizes can be saved in one line.', lpath + '.sizes')
            blank = [s for s in sizes if s.get('blank') and (s.get('quantity') is None or str(s.get('quantity')).strip() == '')]
            if blank and len(blank) < len(sizes):
                issues.warn(f'{where}: size{"s" if len(blank) > 1 else ""} {", ".join(str(x.get("size")) for x in blank)} '
                            f'{"are" if len(blank) > 1 else "is"} blank in the file and treated as not ordered (no size row is saved).', lpath + '.sizes')
            else:
                blank = []
            for si, s in enumerate(sizes):
                if any(s is b for b in blank):
                    continue                       # keeps the draft's own index in the paths below
                name = str(s.get('size', '')).strip()
                if normalize(name) in names:
                    issues.error(f'{where}: duplicate size {name}; review before combining quantities.', f'{lpath}.sizes.{si}.size')
                names.add(normalize(name))
                qty = count(s.get('quantity'), f'{where}, size {name}', issues.sink(f'{lpath}.sizes.{si}.quantity'))
                total += qty; sizedata.append((si, s, qty * factor))
            if line.get('declared_total') is not None:
                declared = count(line['declared_total'], where + ' source total', issues.sink(lpath))
                if declared != total:
                    issues.error(f'{where}: size total {total} differs from source total {declared}.', lpath + '.sizes')
            prepared.append((li, line, where, lpath, total * factor, sizedata))
        order_pieces = sum(p[4] for p in prepared)
        _check_packs(o, prepared, factor, prefix, base, issues)

        orderkey = f'o{index + 1}'
        octx = dict(order=dict(o, order_ref=ref, **{k: dates.get(k) for k in ('ship_date', 'order_date')}),
                    calc=dict(pieces=order_pieces), self=orderkey, keys={}, ops={})
        head = evaluate('ORDMAS', octx, prefix, base, o.get('provenance', {}))
        head.update(_mapped_values('ORDMAS', (o.get('oracle_columns') or {}).get('ORDMAS'), head, registry))
        add(orderkey, 'ORDMAS', head, provenance=o.get('provenance', {}), where=prefix, path=base)
        keys = {'ORDMAS': orderkey}; ops = {'ORDMAS': head}
        subnos = {}
        for li, line, where, lpath, pieces, sizedata in prepared:
            linekey = f'{orderkey}l{li + 1}'; subno = f'{ref}:{li + 1}'; subnos[li + 1] = (subno, pieces)
            lctx = dict(octx, line=line, calc=dict(subno=subno, pieces=pieces, row=li + 1), self=linekey, keys=keys, ops=ops)
            values = evaluate('ORDSTYLE', lctx, where, lpath, line.get('provenance', {}))
            values.update(_mapped_values('ORDSTYLE', line.get('oracle_columns'), values, registry))
            add(linekey, 'ORDSTYLE', values, provenance=line.get('provenance'), where=where, path=lpath)
            totals['lines'] += 1
            for si, s, qty in sizedata:
                sizekey = f'{linekey}s{si + 1}'
                sctx = dict(lctx, size=s, calc=dict(subno=subno, pieces=qty, row=si + 1), self=sizekey,
                            keys=dict(keys, ORDSTYLE=linekey), ops=dict(ops, ORDSTYLE=values))
                vals = evaluate('ORDSIZE', sctx, where, f'{lpath}.sizes.{si}', {'size': s.get('size_source')})
                add(sizekey, 'ORDSIZE', vals, provenance=s.get('source'), where=f'{where}, size {s.get("size", "")}', path=f'{lpath}.sizes.{si}')
        totals['pieces'] += order_pieces
        _plan_mapped_rows(o, orderkey, prefix, base, tables, registry, insertable, issues, add)

        bom = dict(octx=octx, keys=keys, ops=ops, subnos=subnos, order_pieces=order_pieces, prefix=prefix, base=base,
                   orderkey=orderkey, ref=ref, tables=tables, writable=writable, issues=issues, evaluate=evaluate,
                   add=add, totals=totals, reference_only=reference_only, derived=derived)
        _plan_fabrics(o, **bom)
        _plan_accessories(o, **bom)

    _check_columns(operations, registry, issues, tables)
    totals['fabric_kg'] = plain(q(totals['fabric_kg']))
    not_saved = destination(orders, mapping)
    conversions = _conversions(orders)
    result = dict(operations=operations, errors=issues.messages('error'), warnings=issues.messages('warning'),
                  issues=_unique(issues.items), totals=totals,
                  reference_only=[dict(table=t, business=tables[t]['business'], rows=n,
                                       reason='Mapping not yet verified for this table — shown for review, not saved.')
                                  for t, n in reference_only.items()],
                  skipped_columns={t: c for t, c in skipped.items() if c},
                  not_saved=not_saved, conversions=conversions,
                  live=catalog.live, schema_fingerprint=digest(registry), derived=derived, mapping_fingerprint=fingerprint(mapping),
                  columns=_column_meta(mapping))
    result['hash'] = digest(result)
    return result


# Business fields of an order and where each part of an order lives in the draft (field path scope → draft keys).
LINE_FIELDS = {'label': 'Line label', 'color': 'Colour / combo'}
SIZE_FIELDS = {'size': 'Size', 'quantity': 'Quantity'}
SECTIONS = {'fabrics': ('ORDFAB', 'Fabric rows'), 'accessories': ('ORDACC', 'Trims / accessory rows')}
YARNS = ('ORDYARN', 'Yarn split rows')
FILLED = lambda v: v is not None and str(v).strip() != ''      # noqa: E731


def _written(mapping):
    """{'order.buyer': (table, column, status)} for every business field a mapped column reads, and the status of the
    column and its table together (both must be verified for the value to be written)."""
    out = {}
    for table, spec in mapping['tables'].items():
        for col, c in spec['columns'].items():
            field = c.get('field') or ''
            if field == 'calc.pieces' and spec.get('level') == 'size':
                field = 'size.quantity'           # each size quantity is written as pieces (quantity × pieces per unit)
            if not field or field.startswith('calc.'):
                continue
            ok = spec['status'] == 'verified' and c['status'] == 'verified'
            prev = out.get(field)
            if not prev or (ok and prev[2] != 'verified'):
                out[field] = (table, col, 'verified' if ok else 'proposed')
    return out


def field_destinations(mapping):
    """Where each order field goes, for badges on the review screen before any preview: {field: {kept, column}} for the
    fields that do NOT reach Oracle (kept 'unverified' or 'poppys'); fields that are saved are left out."""
    from .. import fields as business_fields
    written = _written(mapping); out = {}
    for f in business_fields.DEFAULTS:
        key = f['key']
        if f['where'] != 'header' or key == 'qty_unit':
            continue
        target = written.get(f'order.{key}')
        if target and target[2] == 'verified':
            continue
        out[key] = dict(kept='unverified' if target else 'poppys', column=f'{target[0]}.{target[1]}' if target else None)
    for key in LINE_FIELDS:
        target = written.get(f'line.{key}')
        if not (target and target[2] == 'verified'):
            out[f'line.{key}'] = dict(kept='unverified' if target else 'poppys', column=f'{target[0]}.{target[1]}' if target else None)
    return out


def destination(orders, mapping):
    """Every value the person can see that will NOT reach Oracle, and why (audit MAPPING_AND_ORACLE_WRITE §5.1:
    Exporter, Season, Department… were typed and never saved, and no screen said so). One entry per order and field:
    {order, path, field, label, values, count, reason, kept, column}. kept is 'poppys' (kept only in the POPPYS import
    record) for values Oracle has no place for, 'unverified' when a column exists but is not confirmed yet."""
    from .. import fields as business_fields
    written = _written(mapping); out = []

    def note(index, path, field, label, values, scope_field):
        values = [v for v in values if FILLED(v)]
        if not values:
            return
        target = written.get(scope_field)
        if target and target[2] == 'verified':
            return
        if target:
            reason = 'not saved to Oracle yet — an administrator still has to confirm where it goes (Setup → Write mapping).'
        else:
            reason = 'not saved to Oracle — the Oracle order has no field for it. POPPYS keeps it with this import.'
        shown = list(dict.fromkeys(str(v) for v in values))
        out.append(dict(order=index, path=path, field=field, label=label, values=shown[:5], count=len(values), reason=reason,
                        kept='unverified' if target else 'poppys', column=f'{target[0]}.{target[1]}' if target else None))

    for index, o in enumerate(orders):
        base = f'orders.{index}'
        for f in business_fields.DEFAULTS:
            key = f['key']
            if f['where'] != 'header' or key == 'qty_unit':
                continue
            note(index, f'{base}.{key}', key, f['label'], [o.get(key)], f'order.{key}')
        for key, label in LINE_FIELDS.items():
            note(index, f'{base}.lines', key, label, [l.get(key) for l in o.get('lines') or []], f'line.{key}')
        for key, label in SIZE_FIELDS.items():
            note(index, f'{base}.lines', key, label, [s.get(key) for l in o.get('lines') or [] for s in l.get('sizes') or []], f'size.{key}')
        for section, (table, label) in SECTIONS.items():
            rows = o.get(section) or []
            spec = mapping['tables'].get(table)
            if rows and (not spec or spec['status'] != 'verified'):
                out.append(dict(order=index, path=f'{base}.{section}', field=section, label=label, values=[], count=len(rows),
                                reason=('not saved to Oracle yet — checked and shown here only, until an administrator confirms '
                                        'where they go.') if spec else 'not saved to Oracle — Oracle has no table for them.',
                                kept='unverified' if spec else 'poppys', column=table if spec else None))
        yarns = [y for f in o.get('fabrics') or [] for y in f.get('yarns') or []]
        spec = mapping['tables'].get(YARNS[0])
        if yarns and (not spec or spec['status'] != 'verified'):
            out.append(dict(order=index, path=f'{base}.fabrics', field='yarns', label=YARNS[1], values=[], count=len(yarns),
                            reason='not saved to Oracle yet — checked and shown here only, until an administrator confirms where they go.',
                            kept='unverified' if spec else 'poppys', column=YARNS[0] if spec else None))
        for x in o.get('other_fields') or []:
            if FILLED(x.get('value')):
                out.append(dict(order=index, path=f'{base}.other_fields', field='other', label=x['label'], values=[str(x['value'])],
                                count=1, cell=x.get('cell'), kept='poppys', column=None,
                                reason='not saved to Oracle — POPPYS has no order field for it yet. POPPYS keeps it with this import.'))
    return out


def _conversions(orders):
    """Quantity conversions the plan applies (packs to pieces), with the original figures and who decided the unit,
    so the approver sees "120 packs × 3 = 360 pieces (unit chosen by a person)" instead of a silent 360."""
    out = []
    for index, o in enumerate(orders):
        if str(o.get('qty_unit') or '').upper() != 'PACK':
            continue
        try:
            per = int(Decimal(str(o.get('pieces_per_unit'))))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if per < 2:
            continue
        source = sum(int(Decimal(str(s.get('quantity')))) for l in o.get('lines') or [] for s in l.get('sizes') or []
                     if FILLED(s.get('quantity')) and _whole(s.get('quantity')))
        prov = (o.get('provenance') or {}).get('pieces_per_unit') or (o.get('provenance') or {}).get('qty_unit') or {}
        decided = {'person': 'chosen by a person', 'template': 'remembered from this buyer’s saved layout',
                   'file': 'stated in the file'}.get(prov.get('reason'), 'entered on the review screen')
        out.append(dict(order=index, path=f'orders.{index}.qty_unit', unit='PACK', pieces_per_unit=per, source_quantity=source,
                        pieces=source * per, decided=decided, derivation=prov.get('derivation') or prov.get('evidence'),
                        text=f'{source:,} packs × {per} pieces per pack = {source * per:,} pieces ({decided}).'))
    return out


def _whole(v):
    try:
        d = Decimal(str(v))
        return d.is_finite() and d == d.to_integral_value()
    except InvalidOperation:
        return False


def _column_meta(mapping):
    meta = {t: {c: dict(label=s.get('label'), field=s.get('field'), master=s.get('master'), status=s['status'])
                for c, s in spec['columns'].items()} for t, spec in mapping['tables'].items()}
    for role, m in mapping['masters'].items():
        meta.setdefault(m['table'], {}).update({m['key']: dict(label=m['business'] + ' ID', master=role),
                                                m['label']: dict(label=m['business'], master=role)})
    return meta


def _unique(items):
    seen = set(); out = []
    for i in items:
        key = (i['severity'], i['message'])
        if key not in seen:
            seen.add(key); out.append(i)
    return out


def _mapped_values(table, given, written, registry):
    """Columns the person mapped from the Excel file that the standard order does not fill (backend/erp/excel_map.py).
    A column the order already writes keeps the order's value; a blank cell adds nothing."""
    out = {}
    for col, x in (given or {}).items():
        v = x.get('value') if isinstance(x, dict) else x
        if col in written or col not in registry.get(table, {}) or not FILLED(v):
            continue
        out[col] = v.strip() if isinstance(v, str) else plain(v)
    return out


def _plan_mapped_rows(o, orderkey, prefix, base, tables, registry, insertable, issues, add):
    """Rows of the order's row tables (fabric, yarn, trims) the person mapped from Excel rows: one insert per row, with
    the record ID, the link to the order and the row number set by POPPYS and every other value from the file."""
    from .excel_map import ROW_TABLES
    for table, records in (o.get('oracle_rows') or {}).items():
        spec = tables.get(table) or {}
        if table not in ROW_TABLES or not spec:
            issues.error(f'{prefix}: rows cannot be added to {table} from POPPYS.', f'{base}.oracle_rows')
            continue
        if table not in registry:
            issues.error(f'{table}: table is not visible to POPPYS AI. Check grants with scripts/grant_bom_tables.py.', f'{base}.oracle_rows')
            continue
        if table not in insertable:
            issues.error(f'{table}: POPPYS AI has no INSERT permission. Run scripts/grant_bom_tables.py.', f'{base}.oracle_rows')
            continue
        parent = next((c for c, x in spec['columns'].items() if x.get('parent') == 'ORDMAS'), 'ORDMASID')
        rowcol = f'{table}ROW' if f'{table}ROW' in registry[table] else None
        for i, rec in enumerate(records[:LIMITS['lines'] * 10]):
            key = f'{orderkey}{table.lower()}{i + 1}'
            values = {spec['key']: '$' + key, parent: '$' + orderkey, **({rowcol: i + 1} if rowcol else {})}
            values.update(_mapped_values(table, rec, values, registry))
            add(key, table, values, provenance={c: x for c, x in rec.items() if isinstance(x, dict)},
                where=f'{prefix}, {spec.get("business", table).lower()} row {i + 1}', path=f'{base}.oracle_rows.{table}.{i}')
        if len(records) > LIMITS['lines'] * 10:
            issues.error(f'{prefix}: at most {LIMITS["lines"] * 10} {table} rows can be saved in one order.', f'{base}.oracle_rows')


def _check_packs(o, prepared, factor, prefix, base, issues):
    packs = o.get('packs') or {}
    try:
        p, pcs = int(packs.get('packs') or 0), int(packs.get('pieces') or 0)
    except (TypeError, ValueError):
        return
    if not p or not pcs:
        return
    if pcs % p:
        issues.warn(f'{prefix}: workbook pieces {pcs} is not a whole multiple of packs {p}. Check the pack composition.', base + '.packs')
        return
    per_pack = pcs // p
    workbook_lines = o.get('order_lines') or []
    workbook_total = sum(decimal(s.get('quantity'), '', [], required=False) or 0
                         for l in workbook_lines for s in l.get('sizes', []))
    if workbook_lines and workbook_total != pcs:
        issues.warn(f'{prefix}: order size breakdown totals {workbook_total} pieces but the header says {pcs} pieces ({p} packs × {per_pack}).', base + '.packs')
    if workbook_lines and len(workbook_lines) != per_pack:
        issues.warn(f'{prefix}: {p} packs → {pcs} pieces means {per_pack} pieces per pack, but {len(workbook_lines)} garment lines were found.', base + '.packs')


def _resolve_line(row, subnos, ref):
    line = row.get('line')
    try:
        line = int(line) if line not in (None, '') else None
    except (TypeError, ValueError):
        line = None
    return (line, *subnos[line]) if line in subnos else (None, ref, None)


def _plan_fabrics(o, octx, keys, ops, subnos, order_pieces, prefix, base, orderkey, ref,
                  tables, writable, issues, evaluate, add, totals, reference_only, derived):
    fabrics = o.get('fabrics') or []
    if not fabrics or 'ORDFAB' not in tables:
        return
    strict = 'ORDFAB' in writable
    sev = 'error' if strict else 'warning'
    if len(fabrics) > LIMITS['fabrics']:
        issues.error(f'{prefix}: more than {LIMITS["fabrics"]} fabric rows.', base + '.fabrics')
    for fi, f in enumerate(fabrics):
        where = f'{prefix}, fabric {fi + 1} ({f.get("component") or f.get("portion") or "row"})'
        fpath = f'{base}.fabrics.{fi}'
        sink = issues.sink(fpath, sev)
        line, subno, pieces = _resolve_line(f, subnos, ref)
        if line is None:
            issues.add(sev, f'{where}: choose which garment line this fabric belongs to.', fpath + '.line')
            pieces = 0
        yy = decimal(f.get('yy'), where + ' consumption (grams per piece)', issues.sink(fpath + '.yy', sev), positive=True)
        rej = decimal(f.get('rejection'), where + ' rejection allowance', sink, required=False)
        if rej is None:
            rej = Decimal(1)
            issues.warn(f'{where}: no rejection allowance found; 0% assumed.', fpath + '.rejection')
        elif rej < 1 or rej > 2:
            issues.add(sev, f'{where}: rejection allowance {rej} must be a factor between 1 and 2 (e.g. 1.03 for 3%).', fpath + '.rejection')
        # Workbook arithmetic is checked, never silently trusted.
        wq = decimal(f.get('order_qty'), '', [], required=False); wr = decimal(f.get('requirement'), '', [], required=False)
        if yy is not None and wq is not None and wr is not None and abs(wq * yy / 1000 - wr) > Decimal('0.01'):
            issues.warn(f'{where}: workbook requirement {wr} kg differs from {wq} × {yy} g = {q(wq * yy / 1000)} kg.', fpath + '.requirement')
        wt = decimal(f.get('total'), '', [], required=False)
        if wr is not None and wt is not None and abs(wr * rej - wt) > Decimal('0.01'):
            issues.warn(f'{where}: workbook total {wt} kg differs from {wr} × {rej} = {q(wr * rej)} kg.', fpath + '.total')
        total_kg = q((pieces or 0) * (yy or 0) / 1000 * rej)
        yarns = f.get('yarns') or []
        if strict and 'ORDYARN' in tables and not yarns:
            issues.add('error' if 'ORDYARN' in writable else 'warning', f'{where}: add the yarn split (count, colour and %).', fpath + '.yarns')
        pct_total = Decimal(0); yarn_rows = []
        for yi, y in enumerate(yarns[:LIMITS['yarns'] + 1]):
            pct = decimal(y.get('percent'), f'{where}, yarn {yi + 1} percentage', issues.sink(f'{fpath}.yarns.{yi}.percent', sev), positive=True)
            pct_total += pct or 0
            yarn_rows.append((yi, y, q(total_kg * (pct or 0) / 100)))
        if yarns and pct_total != 100:
            issues.add(sev, f'{where}: yarn percentages total {plain(pct_total)}%; they must total 100%.', fpath + '.yarns')
        _composition(f, yarns, where, fpath, issues)
        if yarn_rows and pct_total == 100:
            # Assign the rounding residual to the largest share so yarn kg reconcile exactly with fabric kg.
            residual = total_kg - sum(r[2] for r in yarn_rows)
            if residual:
                big = max(range(len(yarn_rows)), key=lambda i: yarn_rows[i][2])
                yi, y, kg = yarn_rows[big]; yarn_rows[big] = (yi, y, kg + residual)
        yarn_kg = sum((r[2] for r in yarn_rows), Decimal(0))
        fabkey = f'{orderkey}f{fi + 1}'; ydet = str(fi + 1)
        totals['fabrics'] += 1; totals['fabric_kg'] += total_kg
        derived[fpath] = dict(line=line, pieces=pieces, total_kg=plain(total_kg), subno=subno,
                            formula=f'{pieces} pcs × {plain(yy)} g ÷ 1000 × {plain(rej)} = {plain(total_kg)} kg')
        fctx = dict(octx, fabric=f, calc=dict(row=fi + 1, subno=subno, pieces=pieces, total_kg=total_kg,
                    ydet=ydet, yarn_kg=yarn_kg, yarn_percent=pct_total), self=fabkey, keys=keys, ops=ops)
        if 'ORDFAB' in writable:
            values = evaluate('ORDFAB', fctx, where, fpath, f.get('source'))
            add(fabkey, 'ORDFAB', values, provenance=f.get('source'), where=where, path=fpath)
        else:
            reference_only['ORDFAB'] = reference_only.get('ORDFAB', 0) + 1
        for yi, y, kg in yarn_rows:
            totals['yarns'] += 1
            derived[f'{fpath}.yarns.{yi}'] = dict(kg=plain(kg))
            if 'ORDYARN' not in tables:
                continue
            if 'ORDYARN' in writable:
                ykey = f'{fabkey}y{yi + 1}'
                yctx = dict(fctx, yarn=y, calc=dict(row=yi + 1, subno=subno, ydet=ydet, kg=kg), self=ykey,
                            keys=dict(keys, ORDFAB=fabkey))
                add(ykey, 'ORDYARN', evaluate('ORDYARN', yctx, f'{where}, yarn {yi + 1}', f'{fpath}.yarns.{yi}'),
                    provenance=y.get('source'), where=f'{where}, yarn {yi + 1}', path=f'{fpath}.yarns.{yi}')
            else:
                reference_only['ORDYARN'] = reference_only.get('ORDYARN', 0) + 1


def _composition(f, yarns, where, path, issues):
    content = str(f.get('content') or '').lower()
    declared = {x for x in CONTENT_FIBRES if x in content}
    used = {x for y in yarns for x in CONTENT_FIBRES if x in f'{y.get("count", "")} {y.get("description", "")}'.lower()}
    extra = used - declared
    if declared and extra:
        issues.warn(f'{where}: fabric content says {f.get("content")} but the yarn split includes {", ".join(sorted(extra))}. Confirm the composition.', path + '.yarns')


def size_key(name):
    """Canonical size spelling: "Up To 1Mth" == "Up To 1M", "1.5-2 Yrs" == "1.5-2 Y"."""
    n = re.sub(r'\s+', '', str(name or '').lower())
    n = re.sub(r'(months?|mths?)$', 'm', n)
    n = re.sub(r'(years?|yrs?)$', 'y', n)
    return n


def _plan_accessories(o, octx, keys, ops, subnos, order_pieces, prefix, base, orderkey, ref,
                      tables, writable, issues, evaluate, add, totals, reference_only, derived):
    rows = o.get('accessories') or []
    if not rows or 'ORDACC' not in tables:
        return
    strict = 'ORDACC' in writable; sev = 'error' if strict else 'warning'
    if len(rows) > LIMITS['accessories']:
        issues.error(f'{prefix}: more than {LIMITS["accessories"]} accessory rows.', base + '.accessories')
    workbook = o.get('workbook_pieces')
    order_sizes = {size_key(sz.get('size')): str(sz.get('size')).strip() for l in o.get('lines', []) for sz in l.get('sizes', [])}
    aligned = set()
    ratio = None
    if workbook:
        ratio = Decimal(order_pieces) / Decimal(workbook)
        if ratio != 1:
            issues.warn(f'{prefix}: accessory quantities are scaled from the workbook’s {workbook} pieces to the {order_pieces} pieces being saved (× {q(ratio, 4)}).', base + '.accessories')
    for ai, a in enumerate(rows):
        where = f'{prefix}, accessory {ai + 1} ({a.get("name") or a.get("category") or "row"})'
        apath = f'{base}.accessories.{ai}'
        line, subno, _ = _resolve_line(a, subnos, ref)
        total = decimal(a.get('total'), where + ' quantity', issues.sink(apath + '.total', sev))
        if total is None:
            continue
        if total == 0:
            issues.warn(f'{where}: quantity is zero in the workbook; this row will not be saved.', apath + '.total')
            continue
        quantity = q(total * ratio) if ratio is not None else total
        if ratio is None:
            issues.warn(f'{where}: workbook order quantity unknown; quantity {total} used as entered.', apath + '.total')
        if not str(a.get('uom') or '').strip():
            issues.add(sev, f'{where}: enter the unit (for example PCS or MTR).', apath + '.uom')
        derived[apath] = dict(quantity=plain(quantity), line=line, subno=subno,
                              formula=f'{plain(total)} × {plain(q(ratio, 4))} = {plain(quantity)}' if ratio not in (None, 1) else None)
        totals['accessories'] += 1
        size = None if normalize(a.get('size')).startswith('ALL') else str(a.get('size') or '').strip() or None
        if size and size not in order_sizes.values() and size_key(size) in order_sizes:
            aligned.add((size, order_sizes[size_key(size)])); size = order_sizes[size_key(size)]
        acc = dict(a, size=size)
        actx = dict(octx, acc=acc, calc=dict(row=ai + 1, subno=subno, quantity=quantity), self=f'{orderkey}a{ai + 1}', keys=keys, ops=ops)
        if strict:
            add(f'{orderkey}a{ai + 1}', 'ORDACC', evaluate('ORDACC', actx, where, apath, a.get('source')),
                provenance=a.get('source'), where=where, path=apath)
        else:
            reference_only['ORDACC'] = reference_only.get('ORDACC', 0) + 1
    if aligned:
        issues.warn(f'{prefix}: accessory sizes matched to the order sizes: ' +
                    ', '.join(f'“{a}” → “{b}”' for a, b in sorted(aligned)) + '.', base + '.accessories')


def _check_columns(operations, registry, issues, tables):
    # Fail closed against actual Oracle lengths/precision/scale/nullability rather than silently truncate.
    too_long = {}
    for op in operations:
        if op['action'] == 'reuse':
            continue
        table = op['table']; vals = op['values']
        if table not in registry:
            issues.error(f'{table}: table absent in live database.')
            continue
        for col, meta in registry[table].items():
            if meta['nullable'] == 'N' and vals.get(col) is None:
                issues.error(f'{table}.{col}: mandatory database value missing.')
        for col, value in vals.items():
            meta = registry[table].get(col)
            if meta is None:
                issues.error(f'{table}.{col}: column absent in live database.')
                continue
            if value is None or (isinstance(value, str) and value.startswith('$')):
                continue
            if meta['type'] in ('VARCHAR2', 'CHAR') and len(str(value).encode('utf-8')) > meta['length']:
                field = (tables.get(table, {}).get('columns', {}).get(col) or {}).get('field', '')
                scope, _, name = field.partition('.')
                path = f'{op["path"]}.{name}' if op.get('path') and scope != 'calc' and name else op.get('path')
                too_long.setdefault((table, col, str(value)), []).append((op['where'] or table, path))
            if meta['type'] == 'NUMBER':
                try:
                    d = Decimal(str(value))
                    scale = meta['scale'] or 0
                    if meta['precision'] and abs(d) >= Decimal(10) ** (meta['precision'] - scale):
                        issues.error(f'{table}.{col}: value exceeds numeric precision.')
                    if meta['scale'] is not None and d != d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP):
                        issues.error(f'{table}.{col}: {value} has more than {scale} decimal places.')
                except InvalidOperation:
                    issues.error(f'{table}.{col}: requires a number.')
            if meta['type'] == 'DATE':
                try:
                    datetime.strptime(str(value), '%Y-%m-%d')
                except ValueError:
                    issues.error(f'{table}.{col}: requires a YYYY-MM-DD date.')
    for (table, col, value), where in too_long.items():
        size = len(value.encode('utf-8')); limit = registry[table][col]['length']
        rows = f'{where[0][0]}' if len(where) == 1 else f'{where[0][0]} and {len(where) - 1} more rows'
        issues.error(f'{rows}: “{value}” is {size} characters but Oracle {table}.{col} allows {limit}. Shorten it.', where[0][1])
