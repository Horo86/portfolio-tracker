"""The Administrator's market data routes (D-121, D-135): run now and the status. 404 for
everyone else, counts and error codes only: never an ISIN, an instrument, a listing or a
symbol. Invented data only."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fakes import Provider
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import select
from test_api_reports import ETF, post

from portfolio_tracker.cli import reset_market_data_config
from portfolio_tracker.config import Settings
from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.repositories import market_config
from portfolio_tracker.services.scheduler import Trigger, market_job

RUN, STATUS = "/api/v1/admin/market-data/run", "/api/v1/admin/market-data/status"
HTTPS = "https://testserver"  # the session cookie is Secure
ISIN = fake_isin(418)


def link_now(client: TestClient) -> None:
    market_data = client.app.state.market_data
    market_job(
        client.app.state.engine,
        market_data.registry,
        market_data.transport,
        market_data.cipher,
        urls=market_data.urls,
        clock=lambda: datetime(2026, 3, 5, 18, 0, tzinfo=UTC),
    )("DAILY")


def test_an_investor_gets_404_on_both_routes(on: TestClient) -> None:
    alice = investor(on, "alice")
    for method, path in (("POST", RUN), ("GET", STATUS)):
        response = on.request(method, path, headers=alice)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize(
    ("trigger", "status", "answer"),
    [
        (Trigger.QUEUED, 202, {"queued": "QUEUED"}),
        (Trigger.ALREADY_QUEUED, 202, {"queued": "ALREADY_QUEUED"}),
        (Trigger.BUSY, 409, None),
    ],
)
def test_run_now_queues_a_run_or_is_refused_while_one_runs(
    on: TestClient, monkeypatch: pytest.MonkeyPatch, trigger, status, answer
) -> None:
    asked: list[str] = []
    scheduler = on.app.state.market_data.scheduler
    monkeypatch.setattr(scheduler, "trigger", lambda kind: asked.append(kind) or trigger)

    response = on.post(RUN, headers=CSRF)

    assert response.status_code == status and asked == ["MANUAL"]
    if answer is None:
        assert response.json()["code"] == "MARKET_DATA_RUNNING"
        return
    assert response.json() == answer
    with db_transaction(on.app.state.engine, write=False) as session:
        actions = [e.action for e in session.scalars(select(models.AuditEntry))]
    assert actions[-1] == "market_data.run_requested"


def test_with_market_data_off_run_now_is_refused(client: TestClient) -> None:
    create_admin(client)
    response = client.post(RUN, headers=CSRF)
    assert (response.status_code, response.json()["code"]) == (409, "MARKET_DATA_OFF")
    status = client.get(STATUS).json()
    assert (status["market_data"], status["next_run_at"], status["running"]) == ("OFF", None, None)


def test_the_status_never_tells_whether_an_instrument_was_new(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final review M-2: the Administrator adds an ISIN Alice already holds, and one nobody
    holds, and runs now each time: the run counts shown are the same."""
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    alice = investor(on, "alice")
    post(on, "/api/v1/assets", ETF | {"isin": ISIN}, alice)
    link_now(on)

    shown = []
    for isin in (ISIN, fake_isin(419)):  # held by Alice, then held by nobody
        post(on, "/api/v1/assets", ETF | {"name": isin, "isin": isin})
        link_now(on)
        shown.append(on.get(STATUS).json()["last_run"]["counts"])
    assert shown[0] == shown[1] == {"assets_linked": 1}


