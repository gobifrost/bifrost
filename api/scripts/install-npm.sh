#!/bin/sh
set -eu

# npm 10.9.3 integrity from https://registry.npmjs.org/npm/10.9.3
NPM_VERSION="10.9.3"
NPM_TARBALL_SHA512="e84875bb943e908557780f1eee5d9cfc7a67145730ae4b77ef10ccba30f96ded6096859af69ea3dc5b2fde60725d79aa247cbed9c12544c30bf28a4d4fbc4825"
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
