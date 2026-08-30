"""Shared v1-to-v2 source migration for Solution and independent Apps."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Literal

import click

MigrationLifecycle = Literal["solution", "app"]


COMBOBOX_WRAPPER = '''\
import { useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export interface ComboboxOption { value: string; label: string; }
export interface ComboboxProps {
  options: ComboboxOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  className?: string;
}

export function Combobox({
  options, value, onValueChange, placeholder = "Select…",
  searchPlaceholder = "Search…", emptyText = "No results.", className,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" aria-expanded={open}
          className={cn("w-full justify-between font-normal", className)}>
          {selected ? selected.label : placeholder}
          <ChevronsUpDown className="opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) p-0">
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((o) => (
                <CommandItem key={o.value} value={o.label}
                  onSelect={() => { onValueChange?.(o.value); setOpen(false); }}>
                  <Check className={cn(value === o.value ? "opacity-100" : "opacity-0")} />
                  {o.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
'''


def migrate_v1_source(
    source: pathlib.Path,
    app_dir: pathlib.Path,
    *,
    title: str,
    lifecycle: MigrationLifecycle,
) -> None:
    """Port and deterministically rewrite a pulled v1 App directory.

    The remaining route, authorization, design, and cutover work is deliberately
    printed as a checklist because those decisions cannot be inferred safely.
    """
    src_dir = source.resolve()
    notes: list[str] = []
    (app_dir / "src" / "pages").mkdir(parents=True, exist_ok=True)
    (app_dir / "src" / "components").mkdir(parents=True, exist_ok=True)

    ported = 0
    source_extensions = {".tsx", ".ts", ".jsx", ".js", ".css", ".json"}
    for sub in ("pages", "components"):
        srcsub = src_dir / sub
        if not srcsub.is_dir():
            continue
        for file in srcsub.rglob("*"):
            if (
                not file.is_file()
                or ".tmp." in file.name
                or file.suffix not in source_extensions
            ):
                continue
            destination = app_dir / "src" / sub / file.relative_to(srcsub)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(file, destination)
            ported += 1

    layout_source = src_dir / "_layout.tsx"
    has_layout = layout_source.is_file()
    if has_layout:
        shutil.copy(layout_source, app_dir / "src" / "_layout.tsx")
        ported += 1

    extras = [
        path.name
        for path in src_dir.iterdir()
        if path.name not in ("pages", "components", "_layout.tsx")
        and not path.name.startswith(".")
    ]
    if extras:
        notes.append(
            "v1 App had non-standard top-level entries not auto-ported: "
            f"{extras} — review by hand."
        )

    from bifrost.migrate_imports import load_lucide_icon_names
    from bifrost.migrate_v2 import (
        compute_shadcn_adds,
        is_ui_source,
        rewrite_v2_imports,
        scan_third_party_deps,
    )

    lucide = frozenset(load_lucide_icon_names())
    tsx_files = [
        path
        for path in sorted((app_dir / "src").rglob("*.tsx"))
        if not is_ui_source(path)
    ]
    sources = {path: path.read_text(encoding="utf-8") for path in tsx_files}
    shadcn_adds = compute_shadcn_adds(list(sources.values()))
    for path, source_text in sources.items():
        rewritten = rewrite_v2_imports(source_text, lucide)
        if rewritten != source_text:
            path.write_text(rewritten, encoding="utf-8")

    all_source = [
        path.read_text(encoding="utf-8")
        for path in (app_dir / "src").rglob("*")
        if path.is_file()
        and path.suffix in {".tsx", ".ts", ".jsx", ".js"}
        and not is_ui_source(path)
    ]
    third_party = scan_third_party_deps(all_source)
    unresolved = [
        path.name for path in sources if "TODO(migrate)" in path.read_text()
    ]
    no_v2_hooks = sorted(
        {
            hook
            for text in (path.read_text() for path in tsx_files)
            for hook in ("useUser", "useAppState", "RequireRole")
            if hook in text
        }
    )

    click.echo("Installing dependencies …")
    subprocess.run(
        ["npm", "install"], cwd=app_dir, check=False, capture_output=True
    )
    if shadcn_adds:
        click.echo(f"shadcn components: {' '.join(shadcn_adds)}")
        subprocess.run(
            ["npx", "shadcn@latest", "add", *shadcn_adds, "--yes"],
            cwd=app_dir,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["npm", "install", "radix-ui", "sonner"],
            cwd=app_dir,
            check=False,
            capture_output=True,
        )
        if "combobox" in shadcn_adds:
            (app_dir / "src" / "components" / "ui" / "combobox.tsx").write_text(
                COMBOBOX_WRAPPER, encoding="utf-8"
            )
    if third_party:
        click.echo(f"third-party deps (direct v1 imports): {' '.join(third_party)}")
        subprocess.run(
            ["npm", "install", *third_party],
            cwd=app_dir,
            check=False,
            capture_output=True,
        )

    click.echo("")
    click.echo(
        f"✓ Ported {ported} file(s), {len(shadcn_adds)} shadcn component(s), "
        f"{len(third_party)} third-party dep(s)."
    )
    click.echo("")
    click.echo("NEXT (human judgment — migrate-app stops here ON PURPOSE):")
    click.echo(
        "  1. Wire src/App.tsx routes from the ported pages. v1 used FILE-BASED "
        "routing;"
    )
    click.echo(
        '     recreate it with react-router: pages/foo.tsx → <Route path="foo">, '
    )
    click.echo(
        '     pages/a/b.tsx → path="a/b", pages/x/[id].tsx → path="x/:id" '
        "(useParams())."
    )
    click.echo(f'     Add <BifrostHeader title="{title}"/> + <Toaster/> at the top.')
    if has_layout:
        click.echo(
            "     src/_layout.tsx is the v1 shared nav chrome — make it the "
            "RootLayout: a"
        )
        click.echo(
            "     parent <Route element={<RootLayout/>}> whose RootLayout renders "
            "the nav +"
        )
        click.echo(
            "     <Outlet/>; nest the section pages under it. "
            "(It already uses <Outlet/>.)"
        )
    if unresolved:
        click.echo(
            f"  2. Resolve TODO(migrate) imports in: {unresolved} "
            "(no auto-mapping found)."
        )
    if no_v2_hooks:
        click.echo(
            f"  3. Port v1-only hooks (NO v2 SDK equivalent): {no_v2_hooks}. "
            "There is no"
        )
        click.echo(
            "     useUser in v2 — use `useBifrostContext()` from \"bifrost\" for "
            "token/org/"
        )
        click.echo(
            "     logout/theme; decode the JWT in ctx.token if you need the "
            "user's email."
        )
    click.echo(
        "  4. Workflow refs: rewrite UUIDs to portable path::function refs and "
        "verify access in every target organization."
    )
    for note in notes:
        click.echo(f"  • {note}")
    if lifecycle == "solution":
        click.echo(
            "  5. `npm run build`, then `bifrost solution start` and screenshot "
            "at least 2 routes."
        )
        click.echo(
            "  6. Cutover: `bifrost solution swap-slugs <old> <new>`, then "
            "`bifrost solution capture` LAST (capture is terminal — deploy after "
            "it wipes captures)."
        )
    else:
        click.echo(
            "  5. `npm run build`, then `bifrost app start` and inspect every "
            "route against the intended live organization."
        )
        click.echo(
            "  6. Deploy with `bifrost app deploy`. The App keeps using live "
            "workflows, tables, files, configs, and integrations; none are captured."
        )
        click.echo(
            "  7. Cut over bookmarks with `bifrost app swap-slugs <old-v1> "
            "<new-v2>` only after the deployed V2 App passes browser acceptance."
        )

    click.echo(f"\nApp at {app_dir}")
