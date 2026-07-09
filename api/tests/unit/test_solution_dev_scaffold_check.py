from pathlib import Path

from bifrost.solution_dev.scaffold_check import main_tsx_needs_dev_fallback


FRESH = '''const appId = boot?.appId ?? import.meta.env.VITE_BIFROST_APP_ID ?? null;'''
STALE = '''const appId = boot?.appId ?? null;'''


def test_fresh_main_tsx_passes(tmp_path: Path):
    p = tmp_path / "src" / "main.tsx"
    p.parent.mkdir(parents=True)
    p.write_text(FRESH)
    assert main_tsx_needs_dev_fallback(p) is False


def test_stale_main_tsx_flagged(tmp_path: Path):
    p = tmp_path / "src" / "main.tsx"
    p.parent.mkdir(parents=True)
    p.write_text(STALE)
    assert main_tsx_needs_dev_fallback(p) is True


def test_missing_file_is_not_flagged(tmp_path: Path):
    assert main_tsx_needs_dev_fallback(tmp_path / "src" / "main.tsx") is False


def test_vite_config_needs_org_null_detects_stale_empty_string(tmp_path):
    from bifrost.solution_dev.scaffold_check import vite_config_needs_org_null

    stale = tmp_path / "vite.config.ts"
    stale.write_text(
        '"import.meta.env.VITE_BIFROST_ORG_ID": '
        'JSON.stringify(process.env.VITE_BIFROST_ORG_ID || ""),'
    )
    assert vite_config_needs_org_null(stale) is True

    fresh = tmp_path / "fresh.config.ts"
    fresh.write_text(
        '"import.meta.env.VITE_BIFROST_ORG_ID": '
        'JSON.stringify(process.env.VITE_BIFROST_ORG_ID || null),'
    )
    assert vite_config_needs_org_null(fresh) is False
    assert vite_config_needs_org_null(tmp_path / "absent.config.ts") is False
