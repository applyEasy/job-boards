#!/usr/bin/env bash
# One-time setup with your admin connection string: applies the bank migration,
# gives the crawler login a fresh password (never printed; Postgres only ever
# sees its SCRAM hash, so it can't land in a statement log), checks the login
# works, stores BANK_DATABASE_URL as this repo's secret, turns the crawl
# workflow on and starts the first discover run.
#
#   DATABASE_URL='postgresql://postgres.<ref>:<pw>@<pooler-host>:5432/postgres' scripts/setup_bank.sh
#
# A direct (db.<ref>.supabase.co) DATABASE_URL also works with
# POOLER_HOST=<Dashboard > Connect > Session pooler host>.
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL to the admin connection string}"
REPO=${REPO:-applyEasy/job-boards}
GH=${GH:-gh}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

part() { python3 -I -c 'import sys, urllib.parse as u; p = u.urlsplit(sys.argv[1]); print(getattr(p, sys.argv[2]) or "")' "$DATABASE_URL" "$1"; }
user=$(part username)
host=$(part hostname)
ref=${PROJECT_REF:-}
[[ -z $ref && $user == postgres.* ]] && ref=${user#postgres.}
[[ -z $ref && $host == db.*.supabase.co ]] && { ref=${host#db.}; ref=${ref%.supabase.co}; }
pooler=${POOLER_HOST:-}
[[ -z $pooler && $host == *.pooler.supabase.com ]] && pooler=$host
if [[ -z $ref || -z $pooler ]]; then
  echo "Can't tell the project ref or pooler host from DATABASE_URL; set PROJECT_REF and POOLER_HOST." >&2
  exit 1
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$ROOT/sql/20261014_boards_bank.sql"
echo "migration applied"

password=$(openssl rand -hex 24)
verifier=$(PW="$password" python3 -I -c '
import base64, hashlib, hmac, os
salt, rounds = os.urandom(16), 4096
salted = hashlib.pbkdf2_hmac("sha256", os.environ["PW"].encode(), salt, rounds)
client = hmac.new(salted, b"Client Key", "sha256").digest()
server = hmac.new(salted, b"Server Key", "sha256").digest()
b64 = lambda b: base64.b64encode(b).decode()
print(f"SCRAM-SHA-256${rounds}:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}")')
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "alter role job_boards_crawler login password '$verifier'"

url="postgresql://job_boards_crawler.$ref:$password@$pooler:5432/postgres"
psql "$url" -v ON_ERROR_STOP=1 -qtAc "select 'crawler login works, boards so far: ' || count(*) from bank_boards"

printf %s "$url" | $GH secret set BANK_DATABASE_URL -R "$REPO"
$GH workflow enable crawl.yml -R "$REPO"
$GH workflow run crawl.yml -R "$REPO" -f command=discover
echo "discover started; watch it: gh run list -R $REPO --workflow crawl.yml"
