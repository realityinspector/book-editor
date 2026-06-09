#!/usr/bin/env bash
#
# clean-test-data.sh — remove QA / test data from the Railway Postgres DB.
#
# Connects through `railway connect Postgres` (the same path used during QA),
# so the database credential is injected by the Railway CLI and never printed.
#
# What it removes:
#   • every NON-admin user account (QA registrations) plus their annotations,
#     mentions, and book shares
#   • optionally, anonymous annotations (user_id IS NULL) — see --include-anon
#
# What it ALWAYS keeps:
#   • admin accounts (is_admin = true)
#   • any username listed in KEEP_USERS (comma-separated env var)
#
# Safety:
#   • DRY RUN by default — shows what would be deleted and changes nothing.
#   • Pass --yes to actually delete.
#
# WARNING: This treats every non-admin account as disposable test data. Once the
# app has real (non-admin) readers, list the ones to preserve in KEEP_USERS, or
# delete specific rows by hand instead of running this blindly.
#
# Usage:
#   ./clean-test-data.sh                  # dry run (default)
#   ./clean-test-data.sh --yes            # delete test users + their data
#   ./clean-test-data.sh --yes --include-anon   # also wipe anonymous annotations
#   KEEP_USERS="alice,bob" ./clean-test-data.sh --yes
#
set -euo pipefail

APPLY=0
INCLUDE_ANON=0
for arg in "$@"; do
  case "$arg" in
    --yes) APPLY=1 ;;
    --include-anon) INCLUDE_ANON=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v railway >/dev/null 2>&1; then
  echo "Error: railway CLI not found. Install it and run 'railway link' first." >&2
  exit 1
fi

# Build a SQL-safe quoted list of usernames to keep (always plus admins).
KEEP_USERS="${KEEP_USERS:-}"
keep_clause=""
if [ -n "$KEEP_USERS" ]; then
  IFS=',' read -ra names <<< "$KEEP_USERS"
  quoted=""
  for n in "${names[@]}"; do
    n="$(echo "$n" | xargs)"   # trim whitespace
    [ -z "$n" ] && continue
    quoted="${quoted}${quoted:+, }'${n//\'/\'\'}'"
  done
  [ -n "$quoted" ] && keep_clause="OR username IN ($quoted)"
fi

# A user is "protected" if admin or explicitly kept. Test users are the rest.
# Use a session-scoped TEMP TABLE so every statement in the piped psql session
# can reference the same set (a CTE would only bind to one statement).
mk_test_users="CREATE TEMP TABLE _test_users AS
  SELECT id FROM users WHERE NOT (is_admin = true ${keep_clause});"

anon_clause=""
if [ "$INCLUDE_ANON" -eq 1 ]; then
  anon_clause="OR user_id IS NULL"
fi

# ── Dry-run preview ──────────────────────────────────────────────────────────
preview_sql="${mk_test_users}
\\echo '--- users to delete ---'
SELECT id, username, display_name FROM users
  WHERE id IN (SELECT id FROM _test_users) ORDER BY id;
\\echo '--- annotations to delete ---'
SELECT id, draft_id, user_id, author_name, LEFT(comment, 40) AS comment
  FROM annotations
  WHERE user_id IN (SELECT id FROM _test_users) ${anon_clause}
  ORDER BY id;
\\echo '--- summary ---'
SELECT (SELECT COUNT(*) FROM users WHERE id IN (SELECT id FROM _test_users)) AS users_to_delete,
       (SELECT COUNT(*) FROM annotations
          WHERE user_id IN (SELECT id FROM _test_users) ${anon_clause}) AS annotations_to_delete,
       (SELECT COUNT(*) FROM users WHERE is_admin = true) AS admins_kept;"

echo "==> Previewing test data (admins are always kept; KEEP_USERS='${KEEP_USERS}')"
echo "$preview_sql" | railway connect Postgres

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "DRY RUN — nothing was deleted. Re-run with --yes to apply."
  exit 0
fi

# ── Apply deletion in a single transaction ──────────────────────────────────
# Freeze both target sets into temp tables before deleting, so the DELETEs can
# reference them in any order without re-evaluating against already-changed rows.
delete_sql="${mk_test_users}
CREATE TEMP TABLE _doomed_anns AS
  SELECT id FROM annotations
    WHERE user_id IN (SELECT id FROM _test_users) ${anon_clause};
BEGIN;
DELETE FROM mentions
  WHERE annotation_id IN (SELECT id FROM _doomed_anns)
     OR mentioned_user_id IN (SELECT id FROM _test_users);
DELETE FROM annotations WHERE id IN (SELECT id FROM _doomed_anns);
DELETE FROM book_shares
  WHERE shared_with_id IN (SELECT id FROM _test_users)
     OR shared_by_id IN (SELECT id FROM _test_users);
DELETE FROM users WHERE id IN (SELECT id FROM _test_users);
COMMIT;

SELECT 'remaining users' AS check, COUNT(*) FROM users
UNION ALL SELECT 'remaining annotations', COUNT(*) FROM annotations;"

echo
echo "==> Deleting test data..."
echo "$delete_sql" | railway connect Postgres
echo
echo "Done."
