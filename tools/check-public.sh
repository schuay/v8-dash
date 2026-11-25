#!/bin/sh
# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

# Scans the tracked files and the whole git history for content that must
# not be published. Run before publishing; exits 1 on any finding.
#
#   tools/check-public.sh [--rev REV] [--state FILE]...
#
# History is scanned from REV (default HEAD), the history that would be
# published; pass --rev --all to include every ref.
#
# Checked: e-mail addresses other than example.com placeholders, home and
# user paths, token-like strings, and private identifiers: the lines of
# .git/private-hooks/content-patterns (local, never committed) plus the CL
# numbers and account names found in each --state file (a watcher state).

set -eu

rev=HEAD

cd "$(git rev-parse --show-toplevel)"
self=tools/check-public.sh
patterns=$(mktemp)
trap 'rm -f "$patterns"' EXIT

cat > "$patterns" <<'EOF'
[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)*(com|org|net|io|dev)
/Users/
/home/
~/src/
ya29\.[A-Za-z0-9_-]{10,}
1//0[A-Za-z0-9_-]{10,}
AIza[A-Za-z0-9_-]{30,}
gh[pousr]_[A-Za-z0-9]{30,}
-----BEGIN [A-Z ]*PRIVATE KEY-----
EOF

private=$(git rev-parse --git-common-dir)/private-hooks/content-patterns
if [ -s "$private" ]; then
  grep -Ev '^[[:space:]]*(#|$)' "$private" >> "$patterns"
fi

while [ $# -gt 0 ]; do
  case $1 in
    --rev) rev=${2:?--rev needs a value}; shift 2 ;;
    --state)
      python3 -c '
import json, sys
state = json.load(open(sys.argv[1]))
names = set()
for v in state.values():
    names |= {v.get("owner", "")} | set(v.get("reviewers", []))
    names |= set(v.get("cr_votes", {})) | set(v.get("cq_votes", {}))
for n in sorted(names - {""}):
    print(n)
for cl in sorted(state):
    print(cl)
' "$2" >> "$patterns"
      shift 2 ;;
    *) echo "usage: $0 [--rev REV] [--state FILE]..." >&2; exit 2 ;;
  esac
done

# Placeholder addresses are fine.
allow='@example\.com|@example-ci\.iam\.gserviceaccount\.com|@users\.noreply\.github\.com'

found=0
hits=$(git grep -InE -f "$patterns" -- . ":!$self" | grep -Ev "$allow" || true)
if [ -n "$hits" ]; then
  printf 'tracked files:\n%s\n' "$hits"
  found=1
fi
hits=$(git log "$rev" -p --format='commit %H%n%an <%ae>%n%B' -- . ":!$self" \
  | grep -nE -f "$patterns" | grep -Ev "$allow" || true)
if [ -n "$hits" ]; then
  printf 'history (incl. authors and messages):\n%s\n' "$hits"
  found=1
fi
[ "$found" = 0 ] && echo "clean: no private patterns in files or history"
exit "$found"
