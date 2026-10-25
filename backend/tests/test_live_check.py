"""The opt-in live check (`scripts/live_check.py`, D-151) without any network call: its
refusals, the transport that counts and stops, and a whole pass on the invented fakes of the
end-to-end launcher (the tests' network guard is on). Invented data only."""

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import e2e_market_server
import pytest
from e2e_market_server import STOCK_ISIN, WORLD_ISIN, FakeMarket
from helpers import fake_isin
from marketdata.fakes import FakeTransport, InstantTime, json_response, queue

from portfolio_tracker.config import Settings
from portfolio_tracker.marketdata.registry import default_registry

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_check.py"
PASSWORD = "an-invented-password-for-the-test"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/X"


@pytest.fixture(scope="module")
def live_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def entries(live_check: ModuleType) -> tuple:
    Entry = live_check.Entry
    return (
        Entry(STOCK_ISIN, "Example Market Industrial", "STOCK", "EUR", "MIL", "invented source"),
        Entry(WORLD_ISIN, "Example Market World", "ETF", "GBP", "MIL", "invented source"),
        Entry(fake_isin(8703), "Example Unknown Corp", "STOCK", "USD", None, "none"),
        Entry(fake_isin(8704), "Example Bond 2033", "BOND", "EUR", None, "none"),
    )


@pytest.fixture
def offline(live_check: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """No gate (the fakes would hold the job), a known password, no waiting."""
    monkeypatch.setattr(e2e_market_server, "GATED", frozenset())
    monkeypatch.setattr(live_check, "new_password", lambda: PASSWORD)


def run(live_check, data: Path, entries, inner, **options) -> tuple[int, str]:
    """The check on the fakes, with throttles that never sleep."""
    lines: list[str] = []
    time = InstantTime()
    code = live_check.run(
        Settings(data_dir=data), entries, inner, text="Example", spacing=0, poll=0.05,
        out=lines.append, registry=default_registry(time.clock, time.sleep), **options,
    )  # fmt: skip
    return code, "\n".join(lines)


# --- refusals -----------------------------------------------------------------------------


@pytest.fixture
def no_app(live_check: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*args, **kwargs):
        raise AssertionError("an app was built")

    monkeypatch.setattr(live_check, "create_app", refused)


@pytest.mark.parametrize("flag", [None, "", "yes", "true"])
def test_refused_without_pt_live_check_1(
    live_check, no_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, flag
) -> None:
    if flag is None:
        monkeypatch.delenv("PT_LIVE_CHECK", raising=False)
    else:
        monkeypatch.setenv("PT_LIVE_CHECK", flag)
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / ".data-live"))
    assert live_check.main([]) == 2
    assert "PT_LIVE_CHECK=1" in capsys.readouterr().out
    assert not (tmp_path / ".data-live").exists()


@pytest.mark.parametrize(
    ("folder", "says"),
    [(None, "set PT_DATA_DIR"), ("data", "must start with .data"),
     ("x.data", "must start with .data"), ("existing", "must not exist yet")],
)  # fmt: skip
def test_refused_without_a_new_data_folder(
    live_check, no_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, folder, says
) -> None:
    monkeypatch.setenv("PT_LIVE_CHECK", "1")
    if folder is None:
        monkeypatch.delenv("PT_DATA_DIR", raising=False)  # would fall back to /data
    elif folder == "existing":
        (tmp_path / ".data-live").mkdir()  # even empty
        monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / ".data-live"))
    else:
        monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / folder))
    assert live_check.main([]) == 2
    assert says in capsys.readouterr().out
    left = [".data-live"] if folder == "existing" else []  # only the one made by the test
    assert sorted(p.name for p in tmp_path.iterdir()) == left


@pytest.mark.parametrize(
    ("extras", "says"),
    [(["ZZ0000087013"], "ISIN:CURRENCY"), ([f"{fake_isin(1)}:eur"], "ISIN:CURRENCY"),
     ([f"{fake_isin(1)[:-1]}0:EUR"], "not a valid ISIN"),
     (["US0378331005:USD"], "already in the list"),
     (["US0378331005:EUR"], "already in the list"),  # one asset per ISIN, any currency
     ([f"{fake_isin(1)}:EUR", f"{fake_isin(1)}:USD"], "already in the list"),
     ([f"{fake_isin(n)}:EUR" for n in range(4)], "at most 3")],
)  # fmt: skip
def test_refused_with_a_wrong_extra(
    live_check, no_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, extras, says
) -> None:
    monkeypatch.setenv("PT_LIVE_CHECK", "1")
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / ".data-live"))
    argv = [arg for extra in extras for arg in ("--extra", extra)]
    assert live_check.main(argv) == 2
    assert says in capsys.readouterr().out
    assert not (tmp_path / ".data-live").exists()


# --- the transport that counts and stops ------------------------------------------------


class Ticks:
    def __init__(self, *times: float) -> None:
        self.times = list(times)

    def __call__(self) -> float:
        return self.times.pop(0)


