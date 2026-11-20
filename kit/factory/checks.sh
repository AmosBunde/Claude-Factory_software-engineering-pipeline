#!/usr/bin/env bash
# The gate. Runs in a job's worktree after every build. Exit 0 means the build may go to review.
#
# Edit this for your project: it is the definition of "the build is not broken".
# The builder runs the same script before handing over, and may not edit it.
set -uo pipefail

fail=0
run() {                      # run "label" command...
  local label=$1; shift
  echo "== $label: $*"
  if "$@"; then echo "-- $label: ok"; else echo "-- $label: FAILED"; fail=1; fi
}
has() { command -v "$1" >/dev/null 2>&1; }

# The branch must contain something, and must not carry conflict markers.
base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||'); base=${base:-main}
if git rev-parse --verify --quiet "$base" >/dev/null && [ -z "$(git diff --name-only "$base"...HEAD)" ]; then
  echo "-- no changes against $base: FAILED"; fail=1
fi
if git grep -nE '^(<<<<<<<|>>>>>>>) ' -- . ':!factory/checks.sh' >/dev/null 2>&1; then
  echo "-- conflict markers found: FAILED"; fail=1
fi

# Node projects: use the scripts the project defines.
if [ -f package.json ] && has npm; then
  [ -d node_modules ] || run "install" npm ci --no-audit --no-fund
  for script in lint typecheck test build; do
    if node -e "process.exit(require('./package.json').scripts?.['$script'] ? 0 : 1)" 2>/dev/null; then
      run "npm $script" npm run --silent "$script"
    fi
  done
fi

# Python projects.
if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f requirements.txt ]; then
  if has uv && [ -f pyproject.toml ]; then py="uv run"; else py=""; fi
  if has ruff || [ -n "$py" ] && $py ruff --version >/dev/null 2>&1; then run "ruff" $py ruff check .; fi
  if [ -d tests ] || ls test_*.py >/dev/null 2>&1; then run "pytest" $py python -m pytest -q; fi
fi

# Go and Rust.
[ -f go.mod ] && has go && { run "go vet" go vet ./...; run "go test" go test ./...; }
[ -f Cargo.toml ] && has cargo && run "cargo test" cargo test --quiet

exit $fail
