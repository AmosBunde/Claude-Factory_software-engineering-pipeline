#!/usr/bin/env bash
# Install the software factory into a git repository.
#
#   ./install.sh /path/to/your/repo
#
# Copies factory/ and the Claude Code agents and /factory skill into the target. Existing
# files are never overwritten: anything already there is left alone and listed.
set -euo pipefail

target=${1:-}
[ -n "$target" ] || { echo "usage: $0 /path/to/your/repo" >&2; exit 2; }
target=$(cd "$target" && pwd)
git -C "$target" rev-parse --show-toplevel >/dev/null 2>&1 || { echo "$target is not a git repository" >&2; exit 2; }
kit=$(cd "$(dirname "$0")/kit" && pwd)

copied=0; skipped=0
while IFS= read -r -d '' file; do
  rel=${file#"$kit"/}
  if [ -e "$target/$rel" ]; then
    echo "  kept     $rel (already exists)"; skipped=$((skipped + 1))
  else
    mkdir -p "$(dirname "$target/$rel")"
    cp -p "$file" "$target/$rel"
    echo "  added    $rel"; copied=$((copied + 1))
  fi
done < <(find "$kit" -type f -print0 | sort -z)

grep -qxF '.factory-worktrees/' "$target/.gitignore" 2>/dev/null || echo '.factory-worktrees/' >> "$target/.gitignore"

echo
echo "$copied file(s) added, $skipped kept."
echo "Next:"
echo "  1. Fill in factory/FACTORY.md and check factory/checks.sh runs your tests."
echo "  2. Merge factory/settings.example.json into .claude/settings.json if you want fewer permission prompts."
echo "  3. gh label create factory --description 'Build this with the software factory'"
echo "  4. Commit, then in Claude Code run:  /factory run"
