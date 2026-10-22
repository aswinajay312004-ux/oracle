"""What well-known garment ERP column names mean, and which Excel labels mean the same thing.

Used for ORDERENTRY and for any other table that has a column with one of these names. A column's own Oracle
comment ("Ship date|FOB date") and the labels learned from the person's earlier mappings are added at run time,
so a new column or a new buyer layout is matched without changing code.

level: 'order' = same value on every row of an order; 'line' = per colour line; 'size' = per size row.
field: the structural reader's field (order_ref, buyer, …) or a size-table field (color, size, quantity, …).
system: filled by the ERP itself (IDs, user names); POPPYS never fills these automatically."""
from __future__ import annotations
import re
from .workbooks import norm


def V(label, level='order', field=None, labels=(), system=False):
    return dict(label=label, level=level, field=field, labels=list(labels), system=system)


KNOWN = {
    'IONO': V('OC number', field='order_ref', labels=['OC No', 'OCN', 'OC number', 'IO No', 'IO number', 'Order no', 'Order number', 'Order ref', 'Order reference', 'Internal order no']),
    'BONO': V('Buyer order no.', labels=['BO No', 'BO number', 'Buyer order no', 'Buyer order number']),
    'BUYERNAME': V('Buyer', field='buyer', labels=['Buyer', 'Buyer name', 'Customer', 'Customer name']),
    'STYLENO': V('Style no.', field='style', labels=['Style', 'Style no', 'Style number', 'Item', 'Item number', 'Style #']),
    'STYLEDESC': V('Style description', field='description', labels=['Description', 'Garment description', 'Style description', 'Item description']),
    'PONUM': V('PO number', field='po_number', labels=['PO', 'PO no', 'PO number', 'Contract', 'Contract no', 'Contract number']),
    'SEASON': V('Season', field='season', labels=['Season']),
    'MERCH': V('Merchandiser', field='merch', labels=['Merchandiser', 'Merch']),
    'ORDERDATE': V('Order date', field='order_date', labels=['Order date', 'OC date', 'Confirmation date', 'Order confirmation date']),
    'DELDATE': V('Delivery date', labels=['Delivery date', 'Del date', 'Delivery dt']),
    'SHIPDT': V('Ship date', field='ship_date', labels=['Ship date', 'FOB date', 'Shipment date', 'Ship dt', 'Shipping date']),
    'EXFACDT': V('Ex-factory date', labels=['Ex-factory date', 'Ex factory date', 'Ex-fac date', 'Ex fac date', 'XF date']),
    'PODATE': V('PO date', labels=['PO date', 'PO dt']),
    'BUYORDDATE': V('Buyer order date', labels=['Buyer order date', 'BO date']),
    'OQTY': V('Order quantity (pieces)', labels=['QTY(IN PIECES)', 'Qty in pieces', 'Total pieces', 'Total quantity', 'Total qty', 'Order qty', 'Order quantity']),
    'BOQTY': V('Buyer order quantity', labels=['BO qty', 'Buyer order qty', 'Buyer order quantity']),
    'DESCOUNTRY': V('Destination country', labels=['Destination', 'Destination country', 'Country', 'Country of destination']),
    'CITY': V('City', labels=['City', 'Destination city']),
    'LOADPORT': V('Port of loading', labels=['Port of loading', 'Load port', 'Loading port', 'POL']),
    'SHBY': V('Ship by', labels=['Ship by', 'Ship via', 'Ship mode', 'Shipment mode']),
    'MOS': V('Mode of shipment', labels=['Mode of shipment', 'MOS']),
    'DT': V('Delivery terms', labels=['Delivery terms', 'Incoterms', 'Terms of delivery']),
    'PT': V('Payment terms', labels=['Payment terms', 'Terms of payment', 'Payment']),
    'CURRENCY': V('Currency', labels=['Currency']),
    'PRICE': V('Price', labels=['Price', 'Price in USD', 'FOB price', 'Unit price', 'Price per piece']),
    'CONVRATE': V('Conversion rate', labels=['Conversion rate', 'Exchange rate', 'Conv rate']),
    'INR': V('Value in INR', labels=['INR', 'Value in INR', 'INR value']),
    'UOM': V('Unit', labels=['UOM', 'Unit of measure']),
    'EXPER': V('Excess %', labels=['Excess %', 'Excess', 'Allowance %', 'Extra %']),
    'FABNAME': V('Fabric', labels=['Fabric name', 'Main fabric', 'Fabric description']),
    'GSM': V('GSM', labels=['GSM']),
    'GTYPE': V('Garment type', labels=['Garment type', 'Product type']),
    'OTYPE': V('Order type', labels=['Order type']),
    'ORDCATEGORY': V('Order category', labels=['Order category']),
    'FABMANAGER': V('Fabric manager', labels=['Fabric manager']),
    'PREPBY': V('Prepared by', labels=['Prepared by']),
    'PRODUNIT': V('Production unit', labels=['Production unit', 'Factory']),
    'PACKDESC': V('Pack description', labels=['Pack description', 'Packing description', 'Pack type']),
    'PACKCOMBO': V('Pack combo', labels=['Pack combo']),
    'NOCOMP': V('No. of components', labels=['No of components', 'No. of components']),
    'NOPKCOMP': V('Pieces per pack', labels=['Pcs per pack', 'Pieces per pack', 'No of pack components']),
    'DELNUM': V('Delivery number', labels=['Delivery no', 'Delivery number', 'Del no']),
    'REMARKS': V('Remarks', labels=['Remarks', 'Notes', 'Comments']),
    'ASSORTED': V('Assorted', labels=['Assorted']),
    'STYLERATE': V('Style rate', labels=['Style rate']),
    'ORATE': V('Order rate', labels=['Order rate']),
    'SRATE': V('Rate (S)'),
    'BALDAYS': V('Balance days'),
    'PRODQTY': V('Production quantity', labels=['Production qty', 'Production quantity']),
    'TEMPLATE': V('Template'),
    'CRATIO': V('Colour ratio'),
    'RATIO': V('Size ratio'),
    'SYES': V('SYES'),
    'OD': V('OD'),
    'ENAME': V('Entry name'),
    'MERCHORDER': V('Merch order'),
    # size table: one value per row
    'COMBO': V('Colour / combo', level='line', field='color', labels=['Colour', 'Color', 'Combo']),
    'GARMENT': V('Garment', level='line', field='label'),
    'ORDSTYLEROW': V('Line no.', level='line', field='line_no'),
    'COMBOQTY': V('Colour quantity', level='line', field='line_total'),
    'SIZES': V('Size', level='size', field='size'),
    'ORDERQTY': V('Size quantity', level='size', field='quantity'),
    'ORDSIZEROW': V('Size position', level='size', field='size_no'),
    # filled by the ERP
    'ORDMASID': V('ERP order ID', system=True), 'OMAXNO': V('ERP number', system=True), 'SSEQNO': V('ERP sequence', system=True),
    'SIZEID': V('ERP size ID', level='size', system=True), 'STYLENUMID': V('ERP style ID', system=True), 'STMASID': V('ERP style master ID', system=True),
    'COLORID': V('ERP colour ID', level='line', system=True), 'PACKDESCID': V('ERP pack ID', system=True), 'PACKCOMBOID': V('ERP pack combo ID', system=True),
    'USERID': V('ERP user ID', system=True), 'USERNAME': V('ERP user name', system=True),
    # columns of the earlier POPPYS table (EXCEL_ORDERS), so it keeps matching
    'BOMDATE': V('BOM date', labels=['BOM date']), 'FABIHDATE': V('Fabric in-house date', labels=['Fab IH date', 'Fabric in-house date']),
    'PACKQTY': V('Packs', labels=['QTY(IN PACKS)', 'Qty in packs', 'Packs']),
}

