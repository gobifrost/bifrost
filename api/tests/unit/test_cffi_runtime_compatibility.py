"""Exercise native library loading used by CFFI consumers on the image runtime."""

import pytest
from cffi import FFI


def test_cffi_loads_and_calls_native_library():
    ffi = FFI()
    ffi.cdef("size_t strlen(const char *s);")
    library = ffi.dlopen(None)
    assert library.strlen(b"workflow native dependency") == 26


def test_cffi_rejects_embedded_null_library_path():
    with pytest.raises((TypeError, ValueError)):
        FFI().dlopen("libc.so.6\x00unexpected")
