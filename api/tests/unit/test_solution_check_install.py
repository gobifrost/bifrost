from src.services.solutions.dependency_walker import check_install_needs


def test_blocks_on_missing_module():
    # bundle workflow imports modules.helpers but no modules/helpers.py present.
    python_files = {"workflows/w.py": "from modules.helpers import x\n"}
    needs = check_install_needs(python_files)
    assert any(n.kind == "module" and "helpers" in n.ref for n in needs)


def test_passes_when_module_present():
    python_files = {
        "workflows/w.py": "from modules.helpers import x\n",
        "modules/helpers.py": "x = 1\n",
    }
    needs = check_install_needs(python_files)
    assert not [n for n in needs if n.kind == "module"]


def test_passes_when_module_is_package():
    python_files = {
        "workflows/w.py": "from modules.helpers import x\n",
        "modules/helpers/__init__.py": "x = 1\n",
    }
    needs = check_install_needs(python_files)
    assert not [n for n in needs if n.kind == "module"]


def test_missing_module_surfaced_exactly_once():
    # `from modules.missing import x` over-generates modules.missing AND
    # modules.missing.x; a single genuinely-missing module must surface once.
    python_files = {"workflows/w.py": "from modules.missing import x\n"}
    needs = check_install_needs(python_files)
    module_needs = [n for n in needs if n.kind == "module"]
    assert len(module_needs) == 1
    assert module_needs[0].ref == "modules.missing"


def test_no_needs_for_non_modules_imports():
    # stdlib / third-party imports are NOT flagged — only `modules.*`.
    python_files = {"workflows/w.py": "import os\nfrom datetime import datetime\n"}
    needs = check_install_needs(python_files)
    assert needs == []
