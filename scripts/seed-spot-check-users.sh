#!/usr/bin/env bash
#
# Seed users + Beta org for the Progress Demo table-access spot check.
#
# Idempotent — re-running will refresh password hashes and ensure the
# org/user records exist with the expected stable UUIDs.
#
# Prereqs:
#   - debug stack must be running (./debug.sh)
#   - dev@gobifrost.com / password is the bootstrapped superuser
#
# Result:
#   - Org "Beta" with id 00000000-0000-0000-0000-000000000003
#   - alice@gobifrost.com / password (in Provider, non-superuser)
#   - bob@gobifrost.com   / password (in Beta, non-superuser)
#
# See docs/spot-checks/2026-05-03-progress-demo-org-gates.md for the
# manual procedure that exercises these accounts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Derive compose project name the same way ./debug.sh does
# (scripts/lib/test_helpers.sh::compute_project_name with the debug prefix).
BIFROST_PROJECT_PREFIX="bifrost-debug"
# shellcheck source=lib/test_helpers.sh
source "$SCRIPT_DIR/lib/test_helpers.sh"
PROJECT="$(compute_project_name "$WORKTREE_ROOT")"
API="${PROJECT}-api-1"
PG="${PROJECT}-postgres-1"

PROVIDER_ORG_ID="00000000-0000-0000-0000-000000000002"
BETA_ORG_ID="00000000-0000-0000-0000-000000000003"

# 1. Get a superuser token from dev@gobifrost.com
TOK="$(docker exec "$API" curl -sS -X POST http://localhost:8000/auth/login \
  -d 'username=dev@gobifrost.com&password=password' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"

if [[ -z "$TOK" ]]; then
  echo "ERROR: failed to log in as dev@gobifrost.com" >&2
  exit 1
fi

# 2. Ensure Beta org exists with the stable UUID. The orgs API auto-generates
#    UUIDs, so we patch the row id via SQL when creating fresh.
EXISTING="$(docker exec "$PG" psql -U bifrost -d bifrost -tAc \
  "SELECT id FROM organizations WHERE name='Beta';")"

if [[ -z "$EXISTING" ]]; then
  echo "Creating Beta org..."
  CREATED_ID="$(docker exec "$API" curl -sS -X POST http://localhost:8000/api/organizations \
    -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" \
    -d '{"name":"Beta"}' \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
  docker exec "$PG" psql -U bifrost -d bifrost -c \
    "UPDATE organizations SET id='$BETA_ORG_ID' WHERE id='$CREATED_ID';" > /dev/null
elif [[ "$EXISTING" != "$BETA_ORG_ID" ]]; then
  echo "Re-keying existing Beta org $EXISTING -> $BETA_ORG_ID..."
  docker exec "$PG" psql -U bifrost -d bifrost -c \
    "UPDATE organizations SET id='$BETA_ORG_ID' WHERE id='$EXISTING';" > /dev/null
else
  echo "Beta org already exists at $BETA_ORG_ID."
fi

# 3. Compute a bcrypt hash of "password" using the API's own helper so the
#    hash format and rounds match production exactly.
HASH="$(docker exec "$API" python -c \
  "from src.core.security import get_password_hash; print(get_password_hash('password'))")"

create_or_update_user() {
  local email="$1"
  local name="$2"
  local org_id="$3"

  local user_id
  user_id="$(docker exec "$PG" psql -U bifrost -d bifrost -tAc \
    "SELECT id FROM users WHERE email='$email';")"

  if [[ -z "$user_id" ]]; then
    echo "Creating $email in org $org_id..."
    docker exec "$API" curl -sS -X POST http://localhost:8000/api/users \
      -H "Authorization: Bearer $TOK" \
      -H "Content-Type: application/json" \
      -d "{\"email\":\"$email\",\"name\":\"$name\",\"password\":\"password\",\"organization_id\":\"$org_id\",\"is_superuser\":false,\"is_active\":true}" \
      > /dev/null
  else
    echo "$email already exists, refreshing hash + flags..."
  fi

  # The admin user-create endpoint stores empty password and is_registered=false,
  # so always force the hash + registered flag via SQL afterward.
  docker exec "$PG" psql -U bifrost -d bifrost -c \
    "UPDATE users
       SET hashed_password = '$HASH',
           is_registered    = true,
           is_active        = true,
           is_verified      = true,
           organization_id  = '$org_id'
     WHERE email = '$email';" > /dev/null
}

create_or_update_user alice@gobifrost.com Alice "$PROVIDER_ORG_ID"
create_or_update_user bob@gobifrost.com   Bob   "$BETA_ORG_ID"

# 3.5. Make the Progress Demo app global so Bob (in Beta) can navigate to it.
#      The org gate we want to spot-check is on the TABLE, not the APP — if the
#      app stays org-scoped Bob never reaches the page and the table read is
#      unreachable. Idempotent: no-op when already NULL.
docker exec "$PG" psql -U bifrost -d bifrost -c \
  "UPDATE applications
     SET organization_id = NULL
   WHERE id = 'ae1740ef-bc04-404b-8bd6-b671fdde3b77';" > /dev/null
echo "Progress Demo app set to global (cross-org users can now open it)"

# 4. Smoke-test both logins
for u in alice@gobifrost.com bob@gobifrost.com; do
  if docker exec "$API" curl -sS -X POST http://localhost:8000/auth/login \
       -d "username=$u&password=password" \
     | grep -q '"access_token"'; then
    echo "  login OK: $u"
  else
    echo "  LOGIN FAILED for $u" >&2
    exit 1
  fi
done

echo
echo "Done. Spot-check accounts ready:"
echo "  Provider org : $PROVIDER_ORG_ID"
echo "  Beta org     : $BETA_ORG_ID"
echo "  alice@gobifrost.com / password  (Provider, non-superuser)"
echo "  bob@gobifrost.com   / password  (Beta,     non-superuser)"
