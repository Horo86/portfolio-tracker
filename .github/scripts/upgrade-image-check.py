"""Upgrade a data folder written by the previous release's image with a new image (D-168 (7)).

1. The old image starts on a new, empty folder (market data off) and gets a few invented
   records through its API: the setup wizard, an account, a security with a `ZZ` ISIN,
   transactions and a manual price; its figures are read.
2. It stops; the new image starts on the same folder and must show the same figures and the
   same transactions. The pre-migration copy: none when both images have the same schema
   revision, exactly one (checked and recorded as done) when the new one is ahead.
3. Both containers are removed by name and the folder is deleted, whatever happened.

Standard library only. The containers run with the caller's user id, so the folder needs no
chown; they listen on 127.0.0.1 only. The folder must not exist and its name must contain
`pt-upgrade-check`, so a real data folder can never be used.

Usage: upgrade-image-check.py --old IMAGE|--old-from FILE --new IMAGE --port N --folder PATH
"""

import argparse
import http.cookiejar
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

CSRF = {"X-Requested-With": "portfolio-tracker"}
ADMIN = ("admin", "upgrade-check-password")  # public, invented; the folder is deleted after
ISIN = "ZZ0000009116"  # no country: no real security
HEAD_COMMAND = (
    "from alembic.script import ScriptDirectory;"
    "from portfolio_tracker.migrations_runner import alembic_config;"
    "from pathlib import Path;"
    "print('-'.join(sorted(ScriptDirectory.from_config(alembic_config(Path('x'))).get_heads())))"
)


class CheckFailed(Exception):
    pass


