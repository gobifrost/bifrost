#!/usr/bin/env bash
# Run CodeQL locally against the working tree — the same security-and-quality
# suite GitHub's hosted CodeQL Action uses, with the repo's codeql-config.yml
# query filters applied. Lets you iterate on alerts without push→CI round-trips.
#
# Requires: gh extension install github/codeql-action  (provides `gh codeql`),
# or the standalone `codeql` CLI on PATH.
#
# Usage:
#   scripts/codeql-local.sh                       # full python suite, all results
#   scripts/codeql-local.sh py/path-injection     # one query id, just its results
#   scripts/codeql-local.sh --rebuild             # force-rebuild the DB first
#   scripts/codeql-local.sh --lang javascript     # analyze JS/TS instead of python
#   scripts/codeql-local.sh --rebuild --fail-on-alerts  # PR preflight gate
#   scripts/codeql-local.sh --changed-only --fail-on-alerts  # new PR lines only
#
# The DB is cached per-worktree at ~/.cache/codeql-db/<hash>-<lang> and only
# rebuilt when you pass --rebuild or the cache is missing (build ~1-2 min;
# analyze ~30-90s). Pass a query id to filter to a single rule for fast loops.
set -euo pipefail

lang="python"
rebuild=0
fail_on_alerts=0
changed_only=0
query=""
for arg in "$@"; do
  case "$arg" in
    --rebuild) rebuild=1 ;;
    --fail-on-alerts) fail_on_alerts=1 ;;
    --changed-only) changed_only=1 ;;
    --lang) ;;                          # handled below
    python|javascript|javascript-typescript) lang="$arg" ;;
    --lang=*) lang="${arg#--lang=}" ;;
    py/*|js/*) query="$arg" ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done
[ "$lang" = "javascript" ] && lang="javascript-typescript"

# Resolve the codeql command (standalone CLI or the gh extension).
if command -v codeql >/dev/null 2>&1; then
  codeql_cmd=(codeql)
elif gh extension list 2>/dev/null | grep -q gh-codeql; then
  codeql_cmd=(gh codeql)
else
  echo "ERROR: no codeql CLI. Install: gh extension install github/codeql-action" >&2
  exit 1
fi

repo_root=$(git rev-parse --show-toplevel)
repo_key=$(echo "$repo_root" | sha1sum | cut -c1-12)
db_path="$HOME/.cache/codeql-db/${repo_key}-${lang}"
mkdir -p "$(dirname "$db_path")"
config="$repo_root/.github/codeql/codeql-config.yml"

lang_for_db="$lang"
[ "$lang" = "javascript-typescript" ] && lang_for_db="javascript"
if [ "$lang_for_db" = "javascript" ]; then
  query_pack="codeql/javascript-queries@2.2.0"
else
  query_pack="codeql/python-queries@1.8.2"
fi
query_suite="$query_pack:codeql-suites/${lang_for_db}-security-and-quality.qls"
if ! "${codeql_cmd[@]}" resolve queries "$query_suite" >/dev/null 2>&1; then
  "${codeql_cmd[@]}" pack download "$query_pack" >&2
fi

if [ "$rebuild" = 1 ] || [ ! -f "$db_path/codeql-database.yml" ]; then
  echo ">> building CodeQL DB ($lang) — ~1-2 min" >&2
  build_log=$(mktemp -t codeql-build-XXXXXX.log)
  if ! "${codeql_cmd[@]}" database create "$db_path" \
      --language="$lang_for_db" --source-root="$repo_root" --overwrite --quiet \
      >"$build_log" 2>&1; then
    tail -80 "$build_log" >&2
    rm -f "$build_log"
    exit 1
  fi
  rm -f "$build_log"
fi

sarif=$(mktemp -t codeql-local-XXXXXX.sarif)
results=$(mktemp -t codeql-local-results-XXXXXX.txt)
changed=$(mktemp -t codeql-changed-lines-XXXXXX.json)
trap 'rm -f "$sarif" "$results" "$changed"' EXIT
if [ "$changed_only" = 1 ]; then
  python3 "$repo_root/scripts/codeql_changed_lines.py" > "$changed"
else
  printf '{}\n' > "$changed"
fi

# Always run the full suite (the per-query path-resolve is brittle across pack
# versions); when a query id is given we just filter the SARIF to it below. The
# suite is cached-DB-fast, so this costs nothing extra on repeat runs.
echo ">> analyzing full $lang security-and-quality suite${query:+ (filtering to $query)}" >&2
"${codeql_cmd[@]}" database analyze "$db_path" \
  "$query_suite" \
  --format=sarif-latest --output="$sarif" --threads=0 --quiet

# Apply the repo's query-filters (exclude:) so local output matches CI, which
# passes config-file: codeql-config.yml. We post-filter by excluded rule ids.
excluded=$(awk '/^[[:space:]]*id:/{print $2}' "$config" 2>/dev/null | tr '\n' '|' | sed 's/|$//')

echo ""
echo "== CodeQL results (config-filtered) =="
jq -r --arg ex "$excluded" --arg q "$query" --argjson changed_only "$changed_only" --slurpfile changed "$changed" '
  ($ex | split("|")) as $excl
  | .runs[].results[]
  | select((.ruleId // "") as $r | ($excl | index($r)) | not)
  | select($q == "" or .ruleId == $q)
  | .locations[0].physicalLocation as $loc
  | select(($loc.artifactLocation.uri as $path |
      ($path | startswith("api/alembic/versions/") or
               startswith("api/src/services/templates/") or
               startswith("client/playwright-report/") or
               startswith("client/dist/") or
               startswith("client/node_modules/") or
               startswith("client/e2e/") or
               startswith("scripts/docs/") or
               contains("/__pycache__/") or
               contains("/.venv/"))) | not)
  | select($changed_only == 0 or
      ($loc.region.startLine as $line |
       ($changed[0][$loc.artifactLocation.uri] // []) |
       any(.[]; .[0] <= $line and $line <= .[1])))
  | "\(.level // "note")\t\(.ruleId)\t\(.locations[0].physicalLocation.artifactLocation.uri):\(.locations[0].physicalLocation.region.startLine)"
' "$sarif" | sort | tee "$results"

echo ""
echo "== counts by rule =="
cut -f2 "$results" | sort | uniq -c | sort -rn
echo ""
echo "total (after config excludes): $(wc -l < "$results")"
if [ "$fail_on_alerts" = 1 ] && [ -s "$results" ]; then
  echo "ERROR: CodeQL found alerts. Resolve them before opening a PR." >&2
  exit 1
fi
