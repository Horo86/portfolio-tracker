#!/usr/bin/env bash
# Starts the built app (frontend/dist served by FastAPI) for the end-to-end tests, on a fresh
# development data directory that is deleted first: never a real installation. Market data
# is off (no outbound call), cookies work over plain http.
#   e2e/serve.sh empty  8710   no user: the first-run screens
#   e2e/serve.sh seeded 8711   the invented development data (backend/scripts/dev_data.py)
set -euo pipefail

kind=$1
port=$2
frontend=$(cd "$(dirname "$0")/.." && pwd)
backend=$(cd "$frontend/../backend" && pwd)
data=$frontend/.data-e2e-$kind # ignored by git (.data*/); dev_data.py needs the .data name

rm -rf "$data"
mkdir -p "$data"
export PT_DATA_DIR=$data PT_HOST=127.0.0.1 PT_PORT=$port PT_STATIC_DIR=$frontend/dist \
  PT_MARKET_DATA=false PT_COOKIE_SECURE=false

if [ "$kind" = seeded ]; then
  uv run --directory "$backend" python scripts/dev_data.py --development
fi
exec uv run --directory "$backend" python -m portfolio_tracker
