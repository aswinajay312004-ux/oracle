from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import uuid
from .config import DATA

@contextmanager
def db():
    with sqlite3.connect(DATA/'poppys.sqlite3',timeout=20) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        yield conn

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,draft_id TEXT,version INTEGER,actor TEXT,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,active INTEGER NOT NULL,
            payload TEXT NOT NULL,created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(name,version));
        CREATE TABLE IF NOT EXISTS relationships(id INTEGER PRIMARY KEY,parent TEXT,parent_column TEXT,child TEXT,child_column TEXT,
            note TEXT,active INTEGER NOT NULL DEFAULT 1,created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,retired_at TEXT);
        CREATE TABLE IF NOT EXISTS change_sets(id TEXT PRIMARY KEY,ordmasid INTEGER,payload TEXT NOT NULL,status TEXT NOT NULL,
            created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,applied_at TEXT);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY,at TEXT DEFAULT CURRENT_TIMESTAMP,actor TEXT,action TEXT,subject TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS learned_labels(table_name TEXT NOT NULL,column_name TEXT NOT NULL,label TEXT NOT NULL,
            created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(table_name,column_name,label));
        ''')

def identify(orders):
    for o in orders:
        o.setdefault('id',uuid.uuid4().hex)
        for key in ('lines','order_lines','sample_lines'):
            for line in o.get(key,[]):
                line.setdefault('id',uuid.uuid4().hex)
                for s in line.get('sizes',[]):s.setdefault('id',uuid.uuid4().hex)
    return orders

def get(id):
    with db() as c:
        row=c.execute('SELECT payload FROM drafts WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError('Draft not found')
        return json.loads(row['payload'])

def save(draft,actor,expected=None):
    with db() as c:
        payload=json.dumps(draft,ensure_ascii=False)
        if expected is None:
            c.execute('INSERT INTO drafts VALUES (?,?,?)',(draft['id'],draft['version'],payload))
        else:
            cursor=c.execute('UPDATE drafts SET version=?,payload=? WHERE id=? AND version=?',
                (draft['version'],payload,draft['id'],expected))
            if cursor.rowcount!=1:raise ValueError('Draft changed in another window. Reload before continuing.')
        c.execute('INSERT INTO revisions(draft_id,version,actor,payload) VALUES (?,?,?,?)',(draft['id'],draft['version'],actor,payload))

def summaries():
    with db() as c:
        return [dict(id=d['id'],version=d['version'],filename=d['filename'],format=d['format'],created_at=d.get('created_at'),
                     committed=bool(d.get('result')),orders=len(d['orders']),refs=[o.get('order_ref') for o in d['orders']])
                for r in c.execute('SELECT payload FROM drafts ORDER BY rowid DESC LIMIT 200') for d in [json.loads(r[0])]]

def log(actor,action,subject='',detail=None):
    with db() as c:
        c.execute('INSERT INTO activity(actor,action,subject,detail) VALUES (?,?,?,?)',(actor,action,subject,json.dumps(detail or {},ensure_ascii=False,default=str)))

def activity(limit=20):
    with db() as c:
        return [dict(r) for r in c.execute('SELECT at,actor,action,subject FROM activity ORDER BY id DESC LIMIT ?',(limit,))]

def templates(active_only=False):
    with db() as c:
        rows=c.execute('SELECT * FROM templates'+(' WHERE active=1' if active_only else '')+' ORDER BY name,version DESC').fetchall()
        return [dict(id=r['id'],name=r['name'],version=r['version'],active=bool(r['active']),created_by=r['created_by'],
                     created_at=r['created_at'],**json.loads(r['payload'])) for r in rows]

def save_template(name,payload,actor):
    # New versions never overwrite old ones; only the newest is active unless rolled back.
    with db() as c:
        version=(c.execute('SELECT max(version) FROM templates WHERE name=?',(name,)).fetchone()[0] or 0)+1
        c.execute('UPDATE templates SET active=0 WHERE name=?',(name,))
        c.execute('INSERT INTO templates(name,version,active,payload,created_by) VALUES (?,?,?,?,?)',(name,version,1,json.dumps(payload,ensure_ascii=False),actor))
        return version

def activate_template(id,active):
    with db() as c:
        row=c.execute('SELECT name FROM templates WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError('Template not found')
        if active:c.execute('UPDATE templates SET active=0 WHERE name=?',(row['name'],))
        c.execute('UPDATE templates SET active=? WHERE id=?',(1 if active else 0,id))

def app_relationships(include_retired=False):
    with db() as c:
        return [dict(r,kind='application') for r in c.execute('SELECT * FROM relationships'+('' if include_retired else ' WHERE active=1')+' ORDER BY id')]

def add_relationship(r,actor):
    with db() as c:
        c.execute('INSERT INTO relationships(parent,parent_column,child,child_column,note,created_by) VALUES (?,?,?,?,?,?)',
            (r['parent'],r['parent_column'],r['child'],r['child_column'],r.get('note',''),actor))

def retire_relationship(id):
    with db() as c:
        if c.execute("UPDATE relationships SET active=0,retired_at=CURRENT_TIMESTAMP WHERE id=? AND active=1",(id,)).rowcount!=1:raise KeyError('Relationship not found')

def save_change_set(id,ordmasid,payload,actor,status='pending'):
    with db() as c:
        c.execute('INSERT INTO change_sets(id,ordmasid,payload,status,created_by) VALUES (?,?,?,?,?)',(id,ordmasid,json.dumps(payload,ensure_ascii=False,default=str),status,actor))

def change_set(id):
    with db() as c:
        row=c.execute('SELECT * FROM change_sets WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError('Change not found')
        return dict(row,payload=json.loads(row['payload']))

def mark_change_set(id,status):
    with db() as c:c.execute('UPDATE change_sets SET status=?,applied_at=CURRENT_TIMESTAMP WHERE id=?',(status,id))

def edited_orders(old_orders, incoming):
    # Keep original workbook provenance and snapshots server-owned, even after user corrections.
    result=[];old_by_id={o['id']:o for o in old_orders}
    for o in incoming:
        original=old_by_id.get(o.get('id'))
        if not original:
            if o.get('id'):raise ValueError('Unknown order identity. Reload this draft.')
            from .workbooks import base_order
            original=base_order()
            original['id']=uuid.uuid4().hex
            original['warnings']=['Manually entered order. Review every value before accepting.']
        new=deepcopy(original)
        for field in ('buyer','style','description','po_number','order_ref','ship_date','order_date','season','merch','sample_type','pieces_per_unit','quantity_basis'):
            if field in o:
                if field in new['provenance'] and str(o[field] or '')!=str(new.get(field) or ''):
                    # A typed value replaces the Excel value; keep the original for comparison.
                    new['provenance'][field]=dict(new['provenance'][field],via='Manual correction',excel_value=new['provenance'][field].get('value'))
                new[field]=o[field]
        new['extras']=merge_extras(original.get('extras',{}),o.get('extras',{}))
        new['table_values']=merge_table_values(original.get('table_values',{}),o.get('table_values',{}))
        existing_lines={l['id']:l for key in ('lines','order_lines','sample_lines') for l in original.get(key,[])}
        new['lines']=[]
        for line in o.get('lines',[]):
            source_line=existing_lines.get(line.get('id'),{})
            clean=deepcopy(source_line) or dict(id=uuid.uuid4().hex,provenance={})
            for f in ('label','color'):clean[f]=str(line.get(f,''))
            clean['extras']=merge_extras(source_line.get('extras',{}),line.get('extras',{}))
            clean['sizes']=[]
            originals={s['id']:s for s in source_line.get('sizes',[])}
            changed=False
            for s in line.get('sizes',[]):
                prev=originals.get(s.get('id'),{})
                row=deepcopy(prev) or dict(id=uuid.uuid4().hex,source={'origin':'Manual correction'})
                row['size']=str(s.get('size',''));row['quantity']=s.get('quantity')
                if row['size']!=prev.get('size') or str(row['quantity'])!=str(prev.get('quantity')):changed=True
                clean['sizes'].append(row)
            changed=changed or len(clean['sizes'])!=len(source_line.get('sizes',[]))
            # Only an actual quantity/size edit can waive the original workbook total comparison.
            clean['declared_total']=None if changed else source_line.get('declared_total')
            clean['original_total']=source_line.get('original_total',source_line.get('declared_total'))
            new['lines'].append(clean)
        result.append(new)
    return result

def merge_extras(old,incoming):
    """Typed extra-column values keep the server-held Excel source only while unchanged."""
    result={}
    for col,item in (incoming or {}).items():
        value=item.get('value') if isinstance(item,dict) else item
        prev=old.get(col)
        if prev and str(prev.get('value'))==str(value):result[col]=prev
        elif value not in (None,''):result[col]=dict(value=value,source={'origin':'Manual correction'})
    return result

def merge_table_values(old,incoming):
    """Single-table mode values. Unchanged values keep their server-held Excel source; a typed value is a
    manual entry; an emptied value is an explicit blank (an Excel label match must not refill it)."""
    result={}
    for col,item in (incoming or {}).items():
        value=item.get('value') if isinstance(item,dict) else item
        prev=old.get(col)
        if isinstance(item,dict) and item.get('na'):result[col]=dict(value=None,source={'origin':'Not applicable'})
        elif prev and str(prev.get('value'))==str(value) and (value is None)==(prev.get('value') is None):result[col]=prev
        elif value in (None,''):result[col]=dict(value=None,source={'origin':'Left blank by you'})
        else:result[col]=dict(value=value,source={'origin':'Typed by you'})
    for col,prev in (old or {}).items():
        result.setdefault(col,prev)
    return result

def learn(table,column,label,actor):
    """Remember that an Excel label means this column (from the person's own mapping), so the next upload matches it."""
    with db() as c:
        c.execute('DELETE FROM learned_labels WHERE table_name=? AND label=? AND column_name<>?',(table,label,column))
        c.execute('INSERT OR IGNORE INTO learned_labels(table_name,column_name,label,created_by) VALUES (?,?,?,?)',(table,column,label,actor))

def forget(table,column,label=None):
    with db() as c:
        if label is None:c.execute('DELETE FROM learned_labels WHERE table_name=? AND column_name=?',(table,column))
        else:c.execute('DELETE FROM learned_labels WHERE table_name=? AND column_name=? AND label=?',(table,column,label))

def learned():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT table_name,column_name,label,created_by,created_at FROM learned_labels ORDER BY table_name,column_name,label')]
