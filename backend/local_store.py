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
        return [dict(id=d['id'],version=d['version'],filename=d['filename'],format=d['format'],
                     committed=bool(d.get('result')),orders=len(d['orders']))
                for r in c.execute('SELECT payload FROM drafts ORDER BY rowid DESC LIMIT 100') for d in [json.loads(r[0])]]

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
            if field in o:new[field]=o[field]
        existing_lines={l['id']:l for key in ('lines','order_lines','sample_lines') for l in original.get(key,[])}
        new['lines']=[]
        for line in o.get('lines',[]):
            source_line=existing_lines.get(line.get('id'),{})
            clean=deepcopy(source_line) or dict(id=uuid.uuid4().hex,provenance={})
            for f in ('label','color'):clean[f]=str(line.get(f,''))
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
