"""Optional local check on the maintainer's real Directa exports (M3 "Done when"; CLAUDE.md
hard rule 1). Skipped when `private/directa/` has no `.xlsx` file, so always skipped in CI.

The files hold real financial data, so this test is built never to show any of it:
- every check is a precomputed boolean or count, reported with a constant message through
  `pytest.fail(..., pytrace=False)`: no assert statement, no traceback, no locals, no
  introspected values;
- every step on real data runs guarded: an exception is reduced to its stable code
  (`IMPORT_*`, `QUANTITY_CONFLICT`, or the exception type), outside the `except` block, so
  nothing chains or prints its message;
- logging is switched off while it runs, so captured logs show nothing either;
- the scratch database lives inside `private/` (git-ignored) and is deleted afterwards:
  nothing read from `private/` is written anywhere else;
- the only output (with `pytest -s`) is one line of counts.

Structural checks: every file parses; no line is ambiguous or invalid; every merged order
meets the BUY invariant; the files import in every order with no quantity conflict and give
the same ledger; a re-import imports nothing.
"""

import hashlib
import itertools
import logging
import shutil
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from portfolio_tracker.db import create_db_engine
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.importers.base import Category
from portfolio_tracker.importers.directa import DirectaImporter
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import accounts, assets, mapping, users
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services import imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput

PRIVATE = Path(__file__).parents[2] / "private"
FILES = sorted((PRIVATE / "directa").glob("*.xlsx")) if (PRIVATE / "directa").is_dir() else []
TODAY = date(2100, 1, 1)  # no FUTURE_DATE noise; only counts are compared
NOT_IMPORTED = (Category.AMBIGUOUS, Category.NOT_RECOGNIZED, Category.INVALID)

pytestmark = pytest.mark.skipif(not FILES, reason="no files in private/directa (local only)")


class Problems:
    """Failures by fixed category, with counts only."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def check(self, ok: bool, category: str) -> None:
        if not ok:
            self.counts[category] += 1

    def report(self) -> None:
        if self.counts:
            summary = "; ".join(f"{name}: {n}" for name, n in sorted(self.counts.items()))
            pytest.fail(f"private Directa check failed ({summary})", pytrace=False)


def _code(exc: BaseException) -> str:
    """A stable code only: never the message, which may quote a cell."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code.isupper() else type(exc).__name__


@contextmanager
def _quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@contextmanager
def _scratch_db():
    """A migrated database inside private/ (git-ignored), removed afterwards."""
    folder = PRIVATE / f".pytest-db-{uuid.uuid4().hex}"
    folder.mkdir()
    engine = create_db_engine(folder / "app.sqlite")

    @event.listens_for(engine, "connect")
    def _memory_temp(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA temp_store=MEMORY")  # no temp files elsewhere

    try:
        run_migrations(engine, folder / "app.sqlite", folder / "backups")
        with Session(engine, expire_on_commit=False) as db, db.begin():
            yield db
    finally:
        engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)


def _parse_counts(problems: Problems, totals: Counter[str]) -> None:
    for path in FILES:
        error = None
        try:
            parsed = DirectaImporter().parse(path.read_bytes())
        except Exception as exc:  # reduced to its code below
            error = _code(exc)
        if error is not None:
            problems.check(False, f"file not readable ({error})")
            continue
        cands = parsed.candidates
        rows = {category: sum(len(c.lines) for c in cands if c.category is category)
                for category in Category}  # fmt: skip
        totals["files"] += 1
        totals["rows read"] += sum(rows.values())
        totals["orders"] += sum(1 for c in cands if c.key.startswith("order:"))
        totals["rows to import"] += rows[Category.TO_IMPORT]
        totals["rows to review"] += rows[Category.TO_REVIEW]
        totals["problems"] += sum(rows[c] for c in NOT_IMPORTED)
        problems.check(rows[Category.AMBIGUOUS] == 0, "ambiguous rows")
        problems.check(rows[Category.NOT_RECOGNIZED] == 0, "rows not recognized")
        problems.check(parsed.account_code is not None, "no account code")
        for c in cands:
            if c.key.startswith("order:") and c.selectable:
                parts = (c.gross_amount, c.fees, c.accrued_interest_paid, c.taxes)
                total = sum((p for p in parts if p is not None), start=0)
                problems.check(total == c.total_amount, "order breaks the BUY invariant")


