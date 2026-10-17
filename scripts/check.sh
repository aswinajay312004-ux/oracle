#!/usr/bin/env bash
# The same checks CI runs (.github/workflows/ci.yml), so a change can be proven before it is pushed.
#
#   scripts/check.sh                 secret scan, lint, JavaScript syntax and unit tests
#   scripts/check.sh lint            one step: secrets | lint | js | tests | oracle
#   scripts/check.sh tests -k api    anything after the step name goes to pytest
#   scripts/check.sh oracle          the suite with the real-Oracle tests on (needs .env.poppys or POPPYS_ORACLE_*)
#
# PYTHON picks the interpreter (default: .venv/bin/python, else python3). Dev tools: pip install -r requirements-dev.txt
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-}
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; elif command -v python3 >/dev/null; then PY=python3; else PY=python; fi
fi

step() { printf '\n== %s\n' "$1"; }
# A missing tool skips the step on a laptop but fails on CI (GitHub sets CI=true), so CI never passes by skipping.
missing() {
  if [ "${CI:-}" = "true" ]; then echo "FAILED: $1 is required on CI." >&2; return 1; fi
  echo "Skipped: $1 is not installed."
}

# Patterns for committed credentials. Only file:line is printed, never the matched text.
SECRET_PATTERNS=(
  '(ORACLE_PASSWORD|APP_USER_PASSWORD|POPPYS_REPLICA_PASSWORD|POPPYS_ORACLE_PASSWORD)[[:space:]]*[=:][[:space:]]*["'\'']?[A-Za-z0-9]'
  '(^|[^A-Za-z0-9])Pp[0-9a-f]{48}([^0-9a-f]|$)'                     # the format scripts/bootstrap_local.py generates
  '[A-Za-z][A-Za-z0-9_$#]*/[A-Za-z0-9][^[:space:]/@"'\'']*@[A-Za-z0-9_.-]+(:[0-9]+)?/[A-Za-z0-9_.]+'   # user/<password>@host:port/service
  '-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}'
)
# Files that hold local settings, passwords or the local store. .env.example is the only allowed settings file.
SECRET_FILES='(^|/)\.env($|\.)|^data/|\.(pem|key|p12|pfx|jks|sqlite3?|db)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)($|\.)'

secrets() {
  step 'Secret scan: no settings files, local data or passwords in the repository'
  command -v git >/dev/null || { missing git; return; }
  local bad=0 files hits pattern
  # Tracked files plus new files that are not ignored yet, so a leak is caught before it is committed.
  files=$({ git ls-files; git ls-files --others --exclude-standard; } | grep -E "$SECRET_FILES" | grep -vE '(^|/)\.env\.example$' || true)
  if [ -n "$files" ]; then printf '%s\n' "$files" | sed 's/^/  must not be committed: /'; bad=1; fi
  if ! git check-ignore -q .env.poppys; then echo '  .gitignore no longer ignores .env.poppys'; bad=1; fi
  for pattern in "${SECRET_PATTERNS[@]}"; do
    hits=$(git grep --untracked -n -I -E -e "$pattern" -- . | cut -d: -f1,2 || true)
    if [ -n "$hits" ]; then printf '%s\n' "$hits" | sed 's/^/  possible password or key at /'; bad=1; fi
  done
  if [ "$bad" = 1 ]; then
    echo 'FAILED: remove the file or value above (passwords belong in .env.poppys, which git ignores).' >&2; return 1
  fi
  echo 'OK: nothing secret is committed.'
}

lint() {
  step 'Lint: ruff (rules in pyproject.toml)'
  "$PY" -m ruff --version >/dev/null 2>&1 || { missing 'ruff (pip install -r requirements-dev.txt)'; return; }
  "$PY" -m ruff check .
}

js() {
  step 'JavaScript: syntax of frontend/js/*.js and the strict content-security rules'
  command -v node >/dev/null || { missing node; return; }
  local tmp f bad=0 modules=(frontend/js/*.js)
  tmp=$(mktemp -d)
  # The files are ES modules; node only parses them as modules under an .mjs name.
  for f in "${modules[@]}"; do
    cp "$f" "$tmp/$(basename "${f%.js}").mjs"
    if ! node --check "$tmp/$(basename "${f%.js}").mjs" 2>"$tmp/err"; then
      echo "  $f:"; sed "s#$tmp/[^:]*\.mjs#$f#g" "$tmp/err"; bad=1
    fi
  done
  rm -rf "$tmp"
  # The page runs under script-src 'self': no inline scripts or on*= handlers, no scripts or styles from other sites.
  if grep -nE '<script(\s[^>]*)?>' frontend/index.html | grep -v 'src=' ; then echo '  ^ inline <script> in index.html'; bad=1; fi
  if grep -nE '<[a-zA-Z][^>]*\son[a-z]+\s*=' frontend/index.html frontend/js/*.js; then echo '  ^ inline on*= handler (use addEventListener)'; bad=1; fi
  if grep -nE '(src|href)\s*=\s*["'\'']?(https?:)?//' frontend/index.html frontend/js/*.js; then echo '  ^ resource from another site (the app is offline)'; bad=1; fi
  if grep -nE '\beval\s*\(|new Function\s*\(|set(Timeout|Interval)\s*\(\s*["'\''`]' frontend/js/*.js; then echo '  ^ code built from strings'; bad=1; fi
  if [ "$bad" = 1 ]; then echo 'FAILED: fix the JavaScript above.' >&2; return 1; fi
  echo "OK: ${#modules[@]} modules parse and follow the content-security rules."
}

tests() {
  step 'Unit tests: pytest (stand-in Oracle and SQLite, no database needed)'
  "$PY" -m pytest -q "$@"
}

oracle() {
  # The whole suite with the opt-in switch on, so every real-Oracle test (test_oracle, test_oracle_insight,
  # acceptance E/F, large-table checks, and any added later) runs. It writes only TEST-labelled orders.
  step 'Real-Oracle tests: the whole suite with POPPYS_TEST_ORACLE=1'
  POPPYS_TEST_ORACLE=1 "$PY" -m pytest -q "$@"
}

case "${1:-all}" in
  all)  # run every step, then report, so one run shows every problem
    failed=()
    for s in secrets lint js tests; do "$s" || failed+=("$s"); done
    if [ ${#failed[@]} -gt 0 ]; then printf '\nFAILED: %s\n' "${failed[*]}" >&2; exit 1; fi
    printf '\nAll checks passed.\n' ;;
  secrets|lint|js) "$1" ;;
  tests|oracle) name=$1; shift; "$name" "$@" ;;
  *) echo "Unknown step '$1'. Use: secrets, lint, js, tests or oracle." >&2; exit 2 ;;
esac
