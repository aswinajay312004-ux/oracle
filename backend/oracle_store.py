"""Restricted, bind-only sample order writer for the verified GARMENT_DEMO schema."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
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

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()

def connect():
    if not setting('ORACLE_PASSWORD'):
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    return oracledb.connect(user=setting('ORACLE_USER'),password=setting('ORACLE_PASSWORD'),
        dsn=setting('ORACLE_DSN','127.0.0.1:1521/FREEPDB1'),tcp_connect_timeout=4)

def schema(conn):
    c=conn.cursor()
    c.execute('''select table_name,column_name,data_type,data_length,data_precision,data_scale,nullable
        from all_tab_columns where owner=:owner order by table_name,column_id''',owner=OWNER)
    result={t:{} for t in TABLES}
    for table,col,kind,length,precision,scale,nullable in c:
        if table in TABLES:
            result[table][col]=dict(type=kind,length=length,precision=precision,scale=scale,nullable=nullable)
    for t,pk in PK.items():
        if pk not in result[t]: raise ValueError(f'Required Oracle table/column missing: {t}.{pk}')
    return result

def masters(conn):
    result={};c=conn.cursor()
    for role,(table,pk,label) in MASTERS.items():
        c.execute(f'SELECT {pk},{label},CANCEL FROM {OWNER}.{table} ORDER BY {pk} FETCH FIRST 5001 ROWS ONLY')
        rows=c.fetchall()
        if len(rows)>5000: raise ValueError(f'{table}: master catalogue exceeds demo limit; configure a scoped lookup.')
        result[role]=[dict(id=k,label=str(v).strip(),cancelled=str(cancel or '').upper()=='T') for k,v,cancel in rows if v is not None]
    return result

def normalize(s): return ' '.join(str(s or '').strip().upper().split())

def count(value, name, errors, allow_zero=True):
    try:
        if isinstance(value,bool) or value is None or str(value).strip()=='': raise ValueError()
        d=Decimal(str(value))
        if not d.is_finite() or d!=d.to_integral_value() or d<0 or d>999999999: raise ValueError()
        if d==0 and not allow_zero: raise ValueError()
        return int(d)
    except (ValueError,InvalidOperation):
        errors.append(f'{name}: enter a {"non-negative" if allow_zero else "positive"} whole number.');return 0

def plan(conn, orders, filehash):
    registry=schema(conn); lookups=masters(conn); operations=[]; errors=[];warnings=[];known={}
    totals=dict(orders=len(orders),lines=0,pieces=0)
    if not orders or len(orders)>50: errors.append('Import must contain 1–50 orders.')
    c=conn.cursor()
    c.execute('select JOBID from PP_IMPORT_LOG where FILEHASH=:h fetch first 1 rows only',h=filehash)
    if c.fetchone(): errors.append('This exact workbook has already been committed. Open Import history to view it.')
    def add(key,table,values,action='insert',provenance=None):
        operations.append(dict(key=key,table=table,action=action,values=values,source=provenance or {}))
        return '$'+key
    def master(role,label,where,provenance=None):
        label=' '.join(str(label or '').strip().split())
        if not label: errors.append(f'{where}: {role} is required.');return None
        table,pk,col=MASTERS[role]; code=(role,normalize(label))
        if code in known:return known[code]
        matches=[x for x in lookups[role] if normalize(x['label'])==normalize(label)]
        if len(matches)>1 or (matches and matches[0]['id'] is None):
            errors.append(f'{where}: ambiguous {role} master "{label}". Resolve duplicate master records.');return None
        key=f'm{len(known)+1}'
        if matches and matches[0]['cancelled']:
            errors.append(f'{where}: {role} master "{label}" is cancelled.');return None
        if matches:
            val=matches[0]['id'];add(key,table,{pk:val,col:matches[0]['label']},'reuse',provenance)
        else:
            val=add(key,table,{pk:'$'+key,col:label},provenance=provenance)
        known[code]=val;return val
    seen=set()
    for index,o in enumerate(orders):
        prefix=f'Order {index+1}'; ref=str(o.get('order_ref','')).strip()
        if not ref:errors.append(f'{prefix}: enter a unique sample order reference.')
        elif normalize(ref) in seen:errors.append(f'{prefix}: duplicate order reference in this workbook.')
        seen.add(normalize(ref))
        c.execute(f'SELECT ORDMASID FROM {OWNER}.ORDMAS WHERE upper(trim(DOCID))=:ref FETCH FIRST 1 ROWS ONLY',ref=normalize(ref))
        if c.fetchone():errors.append(f'{prefix}: order reference {ref} already exists. Existing orders are not overwritten.')
        if o.get('quantity_basis') not in ('sample','order_demo'):
            errors.append(f'{prefix}: confirm sample quantities or explicitly use order quantities for this demo.')
        if o.get('quantity_basis')=='order_demo':warnings.append(f'{prefix}: source order quantities will be imported as a DEMO SAMPLE order.')
        factor=count(o.get('pieces_per_unit'),prefix+' pieces per source unit',errors,False)
        dates={}
        for key in ('ship_date','order_date'):
            try:
                dates[key]=datetime.strptime(str(o.get(key,'')),'%Y-%m-%d').strftime('%Y-%m-%d')
            except ValueError:errors.append(f'{prefix}: {key.replace("_"," ")} is required in YYYY-MM-DD format.')
        if len(dates)==2 and dates['ship_date']<dates['order_date']:
            errors.append(f'{prefix}: ship date cannot precede order confirmation date.')
        if not str(o.get('po_number','')).strip():errors.append(f'{prefix}: PO / contract number is required.')
        sample_type=str(o.get('sample_type','')).strip()
        if not sample_type:errors.append(f'{prefix}: sample type is required.')
        refs={role:master(role,o.get(role),prefix,o.get('provenance',{}).get(role)) for role in ('buyer','style','description')}
        uom=master('uom','PCS',prefix,{'derivation':'Quantities converted to pieces using reviewed pieces_per_unit'})
        merch=master('merch',o['merch'],prefix) if o.get('merch') else None
        orderkey=f'o{index+1}'; ordertotal=0; prodtotal=0
        # Only proven demo meanings populated. In particular ORDNO is not a string reference.
        head={ 'ORDMASID':'$'+orderkey,'DOCID':ref,'BUYER':refs['buyer'],'STYLENO':refs['style'],
            'STYLEDESC':refs['description'],'BONO':str(o.get('po_number','')).strip(),
            'ODATE':dates.get('order_date'),'UOM':uom,'SAMPLETYPE':sample_type,
            'ORDCATEGORY':'SAMPLE ORDER','REMARKS':'POPPYS demo sample import; quantity basis: '+str(o.get('quantity_basis'))}
        if merch is not None:head['MERCH']=merch
        add(orderkey,'ORDMAS',head,provenance=o.get('provenance',{}))
        lines=o.get('lines',[])
        if not lines or len(lines)>100:errors.append(f'{prefix}: provide 1–100 garment lines.')
        for li,line in enumerate(lines):
            where=f'{prefix}, line {li+1}';linekey=f'{orderkey}l{li+1}';total=0
            color=master('color',line.get('color'),where,line.get('provenance',{}).get('color'))
            sizes=line.get('sizes',[]);sizes_seen=set();sizedata=[]
            if not sizes or len(sizes)>100:errors.append(f'{where}: provide 1–100 sizes.')
            for si,s in enumerate(sizes):
                name=str(s.get('size','')).strip()
                if normalize(name) in sizes_seen:errors.append(f'{where}: duplicate size {name}; review before combining quantities.')
                sizes_seen.add(normalize(name))
                qty=count(s.get('quantity'),f'{where}, size {name}',errors)
                sizeid=master('size',name,where,s.get('size_source'))
                total+=qty
                sizedata.append((si,sizeid,qty*factor,s))
            if line.get('declared_total') is not None:
                declared=count(line['declared_total'],where+' source total',errors)
                if declared!=total:errors.append(f'{where}: size total {total} differs from source total {declared}.')
            if total==0: errors.append(f'{where}: sample quantities total zero; enter sample quantities or select the order quantities explicitly for the demo.')
            pieces=total*factor;ordertotal+=pieces;prodtotal+=pieces;totals['lines']+=1
            subno=f'{ref}:{li+1}'
            add(linekey,'ORDSTYLE',dict(ORDSTYLEID='$'+linekey,ORDMASID='$'+orderkey,
                STNO=refs['style'],STYLENAME=refs['description'],PONUM=head['BONO'],SUBNO=subno,
                COMBO=color,ORDQTY=pieces,PRODQTY=pieces,SHIPDT=dates.get('ship_date'),PUOM=uom,SNO=li+1),provenance=line.get('provenance'))
            for si,sizeid,qty,s in sizedata:
                sizekey=f'{linekey}s{si+1}'
                add(sizekey,'ORDSIZE',dict(ORDSIZEID='$'+sizekey,ORDMASID='$'+orderkey,
                    SUB3_SUBNO=subno,GSIZE=sizeid,RQTY=qty,PQTY=qty,ORDSIZEROW=si+1),provenance=s.get('source'))
        head['OQTY']=ordertotal
        totals['pieces']+=ordertotal
    # Fail closed against actual Oracle lengths/precision/nullability rather than silently truncate.
    for op in operations:
        if op['action']=='reuse':continue
        table=op['table'];vals=op['values']
        for col,meta in registry[table].items():
            if meta['nullable']=='N' and vals.get(col) is None:errors.append(f'{table}.{col}: mandatory database value missing.')
        for col,value in vals.items():
            meta=registry[table].get(col)
            if meta is None:errors.append(f'{table}.{col}: column absent in live database.');continue
            if value is None or (isinstance(value,str) and value.startswith('$')):continue
            if meta['type'] in ('VARCHAR2','CHAR') and len(str(value).encode('utf-8'))>meta['length']:
                errors.append(f'{table}.{col}: value exceeds {meta["length"]} bytes.')
            if meta['type']=='NUMBER':
                try:
                    value=Decimal(str(value))
                    if meta['precision'] and abs(value)>=Decimal(10)**(meta['precision']-(meta['scale'] or 0)):
                        errors.append(f'{table}.{col}: value exceeds numeric precision.')
                except InvalidOperation:errors.append(f'{table}.{col}: requires a number.')
    result=dict(operations=operations,errors=list(dict.fromkeys(errors)),warnings=warnings,totals=totals,schema_fingerprint=digest(registry))
    result['hash']=digest(result)
    return result

def read_result(cursor,jobid):
    cursor.execute('SELECT RESULT_JSON FROM PP_IMPORT_LOG WHERE JOBID=:id',id=jobid)
    row=cursor.fetchone()
    if not row:return None
    result=json.loads(row[0].read() if hasattr(row[0],'read') else row[0]);result['already_committed']=True
    return result

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
                    if v is not None and registry[table][col]['type']=='DATE':v=datetime.strptime(v,'%Y-%m-%d')
                    vals[col]=v
                # table and columns originate only from this server's allowlisted plan.
                cols=list(vals); binds={f'v{i}':vals[col] for i,col in enumerate(cols)}
                c.execute(f'INSERT INTO {OWNER}.{table} ({",".join(cols)}) VALUES ({",".join(":"+k for k in binds)})',binds)
                c.execute(f'SELECT {",".join(cols)} FROM {OWNER}.{table} WHERE {pk}=:id',id=ident)
                rows=c.fetchall()
                if len(rows)!=1 or list(rows[0])!=list(vals.values()):raise ValueError(f'{table}: inserted values did not match read-back.')
                inserted.append(dict(table=table,values={col:(v.isoformat()[:10] if isinstance(v,datetime) else v) for col,v in zip(cols,rows[0])}))
                if table=='ORDMAS':outorders.append(dict(order_ref=vals['DOCID'],ordmasid=ident))
            result=dict(job_id=draft['id'],orders=outorders,rows=inserted,committed_at=datetime.now().astimezone().isoformat(),already_committed=False)
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                job=draft['id'],filehash=draft['filehash'],hash=expected_hash,who=approver,payload=json.dumps(result,ensure_ascii=False))
            conn.commit();return result
        except Exception:
            conn.rollback();raise

def history():
    with connect() as conn:
        c=conn.cursor();c.execute('SELECT JOBID,PLANHASH,APPROVER,COMMITTED_AT FROM PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC FETCH FIRST 100 ROWS ONLY')
        return [dict(job_id=j,plan_hash=h,approver=a,committed_at=d.isoformat()) for j,h,a,d in c]