def _import(db: Session, owner_id, account_id, path: Path, problems: Problems) -> int:
    """Preview (types chosen for every new asset), confirm everything selectable; returns how
    many transactions were created, or -1 on failure."""
    content = path.read_bytes()
    error = None
    try:
        inp = ImportInput(content=content, account_id=account_id)
        first = imports.preview(db, owner_id, inp, today=TODAY)
        # The type does not change quantities or cash: ETF for any asset without evidence.
        choices = {a.key: AssetChoice(asset_type=AssetType.ETF)
                   for a in first.assets if a.new and a.asset_type is None}  # fmt: skip
        everything = frozenset(x.key for x in first.lines if x.candidate.selectable)
        inp = ImportInput(content=content, account_id=account_id, assets=choices,
                          selected=everything)  # fmt: skip
        seen = imports.preview(db, owner_id, inp, today=TODAY)
        problems.check(not seen.blocking, "preview blocked (choice or quantity conflict)")
        if seen.blocking:
            return -1
        done = imports.confirm(db, owner_id, owner_id, inp, seen.fingerprint, today=TODAY)
        created = len(done.created)
    except Exception as exc:  # reduced to its code below
        error = _code(exc)
    if error is not None:
        problems.check(False, f"import failed ({error})")
        return -1
    return created


def _ledger_digest(db: Session, owner_id, account_id) -> str:
    """A hash of cash and positions by ISIN: compared between import orders, never shown."""
    rows = txs.account_ledger(db, owner_id, account_id)
    ledger = replay([mapping.to_engine_transaction(r) for r in rows])
    by_id = assets.assets_by_id(db, set(ledger.positions))
    state = sorted(
        (by_id[a].isin or "", str(p.quantity), str(p.cost_basis), str(p.income_gross),
         str(p.income_taxes)) for a, p in ledger.positions.items()
    )  # fmt: skip
    return hashlib.sha256(repr((str(ledger.cash), state)).encode()).hexdigest()


def _guarded(problems: Problems, category: str, run, *args):
    """Run one step on real data; an exception is counted by its stable code only, outside
    the `except` block (nothing chains or prints its message, which may quote figures)."""
    error = None
    try:
        return run(*args)
    except Exception as exc:  # reduced to its code below
        error = _code(exc)
    problems.check(False, f"{category} ({error})")
    return None


def _run_sequence(sequence: tuple[Path, ...], problems: Problems) -> tuple[int, str]:
    """Import the files in this order into a new account, then each again; returns the
    number of transactions created and a digest of the final ledger."""
    with _scratch_db() as db:
        owner = users.add_user(db, username="local", role="INVESTOR", password_hash="x",
                               language="en")  # fmt: skip
        account = accounts.add_account(db, owner.id, name="Local", currency="EUR")
        created = 0
        for index, path in enumerate(sequence):
            # Later files find the account by the code the first one stored (§4).
            target = account.id if index == 0 else None
            created += max(_import(db, owner.id, target, path, problems), 0)
        for path in sequence:
            problems.check(_import(db, owner.id, None, path, problems) == 0,
                           "re-import created transactions")  # fmt: skip
        problems.check(
            created == len(txs.account_ledger(db, owner.id, account.id)),
            "transaction count differs from the ledger",
        )
        return created, _ledger_digest(db, owner.id, account.id)


def test_real_directa_exports() -> None:
    problems = Problems()
    totals: Counter[str] = Counter()
    orders = list(itertools.permutations(FILES)) if len(FILES) <= 3 else [FILES, FILES[::-1]]
    digests = set()
    with _quiet():
        _guarded(problems, "counting failed", _parse_counts, problems, totals)
        for sequence in orders:
            result = _guarded(problems, "import run failed", _run_sequence, sequence, problems)
            if result is not None:
                totals["transactions"], digest = result
                digests.add(digest)
    problems.check(len(digests) == 1, "import order changes the ledger")
    print(
        "\nprivate Directa: " + ", ".join(f"{k}={totals[k]}" for k in (
            "files", "rows read", "orders", "rows to import", "rows to review", "problems",
            "transactions"))
    )  # fmt: skip
    problems.report()
