from io import BytesIO

from openpyxl import Workbook
import pytest

from backend import workbooks, main


@pytest.mark.parametrize('heading,basis', [('Sample Qty', 'sample'), ('Order Qty', 'unconfirmed'), ('Qty', 'unconfirmed')])
def test_flat_quantity_heading_keeps_sample_meaning(heading, basis, monkeypatch):
    monkeypatch.setenv('POPPYS_INLINE_PARSE', '1')
    wb = Workbook(); ws = wb.active
    ws.append(['Buyer', 'Style No', 'Colour', 'Size', heading])
    ws.append(['TEST BUYER', 'TEST-001', 'BLUE', 'M', 4])
    ws.append(['TEST BUYER', 'TEST-001', 'BLUE', 'L', 6])
    content = BytesIO(); wb.save(content)
    parsed = workbooks.parse(content.getvalue(), filename='sample.xlsx')
    order = parsed['orders'][0]
    assert order['quantity_basis'] == basis
    assert sum(s['quantity'] for line in order['lines'] for s in line['sizes']) == 10
    if basis == 'sample':
        assert order['sample_lines'] == order['lines'] and not order.get('order_lines')
        assert order['provenance']['quantity_basis']['cell'] == 'E1'
        monkeypatch.setattr(main, 'settings', lambda: main.DEFAULT_SETTINGS)
        main._new_order_defaults([order])
        assert order['quantity_basis'] == 'sample'
        assert order['provenance']['quantity_basis']['reason'] == 'file'


@pytest.mark.parametrize('raw,month_first', [('10/01/2026', '12/20/2026'), ('not a date', None)])
def test_company_defaults_do_not_replace_rejected_source_dates(raw, month_first, monkeypatch):
    monkeypatch.setattr(main, 'settings', lambda: main.DEFAULT_SETTINGS)
    order = workbooks.base_order()
    order.update(order_date=raw)
    order['provenance']['order_date'] = {'sheet': 'Order', 'cell': 'B3'}
    workbooks.finish(order, month_first=month_first)
    assert not order['order_date'] and order['provenance']['order_date']['question']
    main._new_order_defaults([order])
    assert not order['order_date']
    assert order['provenance']['order_date']['cell'] == 'B3'


def test_genuinely_missing_date_can_still_use_the_visible_company_default(monkeypatch):
    monkeypatch.setattr(main, 'settings', lambda: main.DEFAULT_SETTINGS)
    order = workbooks.base_order()
    main._new_order_defaults([order])
    assert order['order_date'] and order['provenance']['order_date']['reason'] == 'default'