# Header words that are labels, not values, even when no column uses them (so "OCN | QTY(IN PACKS)" is read as a header row).
HEADER_WORDS = ['QTY(IN PACKS)', 'QTY(IN PIECES)', 'BOM date', 'Fab IH date', 'Contract', 'Destination', 'Price in USD', 'OCN', 'Garment',
                'Description', 'Total', 'Colour', 'Size', 'Sizes', 'Quantity', 'Qty', 'Remarks']

ROW_FIELDS = {
    'color': ('line', 'Colour (size table)'),
    'label': ('line', 'Garment name (size table)'),
    'line_no': ('line', 'Line number 1, 2, 3…'),
    'line_total': ('line', 'Pieces of this colour (sum of its sizes)'),
    'size': ('size', 'Size name'),
    'quantity': ('size', 'Pieces of this size'),
    'size_no': ('size', 'Size position 1, 2, 3…'),
}
ORDER_FIELDS = {'order_ref': 'OC number', 'buyer': 'Buyer', 'style': 'Style', 'description': 'Description', 'po_number': 'PO / contract',
                'ship_date': 'Ship date', 'order_date': 'Order date', 'season': 'Season', 'merch': 'Merchandiser'}


def pretty(name):
    """FABRIC_TYPE -> 'Fabric type'."""
    words = name.replace('_', ' ').strip().lower()
    return words[:1].upper() + words[1:] if words else name


def condensed(value):
    return re.sub(r'[^a-z0-9%]+', '', str(value or '').lower())


def meaning(name, comment=None):
    """Label, level, field and accepted Excel labels for one column."""
    known = KNOWN.get(name)
    comment_labels = [x.strip() for x in str(comment or '').split('|') if x.strip() and len(x.strip()) <= 60]
    base = dict(known) if known else V(comment_labels[0] if comment_labels else pretty(name))
    labels = list(dict.fromkeys(base['labels'] + comment_labels))
    # The column name itself is a label when it reads like a word (GSM, CITY, FABRIC_TYPE), never 2-letter codes.
    spoken = name.replace('_', ' ')
    if len(condensed(spoken)) >= 3 and not base['system']:
        labels.append(spoken)
    base['labels'] = labels
    if comment_labels and not known:
        base['label'] = comment_labels[0]
    return base


