"""Independent SQL safety review: the second, separate check on every statement that reaches a database
through the read-only executors (Ask AI, explorer, value index, live lookups).

The compiler (query.py) already builds only safe SELECTs from the catalog. This module does not trust that:
it parses the finished SQL with SQLGlot (MIT, pure Python, works offline) and walks the syntax tree, so a bug
in the compiler, a hand-written explorer query or a future code path cannot send anything else. It fails
closed: SQL it cannot parse is refused.

Rules (all statements):
  * exactly one statement, and it is a query (SELECT / WITH / set operation of SELECTs);
  * no DML, DDL, PL/SQL, MERGE, CALL, locking clause (FOR UPDATE) or hierarchical CONNECT BY anywhere;
  * no database links (`@`), no dictionary or other schemas: on Oracle every table is OWNER.TABLE
    (or a WITH name / derived table of the same statement); on SQLite no sqlite_* tables;
  * functions come from an allowlist of pure built-ins: no package calls (DBMS_*, UTL_*), no user-defined
    functions (an autonomous-transaction function could write even inside a read-only transaction).
review() adds the rules for compiled answers: only catalog tables, and a row limit.
Successful parsing is not proof that a query is correct (SQLGlot says so itself); correctness is the job of
the compiler and of verify.py. This module only decides whether a statement may run at all.
"""
from __future__ import annotations
import functools

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .query import QueryError

QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
FORBIDDEN = tuple(getattr(exp, n) for n in (
    'Insert', 'Update', 'Delete', 'Merge', 'Create', 'Drop', 'Alter', 'AlterTable', 'Command', 'Commit', 'Rollback',
    'Transaction', 'Grant', 'Revoke', 'TruncateTable', 'Use', 'Set', 'Into', 'Lock', 'Connect', 'Pragma', 'LoadData',
    'Copy', 'Analyze', 'Execute', 'Describe') if hasattr(exp, n))
# Built-in functions the product's SQL uses, plus common pure ones. Names are SQLGlot class names
# (typed functions) or upper-case Oracle names (functions SQLGlot keeps as anonymous calls).
ALLOWED_FUNCTIONS = {
    'Count', 'Sum', 'Avg', 'Min', 'Max', 'Upper', 'Lower', 'Trim', 'ToChar', 'Substring', 'Cast', 'TryCast', 'Coalesce',
    'Round', 'Abs', 'Length', 'Concat', 'DPipe', 'If', 'Case', 'Nullif', 'Floor', 'Ceil', 'Greatest', 'Least', 'StrToDate',
    'TimeToStr', 'TsOrDsToDate', 'DateTrunc', 'TimestampTrunc', 'Extract', 'Pad', 'Lpad', 'Rpad', 'StrPosition', 'Trunc',
    'ToNumber', 'Stddev', 'Variance', 'CurrentDate', 'CurrentTimestamp', 'Mod', 'Sign', 'Initcap', 'Replace',
    'RPAD', 'LPAD', 'NVL', 'NVL2', 'DECODE', 'SUBSTR', 'INSTR', 'TO_DATE', 'TO_NUMBER', 'TRUNC', 'INITCAP', 'REPLACE',
    'LENGTH', 'SYSDATE', 'ADD_MONTHS', 'MONTHS_BETWEEN', 'LAST_DAY', 'ROUND', 'TO_CHAR', 'UPPER', 'LOWER', 'TRIM',
}
NOT_FUNCTIONS = (exp.Connector, exp.Binary, exp.Predicate, exp.Not, exp.Paren, exp.Neg, exp.Distinct, exp.Exists,
                 exp.Column, exp.Literal, exp.Placeholder, exp.Star, exp.Null, exp.Boolean, exp.Alias, exp.DataType,
                 exp.Ordered, exp.Interval, exp.Bracket, exp.Between, exp.In, exp.Is, exp.Like, exp.Escape)


class UnsafeSQL(QueryError):
    """A statement the safety review refuses. Nothing was sent to the database."""


@functools.lru_cache(maxsize=2048)
def _parse(sql, dialect):
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (SqlglotError, RecursionError, ValueError) as exc:
        raise UnsafeSQL(f'The safety check could not read this query, so it was not run ({str(exc)[:120]}).') from None
    if len(statements) != 1:
        raise UnsafeSQL('Only one statement may run at a time.')
    return statements[0]


def _local_names(tree):
    """WITH names and derived-table aliases: tables that exist only inside this statement."""
    names = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    names |= {s.alias.upper() for s in tree.find_all(exp.Subquery) if s.alias}
    return names


def check(sql, dialect='oracle', owner=None):
    """Raises UnsafeSQL unless sql is one read-only query over the owner's tables with allowed functions.
    Returns the list of OWNER-free table names it reads."""
    if '@' in sql.replace("'@'", ''):   # a database link (table@link) reaches another database
        raise UnsafeSQL('Queries through database links are not allowed.')
    tree = _parse(sql, 'oracle' if dialect == 'oracle' else 'sqlite')
    if not isinstance(tree, QUERY_ROOTS):
        raise UnsafeSQL('Only read-only SELECT queries may run.')
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise UnsafeSQL(f'{type(node).__name__.upper()} is not allowed in a read-only query.')
        if isinstance(node, exp.Select) and (node.args.get('locks') or node.args.get('connect') or node.args.get('into')):
            raise UnsafeSQL('Locking, CONNECT BY and SELECT INTO are not allowed in a read-only query.')
        if isinstance(node, exp.Func) and not isinstance(node, NOT_FUNCTIONS):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else type(node).__name__
            if isinstance(node.parent, exp.Dot) or name not in ALLOWED_FUNCTIONS:
                raise UnsafeSQL(f'The function {node.sql(dialect="oracle")[:60]} is not on the list of allowed functions.')
    local = _local_names(tree); read = []
    for t in tree.find_all(exp.Table):
        name = t.name.upper(); schema = (t.db or '').upper()
        if t.catalog or not name:
            raise UnsafeSQL('Only tables of the ERP schema may be read.')
        if name in local and not schema:
            continue
        if dialect == 'oracle':
            if not owner or schema != owner.upper():
                raise UnsafeSQL(f'{(schema + "." if schema else "") + name} is outside the ERP schema {owner}; only its tables may be read.')
        elif schema or name.startswith('SQLITE_'):
            raise UnsafeSQL(f'{name} may not be read.')
        read.append(name)
    return sorted(set(read))


def review(comp, kb, dialect):
    """The safety review of a compiled answer, as a check entry for the answer's trace. Raises UnsafeSQL."""
    tables = check(comp['sql'], dialect.name, dialect.owner)
    unknown = [t for t in tables if not kb.table(t)]
    if unknown:
        raise UnsafeSQL(f'{", ".join(unknown)} is not in the catalog, so the query was not run.')
    tree = _parse(comp['sql'], 'oracle' if dialect.name == 'oracle' else 'sqlite')
    if not (tree.args.get('limit') or tree.args.get('fetch')):
        raise UnsafeSQL('A question query must have a row limit.')
    known = {j['id'] for j in kb.joins}
    stray = [j['id'] for j in comp.get('joins') or [] if j['id'] not in known]
    if stray:
        raise UnsafeSQL('The query uses a link that is not in the relationship map, so it was not run.')
    return dict(stage='safety', role='Oracle security reviewer', status='passed',
                title='Safety review passed',
                detail=f'One read-only SELECT over {len(tables)} catalog table{"s" if len(tables) != 1 else ""} '
                       f'({", ".join(tables)}), values sent separately as bind variables, row limit {comp["limit"]}, '
                       'only allowed built-in functions, no database links.')