def test_the_status_holds_counts_only(on: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    alice = investor(on, "alice")
    asset = post(on, "/api/v1/assets", ETF | {"isin": ISIN}, alice)
    link_now(on)

    response = on.get(STATUS)
    assert response.status_code == 200
    status = response.json()
    assert status["market_data"] == "ON" and status["daily_time_utc"] == "22:00:00"
    assert status["next_run_at"] == "2026-01-15T22:00:00Z"
    assert status["last_run"]["kind"] == "DAILY"
    assert status["last_run"]["counts"] == {"assets_linked": 1}
    assert {e["name"]: e["checks"] for e in status["engines"]} == {"yahoo": {"OK": 1}}
    with db_transaction(on.app.state.engine, write=False) as session:
        logged = session.scalars(select(models.JobRun)).all()[-1].counts
    assert logged["instruments_created"] == 1 and logged["closes_stored"] >= 1  # server side

    with db_transaction(on.app.state.engine, write=False) as session:
        instrument = session.scalars(select(models.Instrument)).one()
        listing = session.scalars(select(models.InstrumentListing)).one()
        assert session.get(models.Asset, UUID(asset["id"])).instrument_id == instrument.id
    for secret in (ISIN, str(instrument.id), str(listing.id), "XMPW", asset["id"], "alice"):
        assert secret not in response.text, secret


def test_the_status_lists_the_enabled_engines_of_each_role_in_order(on: TestClient) -> None:
    """D-175 (4): the read-only card's data; bonds have no engine by default (D-132)."""
    assert on.get(STATUS).json()["routes"] == {
        "ETF_STOCK": ["yahoo"],
        "BOND": [],
        "FX": ["frankfurter"],
        "CLASSIFY": ["openfigi", "yahoo"],
    }


def test_a_disabled_engine_keeps_its_place_but_is_not_listed(on: TestClient) -> None:
    with db_transaction(on.app.state.engine, write=True) as session:
        stored = market_config.load(session)
        market_config.save(session, replace(stored, enabled=stored.enabled - {"openfigi"}))
    response = on.get(STATUS)
    assert response.json()["routes"]["CLASSIFY"] == ["yahoo"]
    assert "openfigi" not in response.text  # nor in any other role
    with db_transaction(on.app.state.engine, write=False) as session:
        assert market_config.load(session).order(Role.CLASSIFY) == ("openfigi", "yahoo")


def test_the_routes_carry_engine_names_only(on: TestClient) -> None:
    """No credential, key, URL or exchange: only names from the configured orders."""
    routes = on.get(STATUS).json()["routes"]
    assert set(routes) == {role.value for role in Role}
    names = {name for engines in routes.values() for name in engines}
    assert names <= set(on.app.state.market_data.registry.names)


# --- the price sources (D-135, D-186, D-187, D-188) -------------------------------------

BASE = "/api/v1/admin/market-data"
ENGINES, ROUTES, EXCHANGES = f"{BASE}/engines", f"{BASE}/routes", f"{BASE}/exchanges"


@pytest.fixture
def queued(on: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The runs the routes queue (none runs: the tests read what was asked)."""
    asked: list[str] = []
    scheduler = on.app.state.market_data.scheduler
    monkeypatch.setattr(scheduler, "trigger", lambda kind: asked.append(kind) or Trigger.QUEUED)
    return asked


def audit_rows(client: TestClient, prefix: str = "market_data.") -> list[models.AuditEntry]:
    with db_transaction(client.app.state.engine, write=False) as session:
        rows = session.scalars(select(models.AuditEntry)).all()
    return [row for row in rows if row.action.startswith(prefix)]


def recheck_requested(client: TestClient) -> datetime | None:
    with db_transaction(client.app.state.engine, write=False) as session:
        return market_config.resolve_requested_at(session)


def stored_config(client: TestClient):
    with db_transaction(client.app.state.engine, write=False) as session:
        return market_config.load(session)


def engine_of(client: TestClient, name: str) -> dict:
    return next(e for e in client.get(ENGINES).json()["engines"] if e["name"] == name)


def test_an_investor_gets_404_on_the_price_sources(on: TestClient) -> None:
    """D-92: as if the routes did not exist, and nothing changes. The anonymous 401, the
    temporary password and the 2FA obligation, in that order, are in the route matrix."""
    alice = investor(on, "alice")
    before = stored_config(on)
    calls = [
        ("GET", ENGINES, None),
        ("PATCH", f"{ENGINES}/yahoo", {"enabled": False}),
        ("PATCH", f"{ENGINES}/nobody", {"enabled": False}),
        ("GET", ROUTES, None),
        ("PUT", f"{ROUTES}/ETF_STOCK", {"engines": []}),
        ("PUT", EXCHANGES, {"exchanges": ["MIL"]}),
    ]
    for method, path, body in calls:
        response = on.request(method, path, headers=alice, json=body)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND"), path
    assert stored_config(on) == before and audit_rows(on) == []


def test_the_engines_are_listed_with_what_they_declare(on: TestClient) -> None:
    answer = on.get(ENGINES).json()
    assert answer["market_data"] == "ON"
    assert [e["name"] for e in answer["engines"]] == ["frankfurter", "openfigi", "yahoo"]
    yahoo = engine_of(on, "yahoo")
    assert yahoo["roles"] == ["ETF_STOCK", "CLASSIFY"]
    assert yahoo["capabilities"]["search_isin"] and yahoo["capabilities"]["search_text"]
    assert (yahoo["official"], yahoo["enabled"], yahoo["off_by_environment"]) == (
        False, True, False,
    )  # fmt: skip
    assert (yahoo["needs_credential"], yahoo["credential_set"]) == (False, False)
    assert yahoo["credential_updated_at"] is None
    assert yahoo["status"] == "WORKING" and yahoo["last_success_at"] is None
    assert yahoo["checks"] == {}
    assert engine_of(on, "openfigi")["terms_url"].startswith("https://")


def test_turning_an_engine_off_and_on_is_audited_and_queues_a_re_check(
    on: TestClient, queued: list[str]
) -> None:
    assert recheck_requested(on) is None

    response = on.patch(f"{ENGINES}/openfigi", headers=CSRF, json={"enabled": False})

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["enabled"]) == ("openfigi", False)
    assert queued == ["RESOLVE"] and recheck_requested(on) is not None
    config = stored_config(on)
    assert "openfigi" not in config.enabled
    assert config.order(Role.CLASSIFY) == ("openfigi", "yahoo")  # keeps its place (D-132)
    assert on.get(ROUTES).json()["routes"]["CLASSIFY"] == ["openfigi", "yahoo"]
    [row] = audit_rows(on)
    assert row.action == "market_data.engine_disabled"
    assert (row.before, row.after) == (
        {"engine": "openfigi", "enabled": True}, {"engine": "openfigi", "enabled": False},
    )  # fmt: skip
    assert row.entity_type == "market_data_config" and row.actor_user_id is not None

    on.patch(f"{ENGINES}/openfigi", headers=CSRF, json={"enabled": True})
    assert sorted(r.action for r in audit_rows(on)) == [
        "market_data.engine_disabled", "market_data.engine_enabled",
    ]  # fmt: skip
    assert queued == ["RESOLVE", "RESOLVE"]


def test_a_write_that_changes_nothing_answers_the_values_and_records_nothing(
    on: TestClient, queued: list[str]
) -> None:
    """D-188, as D-181's no-op save: 200 with the current values, no audit row, no re-check."""
    routes = on.get(ROUTES).json()
    engine = on.patch(f"{ENGINES}/yahoo", headers=CSRF, json={"enabled": True})
    order = on.put(f"{ROUTES}/CLASSIFY", headers=CSRF, json={"engines": ["openfigi", "yahoo"]})
    exchanges = on.put(EXCHANGES, headers=CSRF, json={"exchanges": routes["exchanges"]})

    assert engine.status_code == order.status_code == exchanges.status_code == 200
    assert engine.json()["enabled"] is True
    assert order.json() == exchanges.json() == routes
    assert queued == [] and recheck_requested(on) is None and audit_rows(on) == []


def test_an_unknown_engine_is_404_and_the_body_is_strict(on: TestClient, queued) -> None:
    response = on.patch(f"{ENGINES}/nobody", headers=CSRF, json={"enabled": False})
    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    for body in ({"enabled": "false"}, {"enabled": 0}, {}, {"enabled": True, "x": 1}):
        response = on.patch(f"{ENGINES}/yahoo", headers=CSRF, json=body)
        assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert queued == [] and audit_rows(on) == []


def test_routes_list_the_orders_the_engines_of_each_role_and_the_exchanges(on) -> None:
    assert on.get(ROUTES).json() == {
        "market_data": "ON",
        "routes": {"ETF_STOCK": ["yahoo"], "BOND": [], "FX": ["frankfurter"],
                   "CLASSIFY": ["openfigi", "yahoo"]},
        "available": {"ETF_STOCK": ["yahoo"], "BOND": [], "FX": ["frankfurter"],
                      "CLASSIFY": ["openfigi", "yahoo"]},
        "exchanges": [],
    }  # fmt: skip


def test_a_new_order_is_stored_audited_and_queues_a_re_check(
    on: TestClient, queued: list[str]
) -> None:
    response = on.put(f"{ROUTES}/CLASSIFY", headers=CSRF, json={"engines": ["yahoo", "openfigi"]})

    assert response.status_code == 200
    assert response.json()["routes"]["CLASSIFY"] == ["yahoo", "openfigi"]
    assert stored_config(on).order(Role.CLASSIFY) == ("yahoo", "openfigi")
    assert queued == ["RESOLVE"] and recheck_requested(on) is not None
    [row] = audit_rows(on)
    assert row.action == "market_data.routes_changed"
    assert row.before == {"role": "CLASSIFY", "engines": ["openfigi", "yahoo"]}
    assert row.after == {"role": "CLASSIFY", "engines": ["yahoo", "openfigi"]}


def test_a_role_may_be_left_without_an_engine(on: TestClient, queued: list[str]) -> None:
    """D-188: the API allows ETF_STOCK or FX with no enabled engine; the screen confirms it.
    No price is deleted (D-134)."""
    for role in ("ETF_STOCK", "FX"):
        response = on.put(f"{ROUTES}/{role}", headers=CSRF, json={"engines": []})
        assert response.status_code == 200 and response.json()["routes"][role] == []
    off = on.patch(f"{ENGINES}/frankfurter", headers=CSRF, json={"enabled": False})
    assert off.status_code == 200
    assert on.get(STATUS).json()["routes"]["ETF_STOCK"] == []


@pytest.mark.parametrize(
    ("role", "engines"),
    [
        ("ETF_STOCK", ["nobody"]),  # unknown
        ("CLASSIFY", ["yahoo", "yahoo"]),  # repeated
        ("ETF_STOCK", ["frankfurter"]),  # does not declare the role
        ("BOND", ["yahoo"]),  # yahoo prices ETFs and stocks, not bonds
        ("FX", ["Frankfurter"]),  # names are exact
    ],
)
def test_an_order_the_engines_do_not_allow_is_refused(
    on: TestClient, queued: list[str], role: str, engines: list[str]
) -> None:
    before = stored_config(on)
    response = on.put(f"{ROUTES}/{role}", headers=CSRF, json={"engines": engines})
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert stored_config(on) == before and queued == [] and audit_rows(on) == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (f"{ROUTES}/STOCKS", {"engines": []}),  # no such role
        (f"{ROUTES}/FX", {"engines": "frankfurter"}),  # not a list
        (f"{ROUTES}/FX", {"engines": ["x"] * 17}),  # too long
        (EXCHANGES, {"exchanges": "MIL"}),
        (EXCHANGES, {"exchanges": ["MIL"] * 21}),
        (EXCHANGES, ["MIL"]),  # a bare list (D-188: the body is an object)
    ],
)
def test_a_malformed_body_is_refused(on: TestClient, queued: list[str], path, body) -> None:
    response = on.put(path, headers=CSRF, json=body)
    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert queued == [] and audit_rows(on) == []


