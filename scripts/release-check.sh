#!/bin/bash
# Pre-tag safety checks before creating a release tag.
# Usage: ./scripts/release-check.sh v2.1.0 --release-body product-updates/release-bodies/v2.1.0.md
# Run this BEFORE: git tag v2.1.0 && git push origin v2.1.0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

TAG="${1:-}"
RELEASE_BODY=""

if [[ $# -ge 3 && "$2" == "--release-body" ]]; then
    RELEASE_BODY="$3"
elif [[ $# -ne 1 ]]; then
    echo -e "${RED}Usage: $0 <tag> --release-body <prepared-body.md> (e.g., $0 v2.1.0 --release-body product-updates/release-bodies/v2.1.0.md)${NC}"
    exit 1
fi

if [ -z "$TAG" ]; then
    echo -e "${RED}Usage: $0 <tag> (e.g., $0 v2.1.0)${NC}"
    exit 1
fi

if [[ "$TAG" != v* ]]; then
    echo -e "${RED}Tag must start with 'v' (got: $TAG)${NC}"
    exit 1
fi

# A final release body is reviewed and rendered before the tag exists. CI
# publishes that exact file; it must never have to invent or rewrite notes
# after release artifacts become visible.
if [[ -z "$RELEASE_BODY" || ! -s "$RELEASE_BODY" ]]; then
    echo -e "${RED}Prepared release body is required and must be a non-empty file: use --release-body <prepared-body.md>.${NC}"
    exit 1
fi

if ! git ls-files --error-unmatch "$RELEASE_BODY" >/dev/null 2>&1; then
    echo -e "${RED}Prepared release body must be tracked in git: $RELEASE_BODY${NC}"
    exit 1
fi

# Applicability is frozen in the verified inventory. The prepared body is
# committed afterward, so using HEAD here would create a self-reference rather
# than validate the reviewed release range.
if ! RELEASE_MARKER="$(head -n 1 "$RELEASE_BODY")"; then
    echo -e "${RED}Prepared release body cannot be read: $RELEASE_BODY${NC}"
    exit 1
fi
if [[ ! "$RELEASE_MARKER" =~ ^\<\!--\ product-updates:\ target=([0-9a-f]{40})\ content-ref=([0-9a-f]{40})\ --\>$ ]]; then
    echo -e "${RED}Prepared release body has no deterministic Product Updates marker.${NC}"
    exit 1
fi
TARGET_SHA="${BASH_REMATCH[1]}"
CONTENT_SHA="${BASH_REMATCH[2]}"
if [[ "$(jq -r .target_ref product-updates/inventory.json)" != "$TARGET_SHA" ]]; then
    echo -e "${RED}Prepared release body target does not match the frozen inventory target_ref.${NC}"
    exit 1
fi
RENDERED_BODY="$(mktemp)"
trap 'rm -f "$RENDERED_BODY"' EXIT
if ! ./scripts/prepare-release-body.sh "$TAG" "$TARGET_SHA" "$CONTENT_SHA" "$RENDERED_BODY"; then
    echo -e "${RED}Prepared release body could not be regenerated from approved Product Updates.${NC}"
    exit 1
fi
if ! cmp -s "$RELEASE_BODY" "$RENDERED_BODY"; then
    echo -e "${RED}Prepared release body is stale. Regenerate and review it before tagging: $RELEASE_BODY${NC}"
    exit 1
fi

FAIL=0

echo "Running release checks for $TAG..."
echo ""

# 1. Clean working tree
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}✗ Working tree is dirty. Commit or stash changes first.${NC}"
    git status --short
    FAIL=1
else
    echo -e "${GREEN}✓ Working tree is clean${NC}"
fi

# 2. Tag does not already exist locally
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo -e "${RED}✗ Tag $TAG already exists locally. Use a different version.${NC}"
    FAIL=1
else
    echo -e "${GREEN}✓ Tag $TAG does not exist locally${NC}"
fi

# 3. Tag does not already exist on remote
if git ls-remote --tags origin "$TAG" | grep -q "$TAG"; then
    echo -e "${RED}✗ Tag $TAG already exists on remote. Use a different version.${NC}"
    FAIL=1
else
    echo -e "${GREEN}✓ Tag $TAG does not exist on remote${NC}"
fi

# 4. Current HEAD is on main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo -e "${YELLOW}⚠ Not on main branch (on: $CURRENT_BRANCH). Proceed with caution.${NC}"
else
    echo -e "${GREEN}✓ On main branch${NC}"
fi

# 5. Unit tests pass
echo ""
echo -e "${YELLOW}Running unit tests...${NC}"
# Stack must be up for the verb-style test.sh; boot it if it isn't (caller may
# have one running already, in which case stack up is a no-op).
./test.sh stack up >/dev/null
if ./test.sh unit; then
    echo -e "${GREEN}✓ Unit tests passed${NC}"
else
    echo -e "${RED}✗ Unit tests failed. Fix before tagging.${NC}"
    FAIL=1
fi

echo ""
if [ $FAIL -ne 0 ]; then
    echo -e "${RED}Release checks FAILED. Fix the issues above before tagging.${NC}"
    exit 1
fi

echo -e "${GREEN}All release checks passed!${NC}"
echo ""
echo "To create the release:"
echo "  git tag $TAG && git push origin $TAG"