def label_set(labels):
    return {norm(x) for x in labels if norm(x)}


GLOBAL_LABELS = set().union(*(label_set(v['labels']) for v in KNOWN.values())) | label_set(HEADER_WORDS)


# Business groups: each has its own colour on the compare screen (labels are always shown too, never colour alone).
GROUPS = {
    'order': 'Order', 'buyer': 'Buyer', 'style': 'Style', 'colour': 'Colour', 'size': 'Size & quantity', 'fabric': 'Fabric',
    'shipment': 'Shipment', 'commercial': 'Price & terms', 'packing': 'Packing', 'printing': 'Printing & embroidery', 'trims': 'Trims',
    'other': 'Other',
}
GROUP_OF = {
    'IONO': 'order', 'BONO': 'order', 'PONUM': 'order', 'ORDCATEGORY': 'order', 'OTYPE': 'order', 'ORDERDATE': 'order', 'PODATE': 'order',
    'BUYORDDATE': 'order', 'TEMPLATE': 'order', 'ORDMASID': 'order', 'OMAXNO': 'order', 'ENAME': 'order', 'MERCHORDER': 'order', 'SSEQNO': 'order',
    'BUYERNAME': 'buyer', 'MERCH': 'buyer', 'FABMANAGER': 'buyer', 'PREPBY': 'buyer', 'USERID': 'buyer', 'USERNAME': 'buyer',
    'STYLENO': 'style', 'STYLEDESC': 'style', 'GTYPE': 'style', 'SEASON': 'style', 'STYLENUMID': 'style', 'STMASID': 'style', 'NOCOMP': 'style',
    'COMBO': 'colour', 'COMBOQTY': 'colour', 'CRATIO': 'colour', 'ORDSTYLEROW': 'colour', 'COLORID': 'colour', 'GARMENT': 'colour',
    'SIZES': 'size', 'ORDERQTY': 'size', 'ORDSIZEROW': 'size', 'RATIO': 'size', 'OQTY': 'size', 'BOQTY': 'size', 'PRODQTY': 'size', 'EXPER': 'size',
    'SIZEID': 'size', 'SYES': 'size', 'PACKQTY': 'size', 'QTYTYPE': 'size',
    'FABNAME': 'fabric', 'GSM': 'fabric', 'FABIHDATE': 'fabric',
    'SHIPDT': 'shipment', 'DELDATE': 'shipment', 'EXFACDT': 'shipment', 'LOADPORT': 'shipment', 'SHBY': 'shipment', 'MOS': 'shipment',
    'DT': 'shipment', 'DESCOUNTRY': 'shipment', 'CITY': 'shipment', 'DELNUM': 'shipment', 'BALDAYS': 'shipment', 'OD': 'shipment',
    'PRICE': 'commercial', 'CURRENCY': 'commercial', 'CONVRATE': 'commercial', 'INR': 'commercial', 'SRATE': 'commercial',
    'STYLERATE': 'commercial', 'ORATE': 'commercial', 'PT': 'commercial', 'BOMDATE': 'order',
    'PACKDESC': 'packing', 'PACKCOMBO': 'packing', 'NOPKCOMP': 'packing', 'ASSORTED': 'packing', 'UOM': 'packing', 'PACKDESCID': 'packing',
    'PACKCOMBOID': 'packing', 'PRODUNIT': 'other', 'REMARKS': 'other',
}
GROUP_WORDS = [('printing', ('PRINT', 'EMB', 'INK')), ('fabric', ('FAB', 'GSM', 'YARN', 'DIA', 'KNIT')), ('shipment', ('SHIP', 'PORT', 'DEL', 'DEST', 'COUNTRY')),
               ('commercial', ('PRICE', 'RATE', 'CURR', 'COST', 'AMOUNT', 'VALUE', 'PAY')), ('size', ('SIZE', 'QTY', 'QUANT', 'PCS')),
               ('colour', ('COLOR', 'COLOUR', 'COMBO', 'SHADE')), ('buyer', ('BUYER', 'CUSTOMER', 'MERCH')), ('style', ('STYLE', 'ITEM', 'GARMENT', 'DESC')),
               ('packing', ('PACK', 'CARTON', 'POLY')), ('trims', ('LABEL', 'TRIM', 'THREAD', 'BUTTON', 'ZIP', 'TAPE', 'PRONG')), ('order', ('ORDER', 'PO', 'OC'))]
SECTION_GROUP = {'fabric': 'fabric', 'printing': 'printing', 'embroidery': 'printing', 'costing': 'commercial', 'packing': 'packing', 'trims': 'trims',
                 'order': 'size', 'sample': 'size', 'summary': 'size', 'other': 'other'}


def group_of(name):
    """Business group of a column (or an Excel header) by its name."""
    if name in GROUP_OF:
        return GROUP_OF[name]
    up = re.sub(r'[^A-Z]+', ' ', str(name or '').upper())
    return next((g for g, words in GROUP_WORDS if any(w in up.split() or up.replace(' ', '').startswith(w) for w in words)), 'other')
