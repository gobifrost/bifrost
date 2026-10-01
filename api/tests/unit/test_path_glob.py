import pytest

from shared.path_glob import compile_glob, glob_literal_prefix, glob_matches

CASES = [
    ("workflows/*.py", "workflows/top.py", True),
    ("workflows/*.py", "workflows/sub/nested.py", False),
    ("workflows/**/*.py", "workflows/sub/nested.py", True),
    ("workflows/**/*.py", "workflows/top.py", True),
    ("**/foo.py", "foo.py", True),
    ("**/foo.py", "a/b/foo.py", True),
    ("*.py", "a/b/c.py", True),            # no slash -> any depth
    ("/*.py", "a/c.py", False),            # leading slash anchors to root
    ("/*.py", "c.py", True),
    ("my_x.txt", "myAx.txt", False),       # '_' is literal
    ("*.{py,json}", "data/c.json", True),
    ("*.{py,json}", "a.ts", False),
    ("file?.txt", "file1.txt", True),
    ("file?.txt", "file/.txt", False),
    ("[ab].py", "a.py", True),
    ("[!ab].py", "a.py", False),
    ("modules", "modules/x/y.py", True),   # directory pattern covers contents
    ("modules/", "modules/x.py", True),
    ("WORKFLOWS/*.py", "workflows/top.py", False),  # case-sensitive like rg
    ("**", "anything/at/all", True),
]


@pytest.mark.parametrize("pattern,path,expected", CASES)
def test_glob_semantics(pattern, path, expected):
    assert glob_matches(compile_glob(pattern), path) is expected


@pytest.mark.parametrize(
    "pattern,prefix",
    [("workflows/*.py", "workflows/"), ("/apps/x/**", "apps/x/"), ("*.py", ""), ("**/foo.py", ""), ("a_b/*", "a_b/")],
)
def test_literal_prefix(pattern, prefix):
    assert glob_literal_prefix(pattern) == prefix


def test_unbalanced_brace_is_value_error():
    with pytest.raises(ValueError):
        compile_glob("*.{py,json")
