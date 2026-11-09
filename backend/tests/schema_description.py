"""A comparable description of a SQLite schema (D-173): what a migration chain creates.

Two databases have the same schema when their descriptions are equal: every table with its
columns in order (type, NOT NULL, default, primary key, collation), its constraints (named
CHECK, UNIQUE, FOREIGN KEY and PRIMARY KEY clauses, in any order), WITHOUT ROWID, the foreign
keys SQLite reads back, and every index with its columns, uniqueness and partial predicate.
SQLite's own index names (`sqlite_autoindex_<table>_<n>`) depend on the order of the clauses, so
those indexes are described by what they cover instead of by name. Whitespace and identifier
quotes in the stored SQL are not compared.
"""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

_SPACES = re.compile(r"\s+")
_CONSTRAINT = re.compile(r"^(CONSTRAINT|PRIMARY KEY|UNIQUE|CHECK|FOREIGN KEY)\b", re.IGNORECASE)


def _normalise(sql: str) -> str:
    return _SPACES.sub(" ", sql.replace('"', "")).strip()


def _clauses(create_table: str) -> list[str]:
    """The comma-separated clauses inside CREATE TABLE's outer parentheses."""
    body = create_table[create_table.index("(") + 1 : create_table.rindex(")")]
    clauses, depth, current = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            clauses.append("".join(current))
            current = []
        else:
            current.append(char)
    clauses.append("".join(current))
    return [_normalise(clause) for clause in clauses if clause.strip()]


def _table(db: sqlite3.Connection, name: str, sql: str) -> dict[str, Any]:
    clauses = _clauses(sql)
    columns = [clause for clause in clauses if not _CONSTRAINT.match(clause)]
    constraints = sorted(clause for clause in clauses if _CONSTRAINT.match(clause))
    info = [list(row) for row in db.execute(f'PRAGMA table_xinfo("{name}")')]
    foreign_keys = sorted(list(row[2:]) for row in db.execute(f'PRAGMA foreign_key_list("{name}")'))
    indexes: dict[str, Any] = {}
    for _, index, unique, origin, partial in db.execute(f'PRAGMA index_list("{name}")'):
        covered = [list(row[2:]) for row in db.execute(f'PRAGMA index_xinfo("{index}")')]
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (index,)
        ).fetchone()
        sql_of_index = row[0] if row else None  # none for an automatic index
        key = index if sql_of_index else f"auto:{origin}:{json.dumps(covered)}"
        indexes[key] = {
            "unique": unique,
            "origin": origin,
            "partial": partial,
            "columns": covered,
            "sql": _normalise(sql_of_index) if sql_of_index else None,
        }
    return {
        "columns": columns,
        "column_info": info,
        "constraints": constraints,
        "without_rowid": sql.rstrip().upper().endswith("WITHOUT ROWID"),
        "foreign_keys": foreign_keys,
        "indexes": dict(sorted(indexes.items())),
    }


def describe(db_path: Path) -> dict[str, Any]:
    """Every table of the database except `alembic_version`, described as above."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
            "AND name NOT IN ('alembic_version', 'sqlite_sequence') ORDER BY name"
        ).fetchall()
        return {name: _table(db, name, sql) for name, sql in rows}


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def write(description: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(description, indent=1, sort_keys=True) + "\n", "utf-8")
