"""Income/withholding pairing (directa.md §3, D-108) after it became linear (final review of
M3, F4): the same result as the first, scan-based version on random synthetic rows, and a
crafted worst case in well under a second. Invented data only."""

import random
import time
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from portfolio_tracker.importers import directa_mapping as m
from portfolio_tracker.importers.directa_reader import DirectaRow

INCOME_TYPES = ("Incasso dividendi italia", "Cedola obb.")
TAX_TYPES = ("Ritenuta dividendi italia", "Rit.cedola obb.")
ISINS = ("IE0TEST00020", "IT0TEST00015", None)
DAYS = (date(2024, 3, 1), date(2024, 3, 2), None)


def row(line: int, movement: str, protocol: int | None, day, isin, amount: str) -> DirectaRow:
    return DirectaRow(
        line=line, trade_date=day, value_date=day, movement=movement, ticker=None, isin=isin,
        protocol=None if protocol is None else str(protocol), description=None,
        quantity=Decimal(0), amount=Decimal(amount), foreign_amount=Decimal(0), currency="EUR",
        order_ref=None,
    )  # fmt: skip


def reference(incomes, withholdings, lost):
    """The first implementation (one scan per income), kept as the specification."""

    def matches(income, tax):
        return (m.VERIFIED[tax.movement] is m._INCOME[m.VERIFIED[income.movement]][1]
                and tax.trade_date == income.trade_date and tax.isin == income.isin)  # fmt: skip

    def may_belong(income, tax):
        return (m.VERIFIED[tax.movement] is m._INCOME[m.VERIFIED[income.movement]][1]
                and tax.trade_date in (None, income.trade_date)
                and tax.isin in (None, income.isin))  # fmt: skip

    pairs, free = {}, list(withholdings)
    for income in incomes:
        for tax in free:
            if matches(income, tax) and m._next_protocol(income.protocol) == tax.protocol:
                pairs[income.line] = (tax, False)
                free.remove(tax)
                break
    rest = [i for i in incomes if i.line not in pairs]
    candidates = {i.line: [t for t in free if matches(i, t)] for i in rest}
    claimed = Counter(t.line for ts in candidates.values() for t in ts)
    ambiguous = set()
    for income in rest:
        found = candidates[income.line]
        if len(found) == 1 and claimed[found[0].line] == 1:
            pairs[income.line] = (found[0], True)
            free.remove(found[0])
        elif found:
            ambiguous.add(income.line)
            ambiguous.update(t.line for t in found)
    entries = []
    for income in incomes:
        if income.line in ambiguous:
            entries.append(m._ambiguous_row(income, "PAIRING_AMBIGUOUS"))
            continue
        tax, inferred = pairs.get(income.line, (None, False))
        suspect = [t for t in lost if may_belong(income, t)]
        if suspect and (tax is None or inferred):
            entries.append(m._ambiguous_row(income, "PAIRED_ROW_REJECTED", line=suspect[0].line))
            if tax is not None:
                entries.append(m._ambiguous_row(tax, "PAIRED_ROW_REJECTED",
                                                line=suspect[0].line))  # fmt: skip
            continue
        entries.append(m._income_entry(income, tax, inferred))
    for tax in free:
        code = "PAIRING_AMBIGUOUS" if tax.line in ambiguous else "WITHHOLDING_WITHOUT_INCOME"
        entries.append(m._ambiguous_row(tax, code))
    return entries


def random_rows(rng: random.Random):
    """Incomes and withholdings that collide on dates, ISINs and protocols on purpose."""
    incomes, withholdings, lost = [], [], []
    protocols = rng.sample(range(100, 140), 30)
    line = 1
    for protocol in protocols:
        kind = rng.randrange(2)
        day, isin = rng.choice(DAYS[:2]), rng.choice(ISINS[:2])
        line += 1
        if rng.random() < 0.6:
            incomes.append(row(line, INCOME_TYPES[kind], protocol, day, isin, "40.00"))
        else:
            withholdings.append(row(line, TAX_TYPES[kind], protocol, day, isin, "-10.40"))
        if rng.random() < 0.1:
            line += 1
            lost.append(row(line, TAX_TYPES[kind], None, rng.choice(DAYS), rng.choice(ISINS),
                            "-1.00"))  # fmt: skip
    return incomes, withholdings, lost


@pytest.mark.parametrize("seed", range(300))
def test_same_result_as_the_scan_based_version(seed: int) -> None:
    incomes, withholdings, lost = random_rows(random.Random(seed))
    assert m._income(incomes, withholdings, lost) == reference(incomes, withholdings, lost)


def test_crafted_worst_case_is_fast() -> None:
    """5,000 incomes and 5,000 withholdings, same date and ISIN, no consecutive protocols."""
    day, isin = date(2024, 3, 1), "IT0TEST00015"
    incomes = [row(n, INCOME_TYPES[0], 3 * n, day, isin, "40.00") for n in range(2, 5002)]
    withholdings = [row(n, TAX_TYPES[0], 3 * n + 5, day, isin, "-10.40")
                    for n in range(5002, 10002)]  # fmt: skip
    start = time.perf_counter()
    entries = m._income(incomes, withholdings, [])
    assert time.perf_counter() - start < 2  # was about 33 s
    assert len(entries) == 10_000
    assert {r.code for e in entries for r in e.reasons} == {"PAIRING_AMBIGUOUS"}
