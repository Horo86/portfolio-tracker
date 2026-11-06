#!/usr/bin/env bash
# Waits for the release image's health endpoint on localhost:8000, then checks that it reports
# the release's version, build date and preview flag (D-167). Used by release.yml before the
# push and again on the image pulled back by digest. Reads VERSION (the tag without "v"),
# PY_VERSION (the same as Python writes it) and BUILD_DATE from the environment.
set -euo pipefail

for _ in $(seq 1 100); do
  if curl -sf http://localhost:8000/api/health -o health.json; then
    exec python3 - <<'PY'
import json, os, re

body, version = json.load(open("health.json")), os.environ["VERSION"]
preview = not re.fullmatch(r"\d+\.\d+\.\d+", version) or version.split(".")[0] == "0"
print(body)
assert body["status"] == "ok" and body["database"] == "ok", "not healthy"
assert body["version"] == os.environ["PY_VERSION"], "wrong version"
assert body["build_date"] == os.environ["BUILD_DATE"], "wrong build date"
assert body["preview"] is preview, "wrong preview flag"
PY
  fi
  sleep 0.2
done
echo "::error::the health endpoint did not answer within 20 s"
docker logs "$(docker ps -lq)" || true
exit 1