def test_preferred_exchanges_are_stored_audited_and_queue_a_re_check(
    on: TestClient, queued: list[str]
) -> None:
    response = on.put(EXCHANGES, headers=CSRF, json={"exchanges": [" MIL", "GER"]})

    assert response.status_code == 200 and response.json()["exchanges"] == ["MIL", "GER"]
    assert stored_config(on).exchanges == ("MIL", "GER")
    assert queued == ["RESOLVE"]
    [row] = audit_rows(on)
    assert row.action == "market_data.exchanges_changed"
    assert (row.before, row.after) == ({"exchanges": []}, {"exchanges": ["MIL", "GER"]})

    assert on.put(EXCHANGES, headers=CSRF, json={"exchanges": []}).json()["exchanges"] == []


@pytest.mark.parametrize("exchanges", [[""], ["MIL,GER"], ["MIL", "mil"], ["x" * 65]])
def test_bad_exchanges_are_refused(on: TestClient, queued: list[str], exchanges) -> None:
    response = on.put(EXCHANGES, headers=CSRF, json={"exchanges": exchanges})
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert stored_config(on).exchanges == () and queued == [] and audit_rows(on) == []


def test_with_market_data_off_the_configuration_stays_editable(client: TestClient) -> None:
    """D-132: the stored values are answered and changed; the re-check request waits for the
    first run once market data is on."""
    create_admin(client)
    answer = client.get(ENGINES).json()
    assert answer["market_data"] == "OFF"
    assert {e["name"] for e in answer["engines"]} == {"frankfurter", "openfigi", "yahoo"}
    assert all(e["off_by_environment"] for e in answer["engines"])
    assert client.get(ROUTES).json()["market_data"] == "OFF"

    response = client.patch(f"{ENGINES}/yahoo", headers=CSRF, json={"enabled": False})
    assert response.status_code == 200 and response.json()["enabled"] is False
    moved = client.put(f"{ROUTES}/CLASSIFY", headers=CSRF, json={"engines": ["yahoo"]})
    assert moved.json()["routes"]["CLASSIFY"] == ["yahoo"]
    assert client.put(EXCHANGES, headers=CSRF, json={"exchanges": ["MIL"]}).status_code == 200
    assert stored_config(client).enabled == frozenset({"frankfurter", "openfigi"})
    assert recheck_requested(client) is not None
    assert len(audit_rows(client)) == 3


