#!/bin/sh
set -eu

# npm 11.21.1 fixes the peer-set resolver crash affecting App SDK rebuilds.
# Integrity from https://registry.npmjs.org/npm/11.21.1
NPM_VERSION="11.21.1"
NPM_TARBALL_SHA512="613ccbe90044257c87713bb426251ced128ca7e9b3949b65eeb18b98445a5d441e14720c73d6ad76be86f4cf42f2f0d66efe347a9a13400ccd2f6fcfb5954385"
tarball="$(mktemp --suffix=.tgz)"

cleanup() {
    rm -f "$tarball"
}
trap cleanup EXIT

curl --fail --location --silent --show-error \
    --proto '=https' \
    --tlsv1.2 \
    --output "$tarball" \
    "https://registry.npmjs.org/npm/-/npm-${NPM_VERSION}.tgz"
printf '%s  %s\n' "$NPM_TARBALL_SHA512" "$tarball" | sha512sum --check --status
npm install --global --prefix /usr/local "$tarball" --no-audit --no-fund
installed_version="$(/usr/local/bin/npm --version)"
if [ "$installed_version" != "$NPM_VERSION" ]; then
    echo "installed npm version $installed_version does not match $NPM_VERSION" >&2
    exit 1
fi