def test_calls_are_counted_per_host_with_the_shortest_gap(live_check) -> None:
    inner = FakeTransport(queue(*[json_response({})] * 3))
    counting = live_check.CountingTransport(inner, cap=10, clock=Ticks(0.0, 1.5, 2.5))
    for url in (YAHOO, "https://api.openfigi.com/v3/mapping", YAHOO):
        assert counting.fetch(url, {"q": "x"}).status == 200  # answers unchanged
    assert counting.calls == {"query2.finance.yahoo.com": 2, "api.openfigi.com": 1}
    assert counting.gaps == {"query2.finance.yahoo.com": 2.5}
    assert counting.stopped is None


@pytest.mark.parametrize("status", [429, 503])
def test_after_a_429_or_503_no_call_leaves(live_check, status: int) -> None:
    inner = FakeTransport(queue(json_response({}, status, Retry_After="30")))
    counting = live_check.CountingTransport(inner, cap=10)
    assert counting.fetch(YAHOO).status == status  # the adapter sees the real answer
    for _ in range(3):
        with pytest.raises(live_check.ProviderError):
            counting.fetch("https://api.openfigi.com/v3/mapping")
    assert len(inner.calls) == 1
    assert counting.stopped == f"{status} from query2.finance.yahoo.com"


def test_the_cap_stops_the_calls(live_check) -> None:
    inner = FakeTransport(queue(*[json_response({})] * 2))
    counting = live_check.CountingTransport(inner, cap=2)
    counting.fetch(YAHOO)
    counting.fetch(YAHOO)
    with pytest.raises(live_check.ProviderError):
        counting.fetch(YAHOO)
    assert len(inner.calls) == 2 and counting.stopped == "the cap of 2 calls"


def test_the_cap_follows_the_list(live_check, entries) -> None:
    # 17 per ISIN, 7 for the text search, 1 per currency other than EUR (USD, GBP)
    assert live_check.max_calls(entries) == 17 * 4 + 7 + 2
    assert live_check.call_cap(entries) == 77 + 7
    assert live_check.max_calls(live_check.ENTRIES) == 17 * 5 + 7 + 2


# --- a whole pass, offline --------------------------------------------------------------


def test_a_whole_pass_on_the_fakes(live_check, offline, entries, tmp_path: Path) -> None:
    data = tmp_path / ".data-live"
    code, report = run(live_check, data, entries, FakeTransport(FakeMarket(data)), keep=True)

    assert code == 0, report
    assert "run CATCH_UP: OK" in report
    assert "ZSTK.MI · MIL · EUR · home (invented source)" in report
    assert "ZWLD.L · LSE · GBP in pence (stored ÷100) · secondary (home: MIL" in report
    assert "status    OK · 24.75 EUR on" in report
    assert "status    OK · 86.75 GBP on" in report  # 8675 pence
    assert report.count("status    NOT_AVAILABLE") == 2  # the unknown share and the bond
    assert "FX GBP: no rates" in report and "FX USD: no rates" in report
    assert 'Text search "Example": 1 result, none with an ISIN' in report
    assert f"Folder kept: {data}" in report
    with sqlite3.connect(data / "db" / "app.sqlite") as db:
        assert db.execute("SELECT kind FROM job_runs").fetchall() == [("CATCH_UP",)]
        rows = db.execute("SELECT a.currency, t.quantity FROM transactions t "
                          "JOIN accounts a ON a.id = t.account_id").fetchall()  # fmt: skip
    assert sorted(rows) == [("EUR", "1"), ("EUR", "100"), ("GBP", "1"), ("USD", "1")]
    key = (data / "secrets" / "secret.key").read_text().strip()  # base64 text
    assert key and key not in report and PASSWORD not in report


def test_an_extra_is_not_judged_and_the_folder_is_deleted(
    live_check, offline, entries, tmp_path: Path
) -> None:
    data = tmp_path / ".data-live"
    extra = live_check.extra_entry(1, fake_isin(8705), "USD")
    code, report = run(live_check, data, entries[:1], FakeTransport(FakeMarket(data)),
                       extras=(extra,))  # fmt: skip
    assert code == 0, report
    assert f"{fake_isin(8705)}  Extra 1 · ETF in USD" in report
    assert "not judged (--extra)" in report
    assert "Folder deleted." in report and not data.exists()


def test_a_429_stops_the_pass_and_no_call_leaves_after_it(
    live_check, offline, entries, tmp_path: Path
) -> None:
    data = tmp_path / ".data-live"
    market = FakeMarket(data)

    def handler(request):
        if "/v8/finance/chart/" in request.url:
            return json_response({}, 429, Retry_After="30")
        return market(request)

    inner = FakeTransport(handler)
    code, report = run(live_check, data, entries, inner)
    assert code == 3, report
    assert "stopped: 429 from query2.finance.yahoo.com" in report
    charts = [i for i, c in enumerate(inner.calls) if "/v8/finance/chart/" in c.url]
    assert charts == [len(inner.calls) - 1]  # the 429 was the last call that left
    assert not data.exists()