def test_a_keyed_engine_without_its_credential_may_be_turned_on(market_app_with) -> None:
    """D-188: allowed; it reads MISSING_CREDENTIAL and the run skips it (the credential
    routes come in a later pull request)."""
    from test_marketdata_plugin_proof import KEYED_ISIN, built, registry_with_both

    built.clear()
    with market_app_with(Provider(unknown=frozenset({KEYED_ISIN})), registry_with_both()) as app:
        app.app.state.market_data.scheduler.trigger = lambda kind: Trigger.QUEUED
        keyed = engine_of(app, "keyed")
        assert (keyed["enabled"], keyed["needs_credential"], keyed["credential_set"]) == (
            False, True, False,
        )  # fmt: skip
        assert keyed["credential_label"] == "api_key"
        assert keyed["status"] == "MISSING_CREDENTIAL"  # computed while off too

        response = app.patch(f"{ENGINES}/keyed", headers=CSRF, json={"enabled": True})
        assert response.status_code == 200
        assert (response.json()["enabled"], response.json()["status"]) == (
            True, "MISSING_CREDENTIAL",
        )  # fmt: skip
        order = {"engines": ["yahoo", "keyed"]}
        assert app.put(f"{ROUTES}/ETF_STOCK", headers=CSRF, json=order).status_code == 200
        post(app, "/api/v1/assets", ETF | {"isin": KEYED_ISIN})
        link_now(app)
        assert built == []  # skipped: never built without its credential
        with db_transaction(app.app.state.engine, write=False) as session:
            assert session.scalars(select(models.InstrumentListing)).all() == []


