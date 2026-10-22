"""Make synthetic buyer orders (TEST data, fictional buyers) in the merchandising BOM layout the client uses:
a BUYER / SEASON / ITEM … header, OCN | QTY(IN PACKS) | QTY(IN PIECES) | Price in USD, order / sample / summary size
blocks, FABRIC REQUIREMENT, trims tables and, for some buyers, a PRINTING table. Totals are real formulas with their
calculated values stored, exactly like a workbook saved by Excel.

  python scripts/make_test_orders.py                 -> samples/Test_A_with_printing.xlsx, samples/Test_B_no_printing.xlsx
  python scripts/make_test_orders.py --suffix 0412   -> the same orders with OC numbers ending -0412 (to upload again)
"""
from __future__ import annotations
import argparse
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
THIN = Side(style='thin', color='9AA5A0')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill('solid', fgColor='DDEFE6')
BOLD = Font(bold=True)

ORDERS = {
    'A': dict(file='Test_A_with_printing.xlsx', buyer='BLUE HERON KIDS LTD', season='AW-27', style='BH-7731', description='BABY 2PK ORGANIC ROMPER',
              merch='R. DEVIKA', destination='UK - SEA', ocn='BH-OC-27311', contract='BH-PO-88214', price=4.85, pack=2,
              bom_date=date(2026, 11, 3), fob=date(2027, 3, 26), fab_ih=date(2027, 1, 12),
              header_extra=[('SHIP MODE', 'SEA'), ('PAYMENT TERMS', 'TT 30 DAYS'), ('PORT OF LOADING', 'CHENNAI'), ('CURRENCY', 'USD')],
              sizes=['0-3M', '3-6M', '6-9M', '9-12M', '12-18M', '18-24M'],
              lines=[('GARMENT-1 ROMPER', 'OATMEAL MELANGE', [60, 120, 180, 180, 150, 90]), ('GARMENT-2 ROMPER', 'SAGE GREEN', [60, 120, 180, 180, 150, 90])],
              fabric=[('Garment - 1 BODY', 'OATMEAL MELANGE', 'OATMEAL', 'Single Jersey', 180, '100% Organic Cotton', "30'S", '30 Dia', 0.21),
                      ('Garment - 1 RIB', 'OATMEAL MELANGE', 'OATMEAL', '1x1 Rib', 200, '95% Cotton 5% Elastane', "40'S", '28 Dia', 0.03),
                      ('Garment - 2 BODY', 'SAGE GREEN', 'SAGE', 'Single Jersey', 180, '100% Organic Cotton', "30'S", '30 Dia', 0.21),
                      ('Garment - 2 RIB', 'SAGE GREEN', 'SAGE', '1x1 Rib', 200, '95% Cotton 5% Elastane', "40'S", '28 Dia', 0.03)],
              printing=[('GARMENT-1', 'OATMEAL MELANGE', 'Water based print', 'Front chest', 2, '8 x 6 cm', 'SRI PRINTS'),
                        ('GARMENT-2', 'SAGE GREEN', 'Plastisol print', 'Back neck', 1, '4 x 2 cm', 'SRI PRINTS')]),
    'B': dict(file='Test_B_no_printing.xlsx', buyer='MAPLE & MOSS BABY CO', season='SS-28', style='MM-5520', description='TODDLER 3PK RIB LEGGING',
              merch='K. ARAVIND', destination='GERMANY - AIR', ocn='MM-OC-55207', contract='MM-PO-44031', price=3.15, pack=3,
              bom_date=date(2026, 12, 8), fob=date(2027, 4, 30), fab_ih=date(2027, 2, 15),
              header_extra=[('SHIP MODE', 'AIR'), ('PAYMENT TERMS', 'LC 60 DAYS')],
              sizes=['2T', '3T', '4T', '5T'],
              lines=[('GARMENT-1 LEGGING', 'NAVY', [75, 150, 150, 75]), ('GARMENT-2 LEGGING', 'HEATHER GREY', [75, 150, 150, 75]),
                     ('GARMENT-3 LEGGING', 'BLUSH PINK', [75, 150, 150, 75])],
              fabric=[('Garment - 1 BODY', 'NAVY', 'NAVY', '2x2 Rib', 220, '95% Cotton 5% Elastane', "30'S", '32 Dia', 0.18),
                      ('Garment - 2 BODY', 'HEATHER GREY', 'GREY MEL', '2x2 Rib', 220, '95% Cotton 5% Elastane', "30'S", '32 Dia', 0.18),
                      ('Garment - 3 BODY', 'BLUSH PINK', 'BLUSH', '2x2 Rib', 220, '95% Cotton 5% Elastane', "30'S", '32 Dia', 0.18)],
              printing=None),
}


