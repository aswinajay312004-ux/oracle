"""Synthetic buyer workbooks (TEST data only) in several layouts."""
from datetime import date
from io import BytesIO
from openpyxl import Workbook


def save(wb):
    out = BytesIO(); wb.save(out); return out.getvalue()


def label_layout(order_no='PK/26-27/S/1320', buyer='ABC TEXTILES', printing=None):
    """`Label | value` header with a vertical size table. Printing is an optional section."""
    wb = Workbook(); ws = wb.active; ws.title = 'Buyer Order'
    ws['A1'] = 'BUYER PURCHASE ORDER'
    ws['A3'] = 'Buyer'; ws['B3'] = buyer
    ws['A4'] = 'Order No'; ws['B4'] = order_no
    ws['A5'] = 'Style No'; ws['B5'] = '0008'
    ws['A6'] = 'Delivery Date'; ws['B6'] = date(2026, 12, 15); ws['B6'].number_format = 'dd-mm-yyyy'
    ws['D3'] = 'Season'; ws['E3'] = 'SS27'
    ws['A9'] = 'Size'; ws['B9'] = 'Quantity'; ws['C9'] = 'Colour'
    for r, (size, qty) in enumerate([('M', 2000), ('L', 3000)], 10):
        ws.cell(r, 1, size); ws.cell(r, 2, qty); ws.cell(r, 3, 'NAVY')
    if printing:
        ws['A14'] = 'Printing ink'; ws['B14'] = printing
    return save(wb)


def horizontal_layout():
    wb = Workbook(); ws = wb.active; ws.title = 'PO'
    ws['A1'] = 'Customer: DEMO BUYER'
    ws['A2'] = 'PO Number'; ws['B2'] = 'PO-7781'
    ws['A4'] = 'Sizes'; ws['B4'] = 'S'; ws['C4'] = 'M'; ws['D4'] = 'L'; ws['E4'] = 'Total'
    ws['B5'] = 10; ws['C5'] = 20; ws['D5'] = 30; ws['E5'] = '=SUM(B5:D5)'
    return save(wb)


def moved_layout():
    """Same labels, different rows: a saved template must not be applied blindly."""
    wb = Workbook(); ws = wb.active; ws.title = 'Buyer Order'
    ws['A2'] = 'Note'; ws['B2'] = 'Moved'
    ws['A8'] = 'Buyer'; ws['B8'] = 'ABC TEXTILES'
    return save(wb)
