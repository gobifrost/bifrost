#!/bin/sh
# Backport https://github.com/python/cpython/pull/160574 at e62d5c61911f5e6b005babfd348e535550d93893.
# Remove this repair when the pinned interpreter includes the upstream fix.
set -eu
python -c 'import sys; assert sys.version_info[:5] == (3, 16, 0, "alpha", 2)'
repair_dir=$(mktemp -d)
trap 'rm -rf "$repair_dir"' EXIT HUP INT TERM
cd "$repair_dir"
curl -fsSL https://www.python.org/ftp/python/3.16.0/Python-3.16.0a2.tar.xz -o python.tar.xz
echo '5fb6a0b8b7a70af2ee1386edcfb832e9636a5331b2bf0376a06cac33c43aa0d0  python.tar.xz' | sha256sum -c -
tar -xf python.tar.xz --strip-components=1
python - <<'PY'
from pathlib import Path
path = Path('Modules/_uuidmodule.c')
source = path.read_text()
old = 'if (PyDict_SetItem(dict, &_Py_ID(is_safe), self->is_safe) < 0) {'
assert source.count(old) == 1, 'UUID repair no longer matches pinned source'
path.write_text(source.replace(old, 'if (self->is_safe != NULL && PyDict_SetItem(dict, &_Py_ID(is_safe), self->is_safe) < 0) {'))
PY
./configure --enable-shared --with-ensurepip
# Compile against the exact installed interpreter's feature/ABI configuration.
cp /usr/local/include/python3.16/pyconfig.h ./pyconfig.h
suffix=$(python3-config --extension-suffix)
make -j2 "Modules/_uuid$suffix"
cp "Modules/_uuid$suffix" /usr/local/lib/python3.16/lib-dynload/
