"""R-14 (D-162): at most 2 Argon2id operations at once in the process; a check waits for a
slot for a short time, then the request is answered 429 TOO_MANY_ATTEMPTS with Retry-After,
counted by no throttle. The slot is released on every path."""

import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import uvicorn
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, create_admin, login_token

from portfolio_tracker.auth.passwords import PasswordChecksBusy, Passwords
from portfolio_tracker.main import create_app

LOGIN = "/api/v1/auth/login"


class InstrumentedHasher:
    """A cheap Argon2 hasher that records how many operations run at once; it can hold every
    operation on an event, slow it down or make it raise."""

    def __init__(self) -> None:
        self._inner = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        self._lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.calls: list[str] = []
        self.hold: threading.Event | None = None
        self.delay = 0.0
        self.fail = False

    def reset(self) -> None:
        with self._lock:
            self.peak, self.calls = self.running, []

    def _run(self, kind, operation):
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.calls.append(kind)
        try:
            if self.hold is not None:
                self.hold.wait(10)
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("hasher failed")
            return operation()
        finally:
            with self._lock:
                self.running -= 1

    def hash(self, password: str) -> str:
        return self._run("hash", lambda: self._inner.hash(password))

    def verify(self, password_hash: str, password: str) -> bool:
        return self._run("verify", lambda: self._inner.verify(password_hash, password))

    def check_needs_rehash(self, password_hash: str) -> bool:
        return self._inner.check_needs_rehash(password_hash)


def wait_until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


@contextmanager
def both_slots_held(hasher: InstrumentedHasher, run) -> Iterator[None]:
    """Two operations started by `run` (called in two threads) hold both slots until exit."""
    hasher.hold = threading.Event()
    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    try:
        wait_until(lambda: hasher.running == 2)
        yield
    finally:
        hasher.hold.set()
        for thread in threads:
            thread.join(10)
        hasher.hold = None


# --- The limiter itself -------------------------------------------------------------------


def test_at_most_two_operations_run_at_once() -> None:
    hasher = InstrumentedHasher()
    passwords = Passwords(hasher, wait=5.0)
    stored = passwords.hash(PASSWORD)
    hasher.reset()
    hasher.delay = 0.05
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: passwords.verify(stored, PASSWORD), range(6)))
    assert results == [True] * 6  # waiting is not refusing: every check ran
    assert hasher.peak == 2
    assert passwords.in_use == 0


def test_an_operation_that_gets_no_slot_within_the_wait_is_refused() -> None:
    hasher = InstrumentedHasher()
    passwords = Passwords(hasher, wait=0.2)
    stored = passwords.hash(PASSWORD)
    with both_slots_held(hasher, lambda _: passwords.verify(stored, PASSWORD)):
        started = time.monotonic()
        with pytest.raises(PasswordChecksBusy) as busy:
            passwords.verify(stored, PASSWORD)
        assert 0.15 <= time.monotonic() - started < 2  # it waited, then gave up
        assert busy.value.retry_after >= 1
    assert passwords.in_use == 0


