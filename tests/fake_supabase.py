"""An in-memory stand-in for the slice of the Supabase client the AI layer uses.

It really filters. A query that forgets `.eq("user_id", ...)` gets every
account's rows back, exactly as the service-role key would in production, so
tenancy tests built on it fail when scoping is missing rather than passing by
construction. `select("a,b")` also returns only those columns, so a field the
code did not ask for (a device's IP address) cannot leak through it.
"""
from datetime import datetime


def _cmp_value(v):
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    return v


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._columns = None
        self._filters = []
        self._order = None
        self._limit = None

    def select(self, columns="*"):
        self._columns = None if columns.strip() == "*" else [c.strip() for c in columns.split(",")]
        return self

    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, values):
        values = list(values)
        self._filters.append(lambda r: r.get(col) in values)
        return self

    def gte(self, col, val):
        self._filters.append(lambda r: r.get(col) is not None and _cmp_value(r[col]) >= _cmp_value(val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self._store.queries.append(self._table)
        if self._store.fail:
            raise RuntimeError("database unreachable")
        rows = [r for r in self._store.tables.get(self._table, []) if all(f(r) for f in self._filters)]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: _cmp_value(r.get(col)) or "", reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._columns:
            rows = [{c: r.get(c) for c in self._columns} for r in rows]
        return _Result([dict(r) for r in rows])


class FakeSupabase:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.queries = []
        self.fail = False

    def table(self, name):
        return _Query(self, name)
