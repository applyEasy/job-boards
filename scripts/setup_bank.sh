#!/usr/bin/env bash
# One-time setup: gives the crawler login a fresh password (never printed;
# Postgres only ever sees its SCRAM hash, so it can't land in a statement log),
# checks the login works, stores BANK_DATABASE_URL as this repo's secret, turns
# the crawl workflow on and starts the first discover run.
#
# Already applied sql/20261014_boards_bank.sql in the SQL editor? Then:
#   PROJECT_REF=<ref> POOLER_HOST=<Dashboard > Connect > Session pooler host> scripts/setup_bank.sh
# It prints one `alter role` line to run in the SQL editor and waits for you.
#
# Or let it apply the migration too, with your admin connection string:
#   DATABASE_URL='postgresql://postgres.<ref>:<pw>@<pooler-host>:5432/postgres' scripts/setup_bank.sh
set -euo pipefail
REPO=${REPO:-applyEasy/job-boards}
GH=${GH:-gh}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ref=${PROJECT_REF:-}
pooler=${POOLER_HOST:-}

if [[ -n ${DATABASE_URL:-} ]]; then
  case $DATABASE_URL in
    postgres://*|postgresql://*) ;;
    *) echo "DATABASE_URL must be the Postgres connection string (postgresql://...), not the API URL." >&2
       echo "Supabase Dashboard > Connect > Session pooler > URI, with your database password filled in." >&2
       exit 1 ;;
  esac
  part() { python3 -I -c 'import sys, urllib.parse as u; p = u.urlsplit(sys.argv[1]); print(getattr(p, sys.argv[2]) or "")' "$DATABASE_URL" "$1"; }
  user=$(part username)
  host=$(part hostname)
  [[ -z $ref && $user == postgres.* ]] && ref=${user#postgres.}
  [[ -z $ref && $host == db.*.supabase.co ]] && { ref=${host#db.}; ref=${ref%.supabase.co}; }
  [[ -z $pooler && $host == *.pooler.supabase.com ]] && pooler=$host
fi
if [[ -z $ref || -z $pooler ]]; then
  echo "Set PROJECT_REF and POOLER_HOST (Dashboard > Connect > Session pooler shows both)." >&2
  exit 1
fi

password=$(openssl rand -hex 24)
verifier=$(PW="$password" python3 -I -c '
import base64, hashlib, hmac, os
salt, rounds = os.urandom(16), 4096
salted = hashlib.pbkdf2_hmac("sha256", os.environ["PW"].encode(), salt, rounds)
client = hmac.new(salted, b"Client Key", "sha256").digest()
server = hmac.new(salted, b"Server Key", "sha256").digest()
b64 = lambda b: base64.b64encode(b).decode()
print(f"SCRAM-SHA-256${rounds}:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}")')
grant="alter role job_boards_crawler login password '$verifier';"

if [[ -n ${DATABASE_URL:-} ]]; then
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$ROOT/sql/20261014_boards_bank.sql"
  echo "migration applied"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "$grant"
else
  echo "Run this one line in the Supabase SQL editor (it holds only a hash of the new password):"
  echo
  echo "  $grant"
  echo
  read -r -p "Press Enter once it has run... " _
fi

url="postgresql://job_boards_crawler.$ref:$password@$pooler:5432/postgres"
psql "$url" -v ON_ERROR_STOP=1 -qtAc "select 'crawler login works, boards so far: ' || count(*) from bank_boards"

printf %s "$url" | $GH secret set BANK_DATABASE_URL -R "$REPO"
$GH workflow enable crawl.yml -R "$REPO"
$GH workflow run crawl.yml -R "$REPO" -f command=discover
echo "discover started; watch it: gh run list -R $REPO --workflow crawl.yml"