def test_beyond_the_waiting_room_an_operation_is_refused_at_once() -> None:
    """Waiting holds a worker thread: at most 8 wait, so other routes keep their threads."""
    hasher = InstrumentedHasher()
    passwords = Passwords(hasher, wait=5.0)
    stored = passwords.hash(PASSWORD)
    with (
        both_slots_held(hasher, lambda _: passwords.verify(stored, PASSWORD)),
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        waiting = [pool.submit(passwords.verify, stored, PASSWORD) for _ in range(8)]
        wait_until(lambda: passwords.waiting == 8)
        started = time.monotonic()
        with pytest.raises(PasswordChecksBusy):
            passwords.verify(stored, PASSWORD)
        assert time.monotonic() - started < 1
        hasher.hold.set()
        assert [future.result(10) for future in waiting] == [True] * 8
    assert passwords.in_use == 0


def test_the_slot_is_released_after_a_wrong_password_and_an_exception() -> None:
    hasher = InstrumentedHasher()
    passwords = Passwords(hasher, wait=0.2)
    stored = passwords.hash(PASSWORD)
    for _ in range(3):  # more times than there are slots
        assert passwords.verify(stored, "a wrong password") is False
    hasher.fail = True
    for _ in range(3):
        with pytest.raises(RuntimeError):
            passwords.hash("a new passphrase!")
        with pytest.raises(RuntimeError):
            passwords.verify(stored, PASSWORD)
    hasher.fail = False
    assert passwords.in_use == 0
    assert passwords.verify(stored, PASSWORD) is True


# --- Through the API ----------------------------------------------------------------------


@pytest.fixture
def hasher() -> InstrumentedHasher:
    return InstrumentedHasher()


@pytest.fixture
def client(settings, clock, hasher: InstrumentedHasher) -> Iterator[TestClient]:
    app = create_app(settings, passwords=Passwords(hasher, wait=0.3), clock=clock)
    # https: the session cookie is Secure by default (as in conftest)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def unknown_login(client: TestClient, i: int):
    return client.post(LOGIN, json={"username": f"nobody{i}", "password": "a wrong guess!"})


def assert_busy(response) -> None:
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["retry_after"] == int(response.headers["Retry-After"])


def test_a_burst_of_30_logins_never_runs_more_than_two_hashes(
    client: TestClient, hasher: InstrumentedHasher
) -> None:
    """30 logins with different usernames (one address may try 30, D-82): peak 2 (R-14)."""
    hasher.reset()
    hasher.delay = 0.1
    with ThreadPoolExecutor(max_workers=30) as pool:
        responses = list(pool.map(lambda i: unknown_login(client, i), range(30)))
    statuses = [r.status_code for r in responses]
    assert hasher.peak == 2
    assert set(statuses) <= {401, 429}
    assert statuses.count(401) == len(hasher.calls)  # every check that ran was answered
    refused = [r for r in responses if r.status_code == 429]
    assert refused  # 2 run, 8 wait at most 0.3 s, the others are refused
    for response in refused:
        assert_busy(response)


def test_a_login_that_gets_no_slot_is_429_and_counted_by_no_throttle(
    client: TestClient, hasher: InstrumentedHasher
) -> None:
    create_admin(client)
    wrong = {"username": "admin", "password": "a wrong guess!"}
    with both_slots_held(hasher, lambda i: unknown_login(client, i)):
        for _ in range(7):  # more than the 5 free failures per username (D-82)
            assert_busy(client.post(LOGIN, json=wrong))
    assert login_token(client, "admin")  # none of them counted as a failure


def test_a_normal_sequence_of_logins_is_not_slowed(client: TestClient) -> None:
    create_admin(client)
    started = time.monotonic()
    for _ in range(5):
        assert login_token(client, "admin")
    assert time.monotonic() - started < 2


def test_an_unknown_user_takes_the_same_path_as_a_wrong_password(
    client: TestClient, hasher: InstrumentedHasher
) -> None:
    """Timing equalization kept (D-82): one verification through the limiter either way."""
    create_admin(client)
    for username in ("nobody", "admin"):
        hasher.reset()
        response = client.post(LOGIN, json={"username": username, "password": "a wrong guess!"})
        assert response.json()["code"] == "INVALID_CREDENTIALS"
        assert hasher.calls == ["verify"]
    with both_slots_held(hasher, lambda i: unknown_login(client, i)):
        assert_busy(client.post(LOGIN, json={"username": "nobody", "password": "a wrong guess!"}))
        assert_busy(client.post(LOGIN, json={"username": "admin", "password": "a wrong guess!"}))


def test_setup_waits_for_a_slot_too(client: TestClient, hasher: InstrumentedHasher) -> None:
    body = {"username": "admin", "password": PASSWORD, "language": "en"}
    with both_slots_held(hasher, lambda i: unknown_login(client, i)):
        assert_busy(client.post("/api/v1/setup/admin", json=body))
    assert client.post("/api/v1/setup/admin", json=body).status_code == 201


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/me/password",
            {"current_password": PASSWORD, "new_password": "a new passphrase!"},
        ),
        ("/api/v1/admin/users", {"username": "marta"}),
        ("reset", None),
    ],
)
def test_every_route_that_hashes_waits_for_a_slot(
    client: TestClient, hasher: InstrumentedHasher, path: str, body: dict | None
) -> None:
    create_admin(client)
    if path == "reset":
        created = client.post("/api/v1/admin/users", headers=CSRF, json={"username": "marta"})
        path = f"/api/v1/admin/users/{created.json()['user']['id']}/reset-password"
    with both_slots_held(hasher, lambda i: unknown_login(client, i)):
        for _ in range(7):  # a busy password change is no wrong guess either (D-82)
            assert_busy(client.post(path, headers=CSRF, json=body))
    assert client.post(path, headers=CSRF, json=body).status_code in (200, 201)


def test_other_routes_keep_answering_while_hashes_wait(
    client: TestClient, hasher: InstrumentedHasher
) -> None:
    create_admin(client)
    with (
        both_slots_held(hasher, lambda i: unknown_login(client, i)),
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        waiting = [pool.submit(unknown_login, client, 10 + i) for i in range(8)]
        started = time.monotonic()
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/v1/me").status_code == 200
        assert client.get("/api/v1/accounts").status_code == 200
        assert time.monotonic() - started < 0.25  # well within the 0.3 s wait
        for future in waiting:
            future.result(10)


# --- A client that goes away --------------------------------------------------------------


def _post(port: int, path: str, body: dict) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    status = connection.getresponse().status
    connection.close()
    return status


def test_the_slot_is_released_when_the_client_disconnects(settings, clock) -> None:
    hasher = InstrumentedHasher()
    passwords = Passwords(hasher, wait=0.3)
    app = create_app(settings, passwords=passwords, clock=clock)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run)
    thread.start()
    try:
        wait_until(lambda: server.started)
        port = server.servers[0].sockets[0].getsockname()[1]

        hasher.hold = threading.Event()
        body = json.dumps({"username": "nobody", "password": "a wrong guess!"}).encode()
        for _ in range(2):  # both slots, then both clients go away
            raw = socket.create_connection(("127.0.0.1", port))
            raw.sendall(
                b"POST /api/v1/auth/login HTTP/1.1\r\nHost: test\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            wait_until(lambda: hasher.running >= 1)
            raw.close()
        wait_until(lambda: hasher.running == 2)
        hasher.hold.set()
        wait_until(lambda: passwords.in_use == 0)
        hasher.hold = None
        assert _post(port, LOGIN, {"username": "nobody", "password": "a wrong guess!"}) == 401
    finally:
        if hasher.hold is not None:
            hasher.hold.set()
        server.should_exit = True
        thread.join(10)