def build(spec, suffix=''):
    wb = Workbook(); ws = wb.active; ws.title = spec['style']
    formulas = {}   # cell -> value Excel would have calculated

    def put(cell, value, bold=False, fill=None, box=True):
        ws[cell] = value
        if bold:
            ws[cell].font = BOLD
        if fill:
            ws[cell].fill = fill
        if box:
            ws[cell].border = BOX
        return ws[cell]

    def formula(cell, text, value):
        put(cell, text); formulas[cell] = value

    ocn = spec['ocn'] + (f'-{suffix}' if suffix else '')
    sizes, lines = spec['sizes'], spec['lines']
    pieces = sum(sum(q) for _, _, q in lines)
    last = 5 + len(sizes)            # column of the Total
    end_col = get_column_letter(max(last, 15))
    put('B1', f"{spec['buyer']} - BOM", bold=True, box=False); ws.merge_cells(f'B1:{end_col}1')
    ws['B1'].font = Font(bold=True, size=14); ws['B1'].alignment = Alignment(horizontal='center')
    header = [('BUYER', spec['buyer'], 'BOM DATE', spec['bom_date']), ('SEASON', spec['season'], 'FOB DATE', spec['fob']),
              ('ITEM', spec['style'], 'FAB IH DATE', spec['fab_ih']), ('DESCRIPTION', spec['description'], 'CONTRACT', spec['contract']),
              ('MERCHANDISER', spec['merch'], 'DESTINATION', spec['destination'])]
    for i, (l1, v1, l2, v2) in enumerate(header, 2):
        put(f'B{i}', l1, bold=True); put(f'C{i}', v1); put(f'D{i}', l2, bold=True); put(f'E{i}', v2)
        if isinstance(v2, date):
            ws[f'E{i}'].number_format = 'dd-mmm-yy'
        ws.merge_cells(f'E{i}:G{i}')
    for i, (label, value) in enumerate(spec['header_extra'], 2):
        put(f'I{i}', label, bold=True); put(f'J{i}', value)
    put('B8', 'OCN', bold=True, fill=HEAD); put('C8', 'QTY(IN PACKS)', bold=True, fill=HEAD); put('D8', 'QTY(IN PIECES)', bold=True, fill=HEAD)
    put('G8', 'Price in USD', bold=True, fill=HEAD)
    put('B9', ocn); put('C9', pieces // spec['pack'])
    formula('D9', f'=C9*{spec["pack"]}', pieces); put('G9', spec['price']); ws['G9'].number_format = '0.00'

    def block(top, role, factor):
        put(f'B{top}', 'Garment', bold=True, fill=HEAD); put(f'C{top}', 'Description', bold=True, fill=HEAD); put(f'D{top}', 'CONTRACT', bold=True, fill=HEAD)
        for j, s in enumerate(sizes):
            put(f'{get_column_letter(5 + j)}{top}', s, bold=True, fill=HEAD)
        put(f'{get_column_letter(last)}{top}', 'Total', bold=True, fill=HEAD)
        for k, (garment, colour, qty) in enumerate(lines, 1):
            r = top + k
            put(f'B{r}', garment); put(f'C{r}', colour)
            for j, q in enumerate(qty):
                put(f'{get_column_letter(5 + j)}{r}', q * factor)
            formula(f'{get_column_letter(last)}{r}', f'=SUM(E{r}:{get_column_letter(last - 1)}{r})', sum(qty) * factor)
        put(f'D{top + 1}', role)
        ws.merge_cells(f'D{top + 1}:D{top + len(lines)}')
        r = top + len(lines) + 1
        put(f'B{r}', 'Total', bold=True); ws.merge_cells(f'B{r}:C{r}')
        for j in range(len(sizes) + 1):
            col = get_column_letter(5 + j)
            formula(f'{col}{r}', f'=SUM({col}{top + 1}:{col}{top + len(lines)})', sum(q[j] for _, _, q in lines) * factor if j < len(sizes) else pieces * factor)
        return r + 2

    row = block(11, f"{spec['contract']} DIR", 1)
    row = block(row, 'SAMPLE', 0)
    row = block(row, 'SUMMARY', 1)
    # fabric requirement
    put(f'B{row}', 'FABRIC REQUIREMENT', bold=True, box=False)
    heads = ['Garment', 'Colour Description', 'Base Colour', 'Fabric Quality', 'Gsm', 'Content', 'Count', 'Dia', 'Order Qty', 'YY', 'Requirment', 'Rej 3%', 'Total Qty']
    for j, h in enumerate(heads):
        put(f'{get_column_letter(2 + j)}{row + 1}', h, bold=True, fill=HEAD)
    per_line = {colour: sum(q) for _, colour, q in lines}
    for k, (garment, colour, base, quality, gsm, content, count, dia, yy) in enumerate(spec['fabric'], row + 2):
        qty = per_line.get(colour, 0)
        for j, v in enumerate([garment, colour, base, quality, gsm, content, count, dia, qty, yy]):
            put(f'{get_column_letter(2 + j)}{k}', v)
        formula(f'L{k}', f'=J{k}*K{k}', round(qty * yy, 4)); put(f'M{k}', 1.03); formula(f'N{k}', f'=L{k}*M{k}', round(qty * yy * 1.03, 4))
    row = row + 2 + len(spec['fabric']) + 1
    if spec['printing']:
        put(f'B{row}', 'PRINTING', bold=True, box=False)
        heads = ['Garment', 'Colour', 'Print Type', 'Placement', 'No of Colours', 'Print Size', 'Supplier', 'Order Qty', 'Rate', 'Remarks']
        for j, h in enumerate(heads):
            put(f'{get_column_letter(2 + j)}{row + 1}', h, bold=True, fill=HEAD)
        for k, (garment, colour, kind, place, ncol, size, supplier) in enumerate(spec['printing'], row + 2):
            for j, v in enumerate([garment, colour, kind, place, ncol, size, supplier, per_line.get(colour, 0), 0.12, 'Approve strike-off first']):
                put(f'{get_column_letter(2 + j)}{k}', v)
        row = row + 2 + len(spec['printing']) + 1
    trims = [('CARE LABEL', 'CARE-' + spec['style'], 'MTR', 1), ('MAIN LABEL', 'ML-' + spec['style'], 'PCS', 1), ('POLY BAG', 'PB-25X30', 'PCS', 1 / spec['pack']),
             ('CARTON BOX', '5 PLY 60X40X40', 'PCS', 1 / 120)]
    for title, code, uom, yy in trims:
        put(f'B{row}', title, bold=True, box=False)
        for j, h in enumerate(['Garment', 'CODE', 'Size', 'Supplier', 'Order Qty', 'YY', 'Required Qty', 'Rejection', 'Req In Single']):
            put(f'{get_column_letter(2 + j)}{row + 1}', h, bold=True, fill=HEAD)
        r = row + 2
        put(f'B{r}', 'ALL GARMENTS'); put(f'C{r}', code); put(f'D{r}', 'ALL SIZES'); put(f'E{r}', uom); put(f'F{r}', pieces); put(f'G{r}', round(yy, 4))
        formula(f'H{r}', f'=F{r}*G{r}', round(pieces * yy, 4)); put(f'I{r}', 1.03); formula(f'J{r}', f'=H{r}*I{r}', round(pieces * yy * 1.03, 4))
        put(f'D{r + 1}', 'TOTAL', bold=True); formula(f'F{r + 1}', f'=F{r}', pieces)
        row = r + 3
    put(f'B{row}', 'SEWING THREAD', bold=True, box=False)
    for j, h in enumerate(['Garment', 'THREAD COLOR', 'SHADE', 'QUALITY', 'METER', 'Order Qty', 'YY', 'Required Qty']):
        put(f'{get_column_letter(2 + j)}{row + 1}', h, bold=True, fill=HEAD)
    for k, (garment, colour, qty) in enumerate(lines, row + 2):
        put(f'B{k}', garment.split()[0]); put(f'C{k}', colour); put(f'D{k}', 'DTM'); put(f'E{k}', "120'S POLY"); put(f'F{k}', 5000); put(f'G{k}', sum(qty))
        put(f'H{k}', 85); formula(f'I{k}', f'=G{k}*H{k}/5000', round(sum(qty) * 85 / 5000, 4))
    for col in range(2, 17):
        ws.column_dimensions[get_column_letter(col)].width = 16
    out = io.BytesIO(); wb.save(out)
    return cache_formulas(out.getvalue(), formulas, ws.title)


def cache_formulas(content, values, sheet_title):
    """Store each formula's calculated value in the file, the way Excel does when it saves (openpyxl writes formulas only)."""
    src = zipfile.ZipFile(io.BytesIO(content)); out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == 'xl/worksheets/sheet1.xml':
                xml = data.decode('utf-8')
                for cell, value in values.items():
                    xml = re.sub(rf'(<c r="{cell}"[^>]*>)(<f>[^<]*</f>)(<v\s*/>|<v></v>)?', lambda m: f'{m.group(1)}{m.group(2)}<v>{value}</v>', xml, count=1)
                data = xml.encode('utf-8')
            dst.writestr(item, data)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--suffix', default='', help='added to the OC numbers so the same orders can be uploaded again')
    ap.add_argument('--out', default=str(ROOT / 'samples'))
    args = ap.parse_args()
    folder = Path(args.out); folder.mkdir(parents=True, exist_ok=True)
    for key, spec in ORDERS.items():
        name = spec['file'] if not args.suffix else spec['file'].replace('.xlsx', f'_{args.suffix}.xlsx')
        (folder / name).write_bytes(build(spec, args.suffix))
        print(f'{folder / name}  OC {spec["ocn"]}{"-" + args.suffix if args.suffix else ""}  {spec["buyer"]}  printing: {"yes" if spec["printing"] else "no"}')


if __name__ == '__main__':
    main()
