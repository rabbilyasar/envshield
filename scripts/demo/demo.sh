#!/usr/bin/env bash
# The README/website demo: a coding agent adds a config read; EnvShield
# catches it in code, then in the local .env and the Compose container.
#
# Re-record (needs agg: cargo install --git https://github.com/asciinema/agg):
#   python3 scripts/demo/record.py 100 44 /tmp/demo.cast bash scripts/demo/demo.sh
#   agg --theme monokai --font-size 14 --idle-time-limit 2.5 /tmp/demo.cast docs/assets/envshield-demo.gif
set -euo pipefail
ES=${ENVSHIELD:-envshield}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

# --- a two-service project with a registered Compose file ---
git init -q -b main && git config user.email demo@example.com && git config user.name demo
mkdir api worker
printf 'import os\n\nDATABASE_URL = os.environ["DATABASE_URL"]\nPORT = int(os.environ.get("PORT", "8000"))\n' > api/app.py
printf 'import os\n\nDATABASE_URL = os.environ["DATABASE_URL"]\n' > worker/jobs.py
cat > env.schema.toml <<'EOF'
[DATABASE_URL]
description = "Postgres connection string"
type = "url"
secret = true
services = ["api", "worker"]

[PORT]
description = "Port the API listens on"
type = "port"
defaultValue = "8000"
services = ["api"]
EOF
cat > envshield.yml <<'EOF'
project_name: shop
services:
  api:
    schema: env.schema.toml
    dir: api
    local_file: api/.env
  worker:
    schema: env.schema.toml
    dir: worker
    local_file: worker/.env
manifests:
  - file: docker-compose.yml
    containers:
      api: api
      worker: worker
EOF
cat > docker-compose.yml <<'EOF'
services:
  api:
    build: ./api
    environment:
      DATABASE_URL: postgres://app@db/shop
      PORT: "8000"
  worker:
    build: ./worker
    environment:
      DATABASE_URL: postgres://app@db/shop
  db:
    image: postgres:16
EOF
printf 'DATABASE_URL=postgres://app@localhost/shop\nPORT=8000\n' > api/.env
printf 'DATABASE_URL=postgres://app@localhost/shop\n' > worker/.env
printf '.env\n' > .gitignore
git add -A && git commit -qm "shop: api + worker"
clear

# --- the recorded part ---
type_cmd() { printf '\033[1;32m$\033[0m '; for ((i = 0; i < ${#1}; i++)); do printf '%s' "${1:i:1}"; sleep 0.03; done; echo; sleep 0.4; }
say() { printf '\033[2m# %s\033[0m\n' "$1"; sleep 1.2; }

say "A coding agent just added a webhook endpoint to the api service."
printf '\n# webhook endpoint (added by agent)\nSTRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]\n' >> api/app.py
type_cmd "git diff --stat"; git --no-pager diff --stat; sleep 1.5
say "Nobody declared the new variable. EnvShield notices before the commit:"
# stdin from /dev/null: run every service, as CI does (no service picker).
type_cmd "envshield undeclared"; COLUMNS=100 "$ES" undeclared </dev/null || true; sleep 3
say "Declare it once in the project contract, scoped to the api service only:"
printf '\n[STRIPE_WEBHOOK_SECRET]\ndescription = "Signs Stripe webhook events"\nsecret = true\nservices = ["api"]\n' >> env.schema.toml
type_cmd "tail -5 env.schema.toml"; tail -5 env.schema.toml; sleep 2
say "Now check every service, locally and in the deployment manifest:"
type_cmd "envshield check"; COLUMNS=100 "$ES" check </dev/null || true; sleep 3
say "api's .env and its compose container both lack it; worker is untouched. Caught before deploy."
sleep 2
