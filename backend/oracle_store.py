"""Restricted, bind-only order writer for the verified GARMENT_DEMO schema.

Field rules (business variation is normal, database rules are not negotiable):
  * Optional values absent from Excel stay NULL. No zero, empty string or default is invented.
  * Child records (lines, sizes) are created only for data that is present.
  * Blocking issues are limited to values Oracle would reject or that would corrupt the order:
    wrong types, lengths, ambiguous/cancelled masters, inconsistent totals, duplicates.
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import oracledb
from .config import OWNER, setting

MASTERS = {
 'buyer':('BUYMAS','BUYMASID','BUYERNAME'), 'style':('STYLENUM','STYLENUMID','STYLENUMBER'),
 'description':('STYLEMAS','STYLEMASID','SDESC'), 'size':('SIZE_MAS','SIZE_MASID','SIZES'),
 'color':('COLORMAS','COLORMASID','COLOR'), 'uom':('UNITMAS','UNITMASID','UOM'),
 'merch':('MERCHMAS','MERCHMASID','NAME'),
}
TABLES={t for t,_,_ in MASTERS.values()} | {'ORDMAS','ORDSTYLE','ORDSIZE'}
PK={t:p for t,p,_ in MASTERS.values()} | {t:t+'ID' for t in ('ORDMAS','ORDSTYLE','ORDSIZE')}
# Columns written by the engine itself; a reviewer cannot bind them as free extras.
MANAGED={
 'ORDMAS':{'ORDMASID','DOCID','BUYER','STYLENO','STYLEDESC','BONO','ODATE','UOM','SAMPLETYPE','ORDCATEGORY','MERCH','OQTY'},
 'ORDSTYLE':{'ORDSTYLEID','ORDMASID','STNO','STYLENAME','PONUM','SUBNO','COMBO','ORDQTY','PRODQTY','SHIPDT','PUOM','SNO'},
 'ORDSIZE':{'ORDSIZEID','ORDMASID','SUB3_SUBNO','GSIZE','RQTY','PQTY','ORDSIZEROW'},
}
# Master references inside order tables (verified by this writer's own insert path).
REFERENCES={('ORDMAS','BUYER'):'buyer',('ORDMAS','STYLENO'):'style',('ORDMAS','STYLEDESC'):'description',
 ('ORDMAS','UOM'):'uom',('ORDMAS','MERCH'):'merch',('ORDSTYLE','STNO'):'style',('ORDSTYLE','STYLENAME'):'description',
 ('ORDSTYLE','COMBO'):'color',('ORDSTYLE','PUOM'):'uom',('ORDSIZE','GSIZE'):'size'}
VERIFIED_RELATIONSHIPS=[dict(parent=MASTERS[role][0],parent_column=MASTERS[role][1],child=t,child_column=c,
    kind='verified',note='Used by the POPPYS order writer') for (t,c),role in REFERENCES.items()]+[
 dict(parent='ORDMAS',parent_column='ORDMASID',child='ORDSTYLE',child_column='ORDMASID',kind='verified',note='Order lines'),
 dict(parent='ORDMAS',parent_column='ORDMASID',child='ORDSIZE',child_column='ORDMASID',kind='verified',note='Size quantities'),
 dict(parent='ORDSTYLE',parent_column='SUBNO',child='ORDSIZE',child_column='SUB3_SUBNO',kind='verified',note='Sizes of a line, scoped by ORDMASID')]
TABLE_INFO={'ORDMAS':('Order header','One record per order: buyer, style, reference, dates and total quantity.'),
 'ORDSTYLE':('Order lines','One record per garment / colour line with its line quantity and ship date.'),
 'ORDSIZE':('Size quantities','One record per size of a line.'),
 'BUYMAS':('Buyers','Buyer master.'),'STYLENUM':('Style numbers','Style number master.'),
 'STYLEMAS':('Garment descriptions','Style description master.'),'SIZE_MAS':('Sizes','Size master.'),
 'COLORMAS':('Colours','Colour / combo master.'),'UNITMAS':('Units','Unit of measure master.'),
 'MERCHMAS':('Merchandisers','Merchandiser master.')}
IDENT=re.compile(r'^[A-Z][A-Z0-9_$#]{0,127}$')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()

def connect():
    if not setting('ORACLE_PASSWORD'):
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    return oracledb.connect(user=setting('ORACLE_USER'),password=setting('ORACLE_PASSWORD'),
        dsn=setting('ORACLE_DSN','127.0.0.1:1521/FREEPDB1'),tcp_connect_timeout=4)

def schema(conn):
    """Live column metadata of every GARMENT_DEMO table this account may read."""
    c=conn.cursor()
    c.execute('''select table_name,column_name,data_type,data_length,data_precision,data_scale,nullable,data_default
        from all_tab_columns where owner=:owner order by table_name,column_id''',owner=OWNER)
    result={t:{} for t in TABLES}
    for table,col,kind,length,precision,scale,nullable,default in c:
        result.setdefault(table,{})[col]=dict(type=kind,length=length,precision=precision,scale=scale,nullable=nullable,
            default=None if default is None else str(default).strip())
    for t,pk in PK.items():
        if pk not in result[t]: raise ValueError(f'Required Oracle table/column missing: {t}.{pk}')
    return result

def constraints(conn):
    """Declared PK / unique / foreign keys. Physical relationships only; never inferred from names."""
    c=conn.cursor()
    c.execute('''select c.constraint_name,c.constraint_type,c.table_name,cc.column_name,cc.position,
            r.table_name,rc.column_name
        from all_constraints c join all_cons_columns cc on cc.owner=c.owner and cc.constraint_name=c.constraint_name
        left join all_constraints r on r.owner=c.r_owner and r.constraint_name=c.r_constraint_name
        left join all_cons_columns rc on rc.owner=r.owner and rc.constraint_name=r.constraint_name and rc.position=cc.position
        where c.owner=:owner and c.constraint_type in ('P','U','R') order by c.constraint_name,cc.position''',owner=OWNER)
    keys={};fks=[]
    for name,kind,table,col,pos,rtable,rcol in c:
        if kind in ('P','U'):keys.setdefault(table,[]).append(dict(name=name,type='primary' if kind=='P' else 'unique',column=col))
        elif rtable:fks.append(dict(name=name,parent=rtable,parent_column=rcol,child=table,child_column=col,kind='physical'))
    return dict(keys=keys,foreign_keys=fks)

def masters(conn):
    result={};c=conn.cursor()
    for role,(table,pk,label) in MASTERS.items():
        c.execute(f'SELECT {pk},{label},CANCEL FROM {OWNER}.{table} ORDER BY {pk} FETCH FIRST 5001 ROWS ONLY')
        rows=c.fetchall()
        if len(rows)>5000: raise ValueError(f'{table}: master catalogue exceeds demo limit; configure a scoped lookup.')
        result[role]=[dict(id=k,label=str(v).strip(),cancelled=str(cancel or '').upper()=='T') for k,v,cancel in rows if v is not None]
    return result

def normalize(s): return ' '.join(str(s or '').strip().upper().split())

def blank(value): return value is None or (isinstance(value,str) and not value.strip())

def count(value, name, errors, allow_zero=True):
    try:
        if isinstance(value,bool) or value is None or str(value).strip()=='': raise ValueError()
        d=Decimal(str(value).strip())
        if not d.is_finite() or d!=d.to_integral_value() or d<0 or d>999999999: raise ValueError()
        if d==0 and not allow_zero: raise ValueError()
        return int(d)
    except (ValueError,InvalidOperation):
        errors.append(f'{name}: enter a {"non-negative" if allow_zero else "positive"} whole number.');return 0

MONTHS={m:i for i,m in enumerate(['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'],1)}
def to_date(value):
    """(ISO date, None) or (None, reason). Never guesses between day-first and month-first."""
    if isinstance(value,(datetime,date)):return value.isoformat()[:10],None
    raw=str(value).strip()
    m=re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][\d:.]+)?',raw)
    if m:y,mo,d=map(int,m.groups())
    else:
        m=re.fullmatch(r'(\d{1,2})[-/.](\d{1,2}|[A-Za-z]{3})[-/.](\d{2}|\d{4})',raw)
        if not m:return None,f'"{raw}" is not a date. Enter it as YYYY-MM-DD.'
        a,b,y=m.groups();y=int(y)+(2000 if len(y)==2 else 0)
        if b.isalpha():
            if b.upper() not in MONTHS:return None,f'"{raw}" is not a date.'
            d,mo=int(a),MONTHS[b.upper()]
        else:
            a,b=int(a),int(b)
            if a<=12 and b<=12 and a!=b:return None,f'"{raw}" can be read as two different dates. Enter it as YYYY-MM-DD.'
            d,mo=(b,a) if b>12 else (a,b)
    try:return date(y,mo,d).isoformat(),None
    except ValueError:return None,f'"{raw}" is not a valid calendar date.'

def convert(meta, value):
    """Excel value → Oracle-ready JSON value for one column, or raise ValueError(reason)."""
    kind=meta['type']
    if kind in ('VARCHAR2','CHAR','NVARCHAR2','NCHAR','CLOB'):
        result=value.isoformat()[:10] if isinstance(value,(datetime,date)) else (str(int(value)) if isinstance(value,float) and value.is_integer() else str(value))
        if kind!='CLOB' and len(result.encode('utf-8'))>meta['length']:raise ValueError(f'longer than {meta["length"]} bytes')
        return result
    if kind=='NUMBER':
        try:d=Decimal(str(value).strip().replace(',',''))
        except InvalidOperation:raise ValueError('needs a number')
        if not d.is_finite():raise ValueError('needs a number')
        if meta['scale']==0 and d!=d.to_integral_value():raise ValueError('needs a whole number')
        if meta['precision'] and abs(d)>=Decimal(10)**(meta['precision']-(meta['scale'] or 0)):raise ValueError('number too large')
        return int(d) if d==d.to_integral_value() else str(d.normalize())
    if kind=='DATE' or kind.startswith('TIMESTAMP'):
        iso,reason=to_date(value)
        if reason:raise ValueError(reason)
        return iso
    raise ValueError(f'{kind} columns cannot be filled from Excel')

def build_plan(registry, lookups, orders, existing_refs=frozenset(), committed_file=None, protected=frozenset()):
    """Pure plan builder: exact rows, columns and values that one approval will write."""
    operations=[];errors=[];warnings=[];info=[];known={}
    totals=dict(orders=len(orders),lines=0,pieces=0)
    if not orders or len(orders)>50: errors.append('Import must contain 1–50 orders.')
    if committed_file:warnings.append(f'This exact workbook was saved before (import {committed_file[:8]}). Only orders with a new order reference can be saved again.')
    def add(key,table,values,action='insert',provenance=None,columns=None,label=''):
        operations.append(dict(key=key,table=table,action=action,values=values,source=provenance or {},columns=columns or {},label=label))
        return '$'+key
    def master(role,label,where,provenance=None):
        label=' '.join(str(label or '').strip().split())
        if not label:return None
        table,pk,col=MASTERS[role]; code=(role,normalize(label))
        if code in known:return known[code]
        matches=[x for x in lookups[role] if normalize(x['label'])==normalize(label)]
        if len(matches)>1 or (matches and matches[0]['id'] is None):
            errors.append(f'{where}: "{label}" matches more than one {role} record in Oracle. Resolve the duplicate master records.');return None
        key=f'm{len(known)+1}'
        if matches and matches[0]['cancelled']:
            errors.append(f'{where}: {role} "{label}" is cancelled in Oracle.');return None
        if matches:
            val=matches[0]['id'];add(key,table,{pk:val,col:matches[0]['label']},'reuse',provenance,{col:dict(origin='master',source=provenance)},label)
        else:
            val=add(key,table,{pk:'$'+key,col:label},provenance=provenance,columns={pk:dict(origin='generated'),col:dict(origin='excel',source=provenance)},label=label)
            info.append(f'New {role} "{label}" will be added to {table}.')
        known[code]=val;return val
    def extras(table,values,columns,items,where):
        for col,item in (items or {}).items():
            meta=registry[table].get(col)
            if not IDENT.match(col) or meta is None:errors.append(f'{where}: {table}.{col} does not exist in Oracle.');continue
            if col in MANAGED[table] or (table,col) in protected:errors.append(f'{where}: {table}.{col} is filled automatically and cannot be mapped.');continue
            if blank(item.get('value')):continue
            try:values[col]=convert(meta,item['value'])
            except ValueError as exc:errors.append(f'{where}: {table}.{col} {exc} (value "{item["value"]}").');continue
            columns[col]=dict(origin='excel' if (item.get('source') or {}).get('cell') else 'manual',source=item.get('source'),raw=item['value'])
    seen=set()
    for index,o in enumerate(orders):
        prefix=f'Order {index+1}'; ref=str(o.get('order_ref','')).strip(); prov=o.get('provenance',{})
        if not ref:errors.append(f'{prefix}: enter an order reference. It identifies the order in Oracle and prevents duplicates.')
        elif normalize(ref) in seen:errors.append(f'{prefix}: order reference {ref} appears twice in this workbook.')
        elif normalize(ref) in existing_refs:errors.append(f'{prefix}: order reference {ref} already exists in Oracle. Change the reference, or open Orders to edit the saved order.')
        seen.add(normalize(ref))
        basis=o.get('quantity_basis')
        if basis not in ('sample','order','order_demo'):
            errors.append(f'{prefix}: choose which quantities to save (order or sample section).')
        factor=None
        if not blank(o.get('pieces_per_unit')):factor=count(o.get('pieces_per_unit'),prefix+' pieces per unit',errors,False)
        dates={}
        for key in ('ship_date','order_date'):
            if blank(o.get(key)):continue
            iso,reason=to_date(o[key])
            if reason:errors.append(f'{prefix}: {key.replace("_"," ")} {reason}')
            else:dates[key]=iso
        if len(dates)==2 and dates['ship_date']<dates['order_date']:
            warnings.append(f'{prefix}: ship date {dates["ship_date"]} is before the order date {dates["order_date"]}.')
        refs={role:master(role,o.get(role),prefix,prov.get(role)) for role in ('buyer','style','description')}
        uom=master('uom','PCS',prefix,{'derivation':'Quantities converted to pieces with your pieces-per-unit'}) if factor else None
        merch=master('merch',o.get('merch'),prefix,prov.get('merch'))
        orderkey=f'o{index+1}'; ordertotal=0
        head={'ORDMASID':'$'+orderkey,'DOCID':ref or None,'BUYER':refs['buyer'],'STYLENO':refs['style'],
            'STYLEDESC':refs['description'],'BONO':str(o.get('po_number','')).strip() or None,
            'ODATE':dates.get('order_date'),'UOM':uom,'SAMPLETYPE':str(o.get('sample_type','')).strip() or None,
            'ORDCATEGORY':'SAMPLE ORDER' if basis=='sample' else None,'MERCH':merch}
        cols={'ORDMASID':dict(origin='generated'),'DOCID':dict(origin='excel' if prov.get('order_ref',{}).get('cell') else 'manual',source=prov.get('order_ref')),
            'BUYER':dict(origin='master',source=prov.get('buyer')),'STYLENO':dict(origin='master',source=prov.get('style')),
            'STYLEDESC':dict(origin='master',source=prov.get('description')),'BONO':dict(origin='excel',source=prov.get('po_number')),
            'ODATE':dict(origin='excel',source=prov.get('order_date'),raw=o.get('order_date')),'UOM':dict(origin='derived'),
            'SAMPLETYPE':dict(origin='manual',source=prov.get('sample_type')),'ORDCATEGORY':dict(origin='derived'),
            'MERCH':dict(origin='master',source=prov.get('merch')),'OQTY':dict(origin='derived',note='Sum of all size quantities')}
        head={k:v for k,v in head.items() if v is not None}
        extras('ORDMAS',head,cols,o.get('extras'),prefix)
        add(orderkey,'ORDMAS',head,provenance=prov,columns={k:v for k,v in cols.items() if k in head or k=='OQTY'},label=ref or prefix)
        lines=o.get('lines',[])
        if len(lines)>100:errors.append(f'{prefix}: more than 100 garment lines.')
        if not lines:info.append(f'{prefix}: no garment lines or sizes in the workbook; only the order header will be saved.')
        for li,line in enumerate(lines):
            where=f'{prefix}, line {li+1}';linekey=f'{orderkey}l{li+1}';total=0
            color=master('color',line.get('color'),where,line.get('provenance',{}).get('color'))
            sizes=line.get('sizes',[]);sizes_seen=set();sizedata=[]
            if len(sizes)>100:errors.append(f'{where}: more than 100 sizes.')
            for si,s in enumerate(sizes):
                name=str(s.get('size','')).strip()
                if blank(s.get('quantity')):
                    if name:info.append(f'{where}: size {name} has no quantity in Excel, so no size record is created.')
                    continue
                if not name:errors.append(f'{where}: quantity {s.get("quantity")} (cell {(s.get("source") or {}).get("cell","?")}) has no size name.');continue
                if normalize(name) in sizes_seen:errors.append(f'{where}: size {name} appears twice; review before combining quantities.')
                sizes_seen.add(normalize(name))
                qty=count(s.get('quantity'),f'{where}, size {name}',errors)
                total+=qty
                sizedata.append((si,master('size',name,where,s.get('size_source')),qty*(factor or 1),s))
            if line.get('declared_total') is not None and sizedata:
                declared=count(line['declared_total'],where+' Excel total',errors)
                if declared!=total:errors.append(f'{where}: sizes add up to {total} but the Excel total says {declared}. Check the size quantities.')
            if not sizedata:
                info.append(f'{where}: no quantities in Excel; this line is not saved.');continue
            pieces=total*(factor or 1);ordertotal+=pieces;totals['lines']+=1
            subno=f'{ref}:{li+1}'
            linevals=dict(ORDSTYLEID='$'+linekey,ORDMASID='$'+orderkey,STNO=refs['style'],STYLENAME=refs['description'],
                PONUM=head.get('BONO'),SUBNO=subno,COMBO=color,ORDQTY=pieces,PRODQTY=pieces,SHIPDT=dates.get('ship_date'),PUOM=uom,SNO=li+1)
            linecols=dict(ORDSTYLEID=dict(origin='generated'),ORDMASID=dict(origin='generated'),STNO=dict(origin='master',source=prov.get('style')),
                STYLENAME=dict(origin='master',source=prov.get('description')),PONUM=dict(origin='excel',source=prov.get('po_number')),
                SUBNO=dict(origin='derived',note='Order reference + line number'),COMBO=dict(origin='master',source=line.get('provenance',{}).get('color')),
                ORDQTY=dict(origin='derived',note='Sum of the size quantities'),PRODQTY=dict(origin='derived',note='Sum of the size quantities'),
                SHIPDT=dict(origin='excel',source=prov.get('ship_date'),raw=o.get('ship_date')),PUOM=dict(origin='derived'),SNO=dict(origin='derived'))
            linevals={k:v for k,v in linevals.items() if v is not None}
            extras('ORDSTYLE',linevals,linecols,line.get('extras'),where)
            add(linekey,'ORDSTYLE',linevals,provenance=line.get('provenance'),columns={k:v for k,v in linecols.items() if k in linevals},
                label=' · '.join(x for x in (line.get('label'),line.get('color')) if x) or f'Line {li+1}')
            for si,sizeid,qty,s in sizedata:
                sizekey=f'{linekey}s{si+1}'
                vals={k:v for k,v in dict(ORDSIZEID='$'+sizekey,ORDMASID='$'+orderkey,SUB3_SUBNO=subno,GSIZE=sizeid,RQTY=qty,PQTY=qty,ORDSIZEROW=si+1).items() if v is not None}
                add(sizekey,'ORDSIZE',vals,provenance=s.get('source'),label=f'{s.get("size")}',columns=dict(ORDSIZEID=dict(origin='generated'),
                    ORDMASID=dict(origin='generated'),SUB3_SUBNO=dict(origin='derived'),GSIZE=dict(origin='master',source=s.get('size_source')),
                    RQTY=dict(origin='excel',source=s.get('source'),raw=s.get('quantity')),PQTY=dict(origin='excel',source=s.get('source'),raw=s.get('quantity')),
                    ORDSIZEROW=dict(origin='derived')))
        saved_lines=sum(1 for op in operations if op['table']=='ORDSTYLE' and op['values'].get('ORDMASID')=='$'+orderkey)
        if saved_lines:operations[[op['key'] for op in operations].index(orderkey)]['values']['OQTY']=ordertotal
        totals['pieces']+=ordertotal
        if saved_lines and ordertotal==0:warnings.append(f'{prefix}: all quantities are zero.')
        if not factor and saved_lines:info.append(f'{prefix}: quantities are saved exactly as in Excel (no unit conversion, unit left blank).')
    # Fail closed against actual Oracle lengths/precision/nullability rather than silently truncate.
    for op in operations:
        if op['action']=='reuse':continue
        table=op['table'];vals=op['values']
        for col,meta in registry[table].items():
            if meta['nullable']=='N' and vals.get(col) is None and not meta.get('default'):
                errors.append(f'{table}.{col}: Oracle requires a value here. Map an Excel cell to it before approving.')
        for col,value in vals.items():
            meta=registry[table].get(col)
            if meta is None:errors.append(f'{table}.{col}: column absent in live database.');continue
            if value is None or (isinstance(value,str) and value.startswith('$')):continue
            if meta['type'] in ('VARCHAR2','CHAR') and len(str(value).encode('utf-8'))>meta['length']:
                errors.append(f'{table}.{col}: "{value}" is longer than the {meta["length"]} characters Oracle allows.')
            if meta['type']=='NUMBER':
                try:
                    value=Decimal(str(value))
                    if meta['precision'] and abs(value)>=Decimal(10)**(meta['precision']-(meta['scale'] or 0)):
                        errors.append(f'{table}.{col}: value exceeds numeric precision.')
                except InvalidOperation:errors.append(f'{table}.{col}: requires a number.')
    tables={}
    for op in operations:
        if op['table'] in tables:continue
        name,purpose=TABLE_INFO.get(op['table'],(op['table'],''))
        tables[op['table']]=dict(label=name,purpose=purpose,columns=[dict(name=c,type=m['type'],nullable=m['nullable'],
            required=m['nullable']=='N' and not m.get('default')) for c,m in registry[op['table']].items()])
    result=dict(operations=operations,errors=list(dict.fromkeys(errors)),warnings=list(dict.fromkeys(warnings)),
        info=list(dict.fromkeys(info)),totals=totals,tables=tables,schema_fingerprint=digest(registry))
    result['hash']=digest(result)
    return result

def plan(conn, orders, filehash):
    registry=schema(conn); lookups=masters(conn); c=conn.cursor()
    c.execute('select JOBID from PP_IMPORT_LOG where FILEHASH=:h fetch first 1 rows only',h=filehash)
    row=c.fetchone();existing=set()
    for o in orders:
        ref=normalize(o.get('order_ref'))
        if not ref:continue
        c.execute(f'SELECT 1 FROM {OWNER}.ORDMAS WHERE upper(trim(DOCID))=:ref FETCH FIRST 1 ROWS ONLY',ref=ref)
        if c.fetchone():existing.add(ref)
    fks={(f['child'],f['child_column']) for f in constraints(conn)['foreign_keys']}
    return build_plan(registry,lookups,orders,existing,row[0] if row else None,fks)

def read_result(cursor,jobid):
    cursor.execute('SELECT RESULT_JSON FROM PP_IMPORT_LOG WHERE JOBID=:id',id=jobid)
    row=cursor.fetchone()
    if not row:return None
    result=json.loads(row[0].read() if hasattr(row[0],'read') else row[0]);result['already_committed']=True
    return result

def bind_value(meta,value):
    if value is None:return None
    if meta['type']=='DATE' or meta['type'].startswith('TIMESTAMP'):return datetime.strptime(str(value)[:10],'%Y-%m-%d')
    if meta['type']=='NUMBER' and isinstance(value,str):return Decimal(value)
    return value

def plain(value):
    """Oracle value → JSON value for receipts and screens."""
    if isinstance(value,datetime):return value.isoformat()[:10] if value.time()==datetime.min.time() else value.isoformat(sep=' ')
    if isinstance(value,Decimal):return int(value) if value==value.to_integral_value() else float(value)
    if hasattr(value,'read'):return value.read()
    return value

def same(meta,expected,actual):
    if expected is None or actual is None:return expected is None and actual is None
    if meta['type']=='NUMBER':return Decimal(str(expected))==Decimal(str(actual))
    if meta['type']=='CHAR':return str(expected).rstrip()==str(actual).rstrip()
    if isinstance(expected,datetime) or isinstance(actual,datetime):return plain(expected)==plain(actual)
    return expected==actual

def commit(draft, expected_hash, approver):
    with connect() as conn:
        c=conn.cursor();c.execute('SELECT ID FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10')
        previous=read_result(c,draft['id'])
        if previous:return previous
        checked=plan(conn,draft['orders'],draft['filehash'])
        if checked['errors']:raise ValueError('; '.join(checked['errors']))
        if checked['hash']!=expected_hash:raise ValueError('Database or preview changed. Generate a fresh preview and review it before accepting.')
        refs={};inserted=[];outorders=[];registry=schema(conn)
        try:
            for o in draft['orders']:
                c.execute('INSERT INTO PP_ORDER_KEYS(ORDER_REF,JOBID) VALUES (:ref,:job)',ref=normalize(o['order_ref']),job=draft['id'])
            for op in checked['operations']:
                if op['action']=='reuse':continue
                table=op['table'];pk=PK[table]
                c.execute('SELECT PP_RECORD_SEQ.nextval FROM dual');ident=c.fetchone()[0]
                refs[op['key']]=ident
                vals={}
                for col,v in op['values'].items():
                    if isinstance(v,str) and v.startswith('$'):v=refs[v[1:]]
                    vals[col]=bind_value(registry[table][col],v)
                # table and columns originate only from this server's allowlisted, metadata-checked plan.
                cols=list(vals); binds={f'v{i}':vals[col] for i,col in enumerate(cols)}
                quoted=','.join(f'"{col}"' for col in cols)
                c.execute(f'INSERT INTO {OWNER}.{table} ({quoted}) VALUES ({",".join(":"+k for k in binds)})',binds)
                c.execute(f'SELECT {quoted} FROM {OWNER}.{table} WHERE {pk}=:id',id=ident)
                rows=c.fetchall()
                if len(rows)!=1 or not all(same(registry[table][col],vals[col],got) for col,got in zip(cols,rows[0])):
                    raise ValueError(f'{table}: inserted values did not match read-back.')
                inserted.append(dict(table=table,key=op['key'],id=ident,values={col:plain(v) for col,v in zip(cols,rows[0])}))
                if table=='ORDMAS':outorders.append(dict(order_ref=vals['DOCID'],ordmasid=ident))
            result=dict(job_id=draft['id'],orders=outorders,rows=inserted,committed_at=datetime.now().astimezone().isoformat(),already_committed=False)
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                job=draft['id'],filehash=draft['filehash'],hash=expected_hash,who=approver,payload=json.dumps(result,ensure_ascii=False))
            conn.commit()
        except Exception:
            conn.rollback();raise
    # Independent verification after commit, on a fresh connection.
    with connect() as conn:
        c=conn.cursor();mismatches=0
        for row in result['rows']:
            cols=list(row['values']);quoted=','.join(f'"{col}"' for col in cols)
            c.execute(f'SELECT {quoted} FROM {OWNER}.{row["table"]} WHERE {PK[row["table"]]}=:id',id=row['id'])
            got=c.fetchone()
            if not got or any(plain(g)!=row['values'][col] for col,g in zip(cols,got)):mismatches+=1
        result['verified']=mismatches==0;result['verified_rows']=len(result['rows'])-mismatches
    return result

def history():
    with connect() as conn:
        c=conn.cursor();c.execute('SELECT JOBID,PLANHASH,APPROVER,COMMITTED_AT,RESULT_JSON FROM PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC FETCH FIRST 200 ROWS ONLY')
        out=[]
        for j,h,a,d,payload in c:
            result=json.loads(payload.read() if hasattr(payload,'read') else payload)
            out.append(dict(job_id=j,plan_hash=h,approver=a,committed_at=d.isoformat(),orders=result.get('orders',[]),rows=len(result.get('rows',[])),mode=result.get('mode','erp')))
        return out

# ------------------------------------------------------------ saved orders

def fetch(c,table,where,binds,limit=500):
    c.execute(f'SELECT * FROM {OWNER}.{table} WHERE {where} FETCH FIRST {int(limit)+1} ROWS ONLY',binds)
    names=[d[0] for d in c.description];rows=[dict(zip(names,map(plain,r))) for r in c.fetchall()]
    return rows[:limit],len(rows)>limit

def labels(c,rows_by_table):
    """Human labels for master ids referenced by order rows (one query per master)."""
    result={}
    for (table,col),role in REFERENCES.items():
        ids={r.get(col) for r in rows_by_table.get(table,[]) if r.get(col) is not None}
        if not ids:continue
        mt,pk,label=MASTERS[role];binds={f'i{n}':v for n,v in enumerate(sorted(ids))}
        c.execute(f'SELECT {pk},{label} FROM {OWNER}.{mt} WHERE {pk} IN ({",".join(":"+k for k in binds)})',binds)
        result[f'{table}.{col}']={str(k):str(v or '').strip() for k,v in c}
    return result

def read_order(conn,ordmasid):
    c=conn.cursor()
    head,_=fetch(c,'ORDMAS','ORDMASID=:id',dict(id=ordmasid),1)
    if not head:raise KeyError('Order not found')
    lines,_=fetch(c,'ORDSTYLE','ORDMASID=:id ORDER BY SNO,ORDSTYLEID',dict(id=ordmasid))
    sizes,_=fetch(c,'ORDSIZE','ORDMASID=:id ORDER BY SUB3_SUBNO,ORDSIZEROW,ORDSIZEID',dict(id=ordmasid),2000)
    rows=dict(ORDMAS=head,ORDSTYLE=lines,ORDSIZE=sizes)
    return dict(ordmasid=ordmasid,rows=rows,labels=labels(c,rows))

def related_rows(conn,ordmasid,table,relationships):
    """Rows of any table connected to one order through verified/physical relationships.
    Child tables are scoped by a direct ORDMAS relationship when one exists, so rows from
    other orders sharing a line number are never mixed in."""
    if table in ('ORDMAS','ORDSTYLE','ORDSIZE'):
        rows=read_order(conn,ordmasid)['rows'][table];return dict(rows=rows,via='ORDMASID',truncated=False)
    order=read_order(conn,ordmasid)['rows'];c=conn.cursor()
    direct=[r for r in relationships if r['parent']=='ORDMAS' and r['child']==table]
    if direct:
        r=direct[0];rows,more=fetch(c,table,f'"{r["child_column"]}"=:id',dict(id=order['ORDMAS'][0][r['parent_column']]))
        return dict(rows=rows,via=f'{table}.{r["child_column"]} = ORDMAS.{r["parent_column"]}',truncated=more)
    for r in relationships:
        if r['parent']==table and r['child'] in order:
            ids=sorted({x.get(r['child_column']) for x in order[r['child']] if x.get(r['child_column']) is not None},key=str)[:500]
            if not ids:continue
            binds={f'i{n}':v for n,v in enumerate(ids)}
            rows,more=fetch(c,table,f'"{r["parent_column"]}" IN ({",".join(":"+k for k in binds)})',binds)
            return dict(rows=rows,via=f'{r["child"]}.{r["child_column"]} → {table}.{r["parent_column"]}',truncated=more)
    return dict(rows=[],via=None,truncated=False)

def summarize(order):
    """Order dashboard figures, aggregated per table separately (no join multiplication)."""
    rows=order['rows'];lab=order['labels'];head=rows['ORDMAS'][0]
    def name(key,value):return lab.get(key,{}).get(str(value),str(value) if value is not None else '—')
    by_size={}
    for s in rows['ORDSIZE']:
        k=name('ORDSIZE.GSIZE',s.get('GSIZE'));by_size[k]=by_size.get(k,0)+(s.get('RQTY') or 0)
    lines=[dict(label=name('ORDSTYLE.COMBO',l.get('COMBO')) if l.get('COMBO') is not None else f'Line {l.get("SNO") or ""}'.strip(),
        subno=l.get('SUBNO'),quantity=l.get('ORDQTY'),ship_date=l.get('SHIPDT'),
        sizes=[dict(size=name('ORDSIZE.GSIZE',s.get('GSIZE')),quantity=s.get('RQTY')) for s in rows['ORDSIZE'] if s.get('SUB3_SUBNO')==l.get('SUBNO')])
        for l in rows['ORDSTYLE']]
    size_total=sum(s.get('RQTY') or 0 for s in rows['ORDSIZE'])
    return dict(order_ref=head.get('DOCID'),buyer=name('ORDMAS.BUYER',head.get('BUYER')) if head.get('BUYER') is not None else None,
        style=name('ORDMAS.STYLENO',head.get('STYLENO')) if head.get('STYLENO') is not None else None,
        description=name('ORDMAS.STYLEDESC',head.get('STYLEDESC')) if head.get('STYLEDESC') is not None else None,
        po=head.get('BONO'),order_date=head.get('ODATE'),total=head.get('OQTY'),size_total=size_total,
        reconciled=head.get('OQTY') is None or head.get('OQTY')==size_total,
        ship_dates=sorted({l['ship_date'] for l in lines if l['ship_date']}),
        sizes=[dict(size=k,quantity=v) for k,v in by_size.items()],lines=lines,
        records={t:len(r) for t,r in rows.items()},
        filled={t:sorted({k for r in rs for k,v in r.items() if v is not None}) for t,rs in rows.items()})

# ------------------------------------------------------------ approved edits

EDIT_KEYS={'ORDMAS':{'ORDMASID'},'ORDSTYLE':{'ORDSTYLEID','ORDMASID','SUBNO'},'ORDSIZE':{'ORDSIZEID','ORDMASID','SUB3_SUBNO'}}
DERIVED={('ORDMAS','OQTY'),('ORDSTYLE','ORDQTY'),('ORDSTYLE','PRODQTY')}

def preview_changes(conn,ordmasid,changes):
    """Validate proposed edits against the current Oracle rows. Nothing is written."""
    registry=schema(conn);order=read_order(conn,ordmasid);rows=order['rows'];lookups=None
    errors=[];result=[];snapshot={}
    if not changes:errors.append('No changes to review.')
    if len(changes)>500:errors.append('Too many changes in one approval.')
    current={(t,r[PK[t]]):r for t,rs in rows.items() for r in rs}
    proposed={k:dict(v) for k,v in current.items()}
    for ch in changes:
        table=ch.get('table');col=ch.get('column');rid=ch.get('id')
        if table not in EDIT_KEYS or not IDENT.match(str(col)) or col not in registry[table]:errors.append(f'{table}.{col}: not editable.');continue
        if (table,rid) not in current:errors.append(f'{table} record {rid} does not belong to this order.');continue
        if col in EDIT_KEYS[table] or col==PK[table]:errors.append(f'{table}.{col} is a key and cannot be edited.');continue
        if (table,col) in DERIVED:errors.append(f'{table}.{col} is recalculated from the size quantities.');continue
        meta=registry[table][col];value=ch.get('value')
        if (table,col) in REFERENCES:
            role=REFERENCES[(table,col)];lookups=lookups or masters(conn)
            if blank(value):new=None
            else:
                found=[m for m in lookups[role] if normalize(m['label'])==normalize(value) and not m['cancelled']]
                if len(found)!=1:errors.append(f'{table}.{col}: "{value}" must match exactly one existing {role} in Oracle.');continue
                new=found[0]['id']
        elif blank(value):
            if meta['nullable']=='N':errors.append(f'{table}.{col}: Oracle requires a value.');continue
            new=None
        else:
            try:new=convert(meta,value)
            except ValueError as exc:errors.append(f'{table}.{col}: {exc}.');continue
        proposed[(table,rid)][col]=plain(bind_value(meta,new)) if new is not None else None
    # Recalculate totals only where the stored totals were consistent before the edit.
    def total(rs,col):return sum(r.get(col) or 0 for r in rs)
    notes=[]
    for line in rows['ORDSTYLE']:
        before=[r for r in rows['ORDSIZE'] if r.get('SUB3_SUBNO')==line.get('SUBNO')]
        after=[proposed[('ORDSIZE',r['ORDSIZEID'])] for r in before]
        for size_col,line_col in (('RQTY','ORDQTY'),('PQTY','PRODQTY')):
            if total(before,size_col)!=total(after,size_col):
                if before and line.get(line_col)==total(before,size_col):proposed[('ORDSTYLE',line['ORDSTYLEID'])][line_col]=total(after,size_col)
                else:notes.append(f'Line {line.get("SUBNO")}: {line_col} was not equal to its sizes before, so it is left unchanged.')
    head=rows['ORDMAS'][0];lines_after=[proposed[('ORDSTYLE',l['ORDSTYLEID'])] for l in rows['ORDSTYLE']]
    if total(rows['ORDSTYLE'],'ORDQTY')!=total(lines_after,'ORDQTY'):
        if head.get('OQTY')==total(rows['ORDSTYLE'],'ORDQTY'):proposed[('ORDMAS',head['ORDMASID'])]['OQTY']=total(lines_after,'ORDQTY')
        else:notes.append('ORDMAS.OQTY was not equal to its lines before, so it is left unchanged.')
    lab=order['labels']
    for key,row in proposed.items():
        for col,new in row.items():
            old=current[key][col]
            if old==new:continue
            table,rid=key;snapshot.setdefault(f'{table}:{rid}',{})[col]=old
            ref=f'{table}.{col}'
            result.append(dict(table=table,id=rid,column=col,old=old,new=new,derived=(table,col) in DERIVED,
                old_label=lab.get(ref,{}).get(str(old)) if (table,col) in REFERENCES else None,
                new_label=next((str(ch.get('value')) for ch in changes if ch.get('table')==table and ch.get('id')==rid and ch.get('column')==col),None) if (table,col) in REFERENCES else None))
    if not result and not errors:errors.append('The proposed values are the same as Oracle. Nothing to change.')
    preview=dict(ordmasid=ordmasid,changes=result,errors=list(dict.fromkeys(errors)),notes=notes,snapshot=snapshot)
    preview['hash']=digest(preview)
    return preview

def apply_changes(changeset_id,preview,approver):
    """One transaction: lock rows, confirm nobody changed them since the preview, update, read back, audit."""
    if preview['errors']:raise ValueError('Resolve the highlighted issues first.')
    with connect() as conn:
        c=conn.cursor();registry=schema(conn)
        c.execute('SELECT ID FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10')
        c.execute('SELECT count(*) FROM PP_CHANGE_LOG WHERE CHANGESET=:id',id=changeset_id)
        if c.fetchone()[0]:return dict(already_applied=True)
        grouped={}
        for ch in preview['changes']:grouped.setdefault((ch['table'],ch['id']),[]).append(ch)
        try:
            for (table,rid),items in grouped.items():
                cols=[i['column'] for i in items];quoted=','.join(f'"{x}"' for x in cols)
                # pk / group come from this server's own preview (single-table mode uses ENTRY_ID / ORDER_KEY).
                pk=items[0].get('pk') or PK[table];group=items[0].get('group') or 'ORDMASID'
                c.execute(f'SELECT {quoted} FROM {OWNER}.{table} WHERE "{pk}"=:id AND "{group}"=:o FOR UPDATE WAIT 10',id=rid,o=preview['ordmasid'])
                got=c.fetchone()
                if not got or any(plain(g)!=i['old'] for g,i in zip(got,items)):
                    raise ValueError('This order was changed in Oracle after your preview. Reload the order and review again.')
                binds={f'v{n}':bind_value(registry[table][i['column']],i['new']) for n,i in enumerate(items)}
                assignments=','.join('"%s"=:v%d'%(i['column'],n) for n,i in enumerate(items))
                c.execute(f'UPDATE {OWNER}.{table} SET {assignments} WHERE "{pk}"=:pk',dict(binds,pk=rid))
                c.execute(f'SELECT {quoted} FROM {OWNER}.{table} WHERE "{pk}"=:id',id=rid)
                back=c.fetchone()
                if any(plain(g)!=i['new'] for g,i in zip(back,items)):raise ValueError(f'{table}: updated values did not match read-back.')
                for i in items:
                    c.execute('''INSERT INTO PP_CHANGE_LOG(CHANGESET,ORDMASID,TABLE_NAME,ROW_ID,COLUMN_NAME,OLD_VALUE,NEW_VALUE,APPROVER)
                        VALUES (:cs,:o,:t,:r,:c,:old,:new,:who)''',cs=changeset_id,o=preview['ordmasid'],t=table,r=rid,c=i['column'],
                        old=None if i['old'] is None else str(i['old'])[:4000],new=None if i['new'] is None else str(i['new'])[:4000],who=approver)
            conn.commit()
        except Exception:
            conn.rollback();raise
    return dict(already_applied=False,applied=len(preview['changes']),verified=True)

def change_history(ordmasid):
    with connect() as conn:
        c=conn.cursor()
        c.execute('''SELECT CHANGESET,TABLE_NAME,ROW_ID,COLUMN_NAME,OLD_VALUE,NEW_VALUE,APPROVER,CHANGED_AT FROM PP_CHANGE_LOG
            WHERE ORDMASID=:o ORDER BY CHANGED_AT DESC,ID FETCH FIRST 500 ROWS ONLY''',o=ordmasid)
        return [dict(changeset=a,table=b,row_id=r,column=col,old=o,new=n,approver=w,changed_at=t.isoformat()) for a,b,r,col,o,n,w,t in c]