def image_from(path: Path) -> str:
    """The first line of `path` that is not empty and not a comment."""
    for line in path.read_text("utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            return line.strip()
    raise CheckFailed(f"{path} names no image")


def contains(expected: Any, actual: Any, where: str = "$") -> list[str]:
    """The differences: every value of `expected` must be in `actual` (new fields are fine)."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{where}: not an object"]
        problems = []
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"{where}.{key}: missing")
            else:
                problems += contains(value, actual[key], f"{where}.{key}")
        return problems
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{where}: length"]
        problems = []
        for i, (want, got) in enumerate(zip(expected, actual, strict=True)):
            problems += contains(want, got, f"{where}[{i}]")
        return problems
    return [] if expected == actual else [f"{where}: {actual!r} != {expected!r}"]


def expected_copies(old_revision: str, new_head: str) -> int:
    return 0 if old_revision == new_head else 1


class Api:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = CSRF | ({"Content-Type": "application/json"} if data else {})
        request = urllib.request.Request(
            f"{self.base}/api/v1{path}", data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                text = response.read().decode()
                kind = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            raise CheckFailed(f"{method} {path}: {error.code} {error.read().decode()}") from None
        if not text:
            return None
        return json.loads(text) if "json" in kind else text

    def health(self, seconds: float = 60) -> dict:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/api/health", timeout=2) as response:
                    return json.loads(response.read())
            except (OSError, ValueError):
                time.sleep(0.5)
        raise CheckFailed(f"no health answer on {self.base} within {seconds:.0f} s")


def docker(*args: str, check: bool = True) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise CheckFailed(f"docker {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def start(name: str, image: str, folder: Path, port: int) -> None:
    docker(
        "run", "-d", "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
        "-p", f"127.0.0.1:{port}:8000", "-v", f"{folder}:/data",
        "-e", "PT_COOKIE_SECURE=false", "-e", "PT_MARKET_DATA=false", image,
    )  # fmt: skip


def seed(api: Api) -> None:
    api.call("POST", "/setup/admin", {"username": ADMIN[0], "password": ADMIN[1], "language": "en"})
    account = api.call("POST", "/accounts", {"name": "Upgrade check", "currency": "EUR"})
    asset = api.call("POST", "/assets", {
        "name": "Upgrade Check ETF", "asset_type": "ETF", "ticker": "ZZUPG", "isin": ISIN,
        "quote_currency": "EUR", "quote_unit": "CURRENCY"})  # fmt: skip
    for body in (
        {"type": "DEPOSIT", "trade_date": "2025-01-02", "total_amount": "5000.00"},
        {"type": "BUY", "trade_date": "2025-01-10", "asset_id": asset["id"], "quantity": "20",
         "unit_price": "100.00", "gross_amount": "2000.00", "fees": "1.50",
         "total_amount": "2001.50"},
        {"type": "DIVIDEND", "trade_date": "2025-06-02", "asset_id": asset["id"],
         "gross_amount": "12.00", "taxes": "3.12", "total_amount": "8.88"},
    ):  # fmt: skip
        api.call("POST", "/transactions", {"account_id": account["id"]} | body)
    api.call("PUT", f"/assets/{asset['id']}/prices", {"day": "2025-12-30", "close": "104.25"})


def figures(api: Api) -> dict:
    return {
        "accounts": api.call("GET", "/accounts"),
        "summary": api.call("GET", "/portfolio/summary"),
        "positions": api.call("GET", "/portfolio/positions"),
        "export_csv": api.call("GET", "/transactions/export.csv"),
    }


def revision_of(db_path: Path) -> str:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return "-".join(sorted(row[0] for row in rows))


def check(old: str, new: str, port: int, folder: Path) -> None:
    names = ("pt-upgrade-check-old", "pt-upgrade-check-new")
    if "pt-upgrade-check" not in folder.name:
        raise CheckFailed(f"{folder}: the folder name must contain pt-upgrade-check")
    if folder.exists():
        raise CheckFailed(f"{folder} exists: give a new folder")
    for name in names:
        docker("rm", "-f", name, check=False)
    folder.mkdir(mode=0o700, parents=True)
    try:
        api = Api(port)
        start(names[0], old, folder, port)
        old_health = api.health()
        print(f"old image {old_health['version']} is up")
        seed(api)
        before = figures(api)
        docker("stop", "-t", "30", names[0])
        docker("rm", names[0])
        old_revision = revision_of(folder / "db" / "app.sqlite")
        new_head = docker("run", "--rm", "--entrypoint", "python", new, "-c", HEAD_COMMAND)
        print(f"old schema {old_revision}, new image head {new_head}")

        start(names[1], new, folder, port)
        new_health = api.health()
        print(f"new image {new_health['version']} is up on the same folder")
        api = Api(port)  # a new session: the login must work after the upgrade
        api.call("POST", "/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})
        problems = contains(before, figures(api))
        if problems:
            raise CheckFailed("the figures changed after the upgrade: " + "; ".join(problems[:10]))
        copies = sorted((folder / "backups" / "pre-migration").glob("app-*.sqlite"))
        wanted = expected_copies(old_revision, new_head)
        if len(copies) != wanted:
            raise CheckFailed(f"{len(copies)} pre-migration copies, expected {wanted}")
        for copy in copies:
            record = json.loads(copy.with_suffix(".json").read_text("utf-8"))
            if record["status"] != "done" or revision_of(copy) != old_revision:
                raise CheckFailed(f"{copy.name}: not a done copy of revision {old_revision}")
        print(f"upgrade check passed: same figures, {len(copies)} pre-migration copy(ies)")
    except CheckFailed:
        for name in names:
            logs = subprocess.run(["docker", "logs", "--tail", "40", name],
                                  capture_output=True, text=True)  # fmt: skip
            if logs.returncode == 0:
                print(f"--- last log lines of {name} ---\n{logs.stdout}{logs.stderr}")
        raise
    finally:
        for name in names:
            docker("rm", "-f", name, check=False)
        shutil.rmtree(folder, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--old", help="the previous release's image, pinned by digest")
    source.add_argument("--old-from", type=Path, help="a file naming that image")
    parser.add_argument("--new", required=True, help="the image to check")
    parser.add_argument("--port", type=int, required=True, help="a free port on 127.0.0.1")
    parser.add_argument("--folder", type=Path, required=True, help="a new pt-upgrade-check folder")
    args = parser.parse_args(argv)
    try:
        old = args.old or image_from(args.old_from)
        check(old, args.new, args.port, args.folder.resolve())
    except CheckFailed as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
