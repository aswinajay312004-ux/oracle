"""Evidence ledger: why POPPYS AI believes a column mapping or a table relationship is correct.

The ledger rules follow REA's evidence ledger (github.com/morluto/rea, MIT; design read from its source in
the first research pass, docs/OPEN_SOURCE_RESEARCH.md). No REA code is used at runtime:

* Records are immutable. A correction is a new record that names the record it supersedes; the old one stays.
* Recording the same evidence again is a no-op (idempotent), so retries and re-imports are safe.
* The same source making a different claim about the same subject is a conflict and is refused, unless the
  new record explicitly supersedes the old one.
* A bundle is validated completely before anything is stored, then stored in one transaction.
* Open questions are kept separately and closed only with a record that answers them.

Subjects: 'column:ORDMAS.DOCID', 'table:ORDMAS', 'join:<relationship id>'. Name similarity is never
evidence: marking something verified needs a reviewer's statement, and the facts known at that moment
(Oracle constraint, discovery match rates, the registry's ERP-screen note) are recorded with it.
"""
from __future__ import annotations
import hashlib
import json
import re

from . import local_store as store

KINDS = {
    'business_decision': 'Reviewer statement',
    'erp_screen': 'Axpert screen / field definition',
    'declared_fk': 'Oracle foreign-key constraint',
    'discovery': 'Schema discovery on the live database',
    'verified_sql': 'Query checked against real records',
    'readback': 'Saved order read back from Oracle',
    'application_code': 'Existing application code',
}
SUBJECT = re.compile(r'^(column:[A-Z0-9_$#]+\.[A-Z0-9_$#]+|table:[A-Z0-9_$#]+|join:[A-Z0-9_$#]+->[A-Z0-9_$#]+:[A-Z0-9_$#=,]+)$')
MIN_STATEMENT = 15


class EvidenceError(ValueError):
    """Invalid or conflicting evidence; nothing from the bundle was stored."""


