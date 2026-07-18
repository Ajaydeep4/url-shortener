#!/usr/bin/env bash
# Bump the platform version everywhere it lives, in one command.
#
#   ./scripts/release.sh 1.2.0
#
# Updates: VERSION, each service's pyproject.toml, and turns the
# "[Unreleased]" changelog section into "[1.2.0] - <today>" (leaving a fresh
# empty Unreleased section on top). Then prints the follow-up git commands.
#
# Intended flow: run this on a release PR branch (stage -> main). After the
# PR merges, tag main with v<version>; the tag push triggers the prod deploy.
set -euo pipefail

VERSION="${1:-}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 <semver>   e.g. $0 1.2.0" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TODAY="$(date -u +%Y-%m-%d)"

echo "$VERSION" > "$ROOT/VERSION"

for svc in shortener-service redirect-service analytics-worker; do
  sed -i -E "s/^version = \"[0-9]+\.[0-9]+\.[0-9]+\"$/version = \"$VERSION\"/" \
    "$ROOT/$svc/pyproject.toml"
done

# Promote [Unreleased] to the new version and re-create an empty Unreleased.
sed -i "s/^## \[Unreleased\]$/## [Unreleased]\n\n## [$VERSION] - $TODAY/" \
  "$ROOT/CHANGELOG.md"

echo "Version set to $VERSION in VERSION, pyprojects, and CHANGELOG.md."
echo
echo "Next steps:"
echo "  git checkout -b release/v$VERSION stage"
echo "  git add -A && git commit -m \"chore(release): v$VERSION\""
echo "  # open PR release/v$VERSION -> main; after merge:"
echo "  git tag v$VERSION main && git push origin v$VERSION   # triggers prod deploy"
