"""`python -m portfolio_tracker reset-market-data-config` (D-132): shows the stored values next
to the environment's, asks on a terminal, then replaces the configuration and records a
re-check request, touching nothing else."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from portfolio_tracker.__main__ import main
from portfolio_tracker.cli import CliError, reset_market_data_config
from portfolio_tracker.config import Settings
from portfolio_tracker.credentials import CredentialCipher, SecretKey
from portfolio_tracker.db import create_db_engine, db_transaction, models
from portfolio_tracker.marketdata.base import Rate, Role
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import credentials, fx, instruments, market_config

# What an administrator changed later (M6): yahoo off, OpenFIGI alone, an exchange.
CHANGED = RoutingConfig(
    frozenset({"frankfurter", "openfigi"}),
    {
        Role.ETF_STOCK: ("yahoo",),
        Role.FX: ("frankfurter",),
        Role.CLASSIFY: ("openfigi",),
    },
    ("MIL",),
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings.db_path)
    run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
    yield engine
    engine.dispose()


def stored(engine: Engine, config: RoutingConfig | None = CHANGED) -> None:
    """The configuration, and data of every other kind the command must not touch."""
    cipher = CredentialCipher(SecretKey(bytes(32), "test"))
    with db_transaction(engine, write=True) as session:
        if config is not None:
            market_config.save(session, config)
        instrument = instruments.get_or_create_instrument(session, "ZZ0000000001", "EUR")
        listing = instruments.save_listing(
            session, instrument.id, "yahoo", "XMPW.MI", currency="EUR", unit="CURRENCY"
        )
        instruments.store_closes(
            session, listing.id, [instruments.StoredClose(date(2026, 3, 5), Decimal("10"))]
        )
        fx.store_rates(session, [Rate(date(2026, 3, 5), "USD", Decimal("1.08"))], "frankfurter")
        credentials.set_credential(session, cipher, "openfigi", "invented-key")


def untouched(engine: Engine) -> tuple[int, int, int, int]:
    with db_transaction(engine, write=False) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (
                models.InstrumentListing,
                models.InstrumentPrice,
                models.FxRate,
                models.ProviderCredential,
            )
        )


def config_and_request(engine: Engine):
    with db_transaction(engine, write=False) as session:
        return market_config.load(session), market_config.resolve_requested_at(session)


def test_it_shows_both_sides_and_changes_nothing_without_a_yes(
    settings: Settings, engine: Engine
) -> None:
    stored(engine)
    shown: list[str] = []
    asked: list[str] = []

    def no(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    assert reset_market_data_config(settings, no, shown.append) is False
    assert "* on/off     frankfurter, openfigi | frankfurter, openfigi, yahoo" in shown
    assert "* CLASSIFY   openfigi | openfigi, yahoo" in shown
    assert "  ETF_STOCK  yahoo | yahoo" in shown
    assert "* exchanges  MIL | (none)" in shown
    assert "[y/N]" in asked[0] and shown[-1] == "Nothing changed."
    assert config_and_request(engine) == (CHANGED, None)


def test_a_yes_replaces_the_configuration_and_asks_for_a_re_check(
    settings: Settings, engine: Engine
) -> None:
    stored(engine)
    before = untouched(engine)

    assert reset_market_data_config(settings, lambda _prompt: "Y", lambda _line: None)

    config, requested = config_and_request(engine)
    assert config.enabled == {"frankfurter", "openfigi", "yahoo"}
    assert config.order(Role.CLASSIFY) == ("openfigi", "yahoo") and config.exchanges == ()
    assert requested is not None
    assert untouched(engine) == before  # listings, closes, rates, credentials
    with db_transaction(engine, write=False) as session:
        entry = session.scalars(select(models.AuditEntry)).one()
    assert (entry.action, entry.after["by"]) == ("market_data.config_reset", "container command")


def test_without_a_terminal_it_refuses(settings: Settings, engine: Engine) -> None:
    stored(engine)
    with pytest.raises(CliError, match="no terminal"):
        reset_market_data_config(settings, None, lambda _line: None)
    assert config_and_request(engine) == (CHANGED, None)


def test_nothing_to_do_when_the_environment_matches(settings: Settings, engine: Engine) -> None:
    stored(engine, None)
    reset_market_data_config(settings, lambda _prompt: "y", lambda _line: None)  # first store
    with db_transaction(engine, write=True) as session:
        market_config.clear_resolve_request(session, market_config.resolve_requested_at(session))
    shown: list[str] = []

    def never(_prompt: str) -> str:
        raise AssertionError("nothing to confirm")

    assert reset_market_data_config(settings, never, shown.append) is False
    assert shown == ["The stored market data configuration already matches the environment."]
    assert config_and_request(engine)[1] is None


def test_nothing_stored_yet_is_shown_as_such(settings: Settings, engine: Engine) -> None:
    shown: list[str] = []
    assert reset_market_data_config(settings, lambda _prompt: "yes", shown.append)
    assert "* exchanges  (nothing) | (none)" in shown
    assert config_and_request(engine)[0] is not None


def test_an_invalid_environment_is_refused_naming_the_variable(settings: Settings) -> None:
    wrong = settings.model_copy(update={"bond_price_providers": "frankfurter"})
    with pytest.raises(CliError, match="PT_BOND_PRICE_PROVIDERS"):
        reset_market_data_config(wrong, lambda _prompt: "y", lambda _line: None)


def test_the_command_line_refuses_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["reset-market-data-config"]) == 1
    assert "no terminal to confirm on" in capsys.readouterr().err