def init():
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, subject TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
            claim TEXT NOT NULL, detail TEXT NOT NULL, supersedes TEXT, actor TEXT NOT NULL, at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_subject ON evidence(subject);
        CREATE TABLE IF NOT EXISTS evidence_questions(id INTEGER PRIMARY KEY, subject TEXT NOT NULL, question TEXT NOT NULL,
            actor TEXT NOT NULL, at TEXT NOT NULL, answered_by TEXT, answered_at TEXT, answer_evidence TEXT);
        ''')


def _canonical(r):
    return json.dumps(dict(subject=r['subject'], kind=r['kind'], source=r['source'], claim=r['claim'],
                           detail=r.get('detail') or {}, supersedes=r.get('supersedes')),
                      sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def _validate(r):
    if not isinstance(r, dict):
        raise EvidenceError('Each evidence record must be an object.')
    subject, kind = str(r.get('subject', '')), r.get('kind')
    if not SUBJECT.match(subject):
        raise EvidenceError(f'Unknown evidence subject "{subject[:80]}".')
    if kind not in KINDS:
        raise EvidenceError(f'Unknown evidence kind "{kind}". Use one of: {", ".join(KINDS)}.')
    claim = ' '.join(str(r.get('claim', '')).split())
    if len(claim) < (MIN_STATEMENT if kind == 'business_decision' else 3) or len(claim) > 1000:
        raise EvidenceError('Say what shows this is correct (for example the Axpert screen and field, or the order '
                            f'whose saved record you checked) — at least {MIN_STATEMENT} characters.' if kind == 'business_decision'
                            else 'Evidence claim must be 3–1000 characters.')
    source = ' '.join(str(r.get('source', '')).split())[:200]
    if not source:
        raise EvidenceError('Evidence needs a source (who or what produced it).')
    detail = r.get('detail') or {}
    if not isinstance(detail, dict) or len(json.dumps(detail, default=str)) > 20000:
        raise EvidenceError('Evidence detail must be a small object.')
    out = dict(subject=subject, kind=kind, source=source, claim=claim, detail=detail, supersedes=r.get('supersedes') or None)
    out['id'] = hashlib.sha256(_canonical(out).encode()).hexdigest()[:32]
    return out


def record(bundle, actor):
    """Store a bundle of evidence records atomically. Returns the stored (or already present) records."""
    if not bundle or len(bundle) > 200:
        raise EvidenceError('An evidence bundle holds 1–200 records.')
    rows = [_validate(r) for r in bundle]                     # the whole bundle is checked before anything is stored
    with store.db() as c:
        for r in rows:
            if r['supersedes'] and not c.execute('SELECT 1 FROM evidence WHERE id=? AND subject=?', (r['supersedes'], r['subject'])).fetchone():
                raise EvidenceError(f'Superseded evidence {r["supersedes"]} does not exist for {r["subject"]}.')
            if c.execute('SELECT 1 FROM evidence WHERE id=?', (r['id'],)).fetchone():
                continue                                       # identical evidence: idempotent
            superseded = {x[0] for x in c.execute('SELECT supersedes FROM evidence WHERE subject=? AND supersedes IS NOT NULL', (r['subject'],))}
            clash = [x for x in c.execute('SELECT id, claim FROM evidence WHERE subject=? AND kind=? AND source=?', (r['subject'], r['kind'], r['source']))
                     if x[0] not in superseded and x[0] != r['supersedes']]
            if clash:
                raise EvidenceError(f'{r["source"]} already recorded different {KINDS[r["kind"]].lower()} for {r["subject"]}: '
                                    f'"{clash[0][1][:120]}". Supersede that record to correct it.')
        now = store.now()
        for r in rows:
            c.execute('INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)',
                      (r['id'], r['subject'], r['kind'], r['source'], r['claim'], json.dumps(r['detail'], ensure_ascii=False, default=str),
                       r['supersedes'], actor, now))
    return [dict(r, actor=actor) for r in rows]


def for_subject(subject, include_superseded=False):
    with store.db() as c:
        rows = [dict(id=x['id'], subject=x['subject'], kind=x['kind'], kind_text=KINDS.get(x['kind'], x['kind']), source=x['source'],
                     claim=x['claim'], detail=json.loads(x['detail']), supersedes=x['supersedes'], actor=x['actor'], at=x['at'])
                for x in c.execute('SELECT * FROM evidence WHERE subject=? ORDER BY at, id', (subject,))]
    gone = {r['supersedes'] for r in rows if r['supersedes']}
    for r in rows:
        r['current'] = r['id'] not in gone
    return rows if include_superseded else [r for r in rows if r['current']]


def counts(prefix=''):
    """{subject: number of current records} for every subject starting with prefix."""
    with store.db() as c:
        rows = c.execute('SELECT subject, id, supersedes FROM evidence WHERE subject LIKE ?', (prefix + '%',)).fetchall()
    gone = {r['supersedes'] for r in rows if r['supersedes']}
    out = {}
    for r in rows:
        if r['id'] not in gone:
            out[r['subject']] = out.get(r['subject'], 0) + 1
    return out


def ask(subject, question, actor):
    if not SUBJECT.match(subject) or not (5 <= len(question.strip()) <= 1000):
        raise EvidenceError('A question needs a known subject and 5–1000 characters.')
    with store.db() as c:
        cur = c.execute('INSERT INTO evidence_questions(subject,question,actor,at) VALUES (?,?,?,?)', (subject, question.strip(), actor, store.now()))
        return cur.lastrowid


def answer(qid, evidence_id, actor):
    with store.db() as c:
        q = c.execute('SELECT subject, answered_at FROM evidence_questions WHERE id=?', (qid,)).fetchone()
        if not q:
            raise EvidenceError('Unknown question.')
        if q['answered_at']:
            raise EvidenceError('This question is already answered.')
        if not c.execute('SELECT 1 FROM evidence WHERE id=? AND subject=?', (evidence_id, q['subject'])).fetchone():
            raise EvidenceError('Answer a question with evidence recorded for the same subject.')
        c.execute('UPDATE evidence_questions SET answered_by=?, answered_at=?, answer_evidence=? WHERE id=?', (actor, store.now(), evidence_id, qid))


def questions(subject=None, open_only=True):
    sql = 'SELECT * FROM evidence_questions' + (' WHERE subject=?' if subject else ' WHERE 1=1') + (' AND answered_at IS NULL' if open_only else '') + ' ORDER BY id DESC LIMIT 500'
    with store.db() as c:
        return [dict(r) for r in c.execute(sql, (subject,) if subject else ())]


def verification_bundle(subject, statement, actor, facts):
    """The records stored when a reviewer marks a subject verified: their statement plus the known facts."""
    bundle = [dict(subject=subject, kind='business_decision', source=f'reviewer:{actor}', claim=statement,
                   supersedes=_current_id(subject, 'business_decision', f'reviewer:{actor}', statement))]
    bundle += [dict(f, subject=subject) for f in facts]
    return bundle


def _current_id(subject, kind, source, claim):
    """A reviewer who changes their statement supersedes their own earlier one (same statement: no-op)."""
    mine = [r for r in for_subject(subject) if r['kind'] == kind and r['source'] == source]
    return mine[-1]['id'] if mine and mine[-1]['claim'] != ' '.join(claim.split()) else None
