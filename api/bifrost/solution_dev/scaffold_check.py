"""Detect an app whose main.tsx predates the VITE_BIFROST_APP_ID dev fallback."""
from __future__ import annotations

from pathlib import Path

PATCH_HINT = (
    "Your app's src/main.tsx predates `bifrost solution start`. Update two lines so\n"
    "local dev can scope to this install (deployed behavior is unchanged):\n\n"
    "  const appId    = boot?.appId    ?? import.meta.env.VITE_BIFROST_APP_ID  ?? null;\n"
    "  const orgScope = boot?.orgScope ?? import.meta.env.VITE_BIFROST_ORG_ID  ?? null;\n"
)


def main_tsx_needs_dev_fallback(main_tsx: Path) -> bool:
    """True if the file exists but lacks the VITE_BIFROST_APP_ID local fallback."""
    if not main_tsx.is_file():
        return False
    text = main_tsx.read_text(encoding="utf-8")
    return "VITE_BIFROST_APP_ID" not in text


MOUNT_RUNTIME_HINT = (
    "Your app's src/main.tsx uses the legacy side-effect mount contract. It is "
    "supported for migration, but cannot remount in the same document. Update "
    "from a current `bifrost solution scaffold-app` main.tsx: export mount(el, "
    "bootstrap), register it in window.__BIFROST_APP_MODULES__ by import.meta.url, "
    "return root.unmount, and add <meta name=\"bifrost-app-runtime\" "
    "content=\"mount-v1\"> to index.html.\n"
)


def main_tsx_needs_mount_runtime(main_tsx: Path) -> bool:
    """True for an existing v2 entry that lacks the reusable mount registry."""
    if not main_tsx.is_file():
        return False
    return "__BIFROST_APP_MODULES__" not in main_tsx.read_text(encoding="utf-8")


ORG_NULL_HINT = (
    "Your app's vite.config.ts predates the null-orgScope fix: it bakes a "
    'missing org var to "" (which `?? null` never catches), so a global '
    "install sees orgScope \"\" instead of null. Change the define to:\n\n"
    '  "import.meta.env.VITE_BIFROST_ORG_ID": '
    "JSON.stringify(process.env.VITE_BIFROST_ORG_ID || null),\n"
)


def vite_config_needs_org_null(vite_config: Path) -> bool:
    """True if the file exists and still coerces a missing org var to ""."""
    if not vite_config.is_file():
        return False
    return 'VITE_BIFROST_ORG_ID || ""' in vite_config.read_text(encoding="utf-8")