def test_the_engines_health_comes_from_their_throttles(
    on: TestClient, answers: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-186: last success, last error and the pause, since the start, by the fake engines."""
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    post(on, "/api/v1/assets", ETF | {"isin": ISIN})
    link_now(on)
    yahoo = engine_of(on, "yahoo")
    assert yahoo["last_success_at"] is not None and yahoo["last_error_code"] is None
    assert yahoo["status"] == "WORKING" and yahoo["checks"] == {"OK": 1}

    answers.status = 429  # the search is rate limited, with Retry-After: 30
    post(on, "/api/v1/assets", ETF | {"name": "Other", "isin": fake_isin(420)})
    link_now(on)
    yahoo = engine_of(on, "yahoo")
    assert yahoo["last_error_code"] == "RATE_LIMITED" and yahoo["status"] == "PAUSED"
    error_at = datetime.fromisoformat(yahoo["last_error_at"])
    paused = datetime.fromisoformat(yahoo["paused_until"])
    assert (paused - error_at).total_seconds() == 30


class Lines(logging.Handler):
    """The app's start replaces the root handlers (`logging_setup`), so the records are taken
    on the startup module's own logger."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_a_restart_keeps_the_stored_values_and_only_logs_the_environment(
    tmp_path, fast_passwords
) -> None:
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    with TestClient(create_app(settings, passwords=fast_passwords), base_url=HTTPS) as first:
        create_admin(first)
        first.patch(f"{ENGINES}/yahoo", headers=CSRF, json={"enabled": False})
        first.put(EXCHANGES, headers=CSRF, json={"exchanges": ["MIL"]})
        saved = stored_config(first)

    changed = settings.model_copy(update={"price_exchanges": "GER"})
    lines, logger = Lines(), logging.getLogger("portfolio_tracker.services.market_startup")
    logger.addHandler(lines)
    try:
        with TestClient(create_app(changed, passwords=fast_passwords), base_url=HTTPS) as second:
            assert stored_config(second) == saved
            assert stored_config(second).exchanges == ("MIL",)
    finally:
        logger.removeHandler(lines)
    [line] = [r for r in lines.records if "differs from the environment" in r.getMessage()]
    assert set(line.differs) == {"on/off", "exchanges"}


def test_the_reset_command_still_puts_back_the_environments_values(
    tmp_path, fast_passwords
) -> None:
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    with TestClient(create_app(settings, passwords=fast_passwords), base_url=HTTPS) as client:
        create_admin(client)
        client.patch(f"{ENGINES}/yahoo", headers=CSRF, json={"enabled": False})
        client.put(f"{ROUTES}/CLASSIFY", headers=CSRF, json={"engines": ["yahoo"]})
        client.put(EXCHANGES, headers=CSRF, json={"exchanges": ["MIL"]})

    assert reset_market_data_config(settings, ask=lambda _: "y", show=lambda _: None)

    with TestClient(create_app(settings, passwords=fast_passwords), base_url=HTTPS) as client:
        config = stored_config(client)
    assert config.enabled == frozenset({"frankfurter", "openfigi", "yahoo"})
    assert config.order(Role.CLASSIFY) == ("openfigi", "yahoo") and config.exchanges == ()


def test_no_isin_symbol_or_id_in_any_price_sources_answer_nor_audit_row(
    on: TestClient, queued: list[str]
) -> None:
    """D-135: counts only. Alice holds a linked ETF; every answer of the price-source
    routes and every audit row they write is scanned for what would show it."""
    alice = investor(on, "alice")
    asset = post(on, "/api/v1/assets", ETF | {"isin": ISIN}, alice)
    link_now(on)
    with db_transaction(on.app.state.engine, write=False) as session:
        instrument = session.scalars(select(models.Instrument)).one()
        listing = session.scalars(select(models.InstrumentListing)).one()
    secrets = (ISIN, listing.symbol, "XMPW", str(instrument.id), str(listing.id), asset["id"],
               "alice")  # fmt: skip

    answers = [
        on.get(ENGINES),
        on.patch(f"{ENGINES}/openfigi", headers=CSRF, json={"enabled": False}),
        on.patch(f"{ENGINES}/yahoo", headers=CSRF, json={"enabled": True}),
        on.get(ROUTES),
        on.put(f"{ROUTES}/CLASSIFY", headers=CSRF, json={"engines": ["yahoo"]}),
        on.put(f"{ROUTES}/ETF_STOCK", headers=CSRF, json={"engines": [ISIN]}),  # refused
        on.put(EXCHANGES, headers=CSRF, json={"exchanges": ["MIL"]}),
    ]
    assert engine_of(on, "yahoo")["checks"] == {"OK": 1}  # the count is there
    rows = audit_rows(on)
    assert len(rows) == 3
    texts = [a.text.replace(ISIN, "") if a.status_code == 422 else a.text for a in answers]
    texts += [repr((row.before, row.after, row.entity_id)) for row in rows]
    for text in texts:
        for secret in secrets:
            assert secret not in text, secret
