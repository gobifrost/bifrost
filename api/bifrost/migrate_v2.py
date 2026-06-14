"""Deterministic v1 → v2 import migration for ``standalone_v2`` apps.

A v1 inline app imports EVERYTHING from one bare ``"bifrost"`` module (the
platform runtime-injects ~40 shadcn UI components + React + react-router + utils
+ the hooks). The v2 ``bifrost`` SDK exposes ONLY the hooks/provider/header, so a
migrated app must split that single import line by ORIGIN:

  - shadcn UI component  -> ``@/components/ui/<component>``  (the app's own
    ``shadcn add``'d copies; also yields the deterministic add-list)
  - React name           -> ``react``
  - react-router name    -> ``react-router-dom``
  - ``cn``/format utils  -> ``@/lib/utils``
  - ``toast``            -> ``sonner``
  - hooks/SDK/tables     -> STAY in ``bifrost``
  - anything else        -> a Lucide icon -> ``lucide-react``

The classification is DETERMINISTIC: the v1 surface is the fixed
``PLATFORM_EXPORT_NAMES`` source of truth, so every name has a known v2 home.
``compute_shadcn_adds`` returns exactly the components an app needs (feed it to
``npx shadcn add``); ``rewrite_v2_imports`` rewrites the import lines.

Files under ``components/ui/`` are NEVER rewritten — they are real shadcn source
and rewriting them would corrupt the components (they legitimately import from
``radix-ui``/``lucide-react``/relative paths, not ``bifrost``).
"""
from __future__ import annotations

import pathlib
import re

from bifrost.platform_names import PLATFORM_EXPORT_NAMES

# React runtime names (subset of the platform surface) → "react".
_REACT = frozenset({
    "React", "Fragment", "Suspense", "lazy", "memo", "forwardRef",
    "useState", "useEffect", "useCallback", "useMemo", "useRef",
    "useContext", "useReducer", "useLayoutEffect", "useId",
    "useTransition", "useDeferredValue", "useImperativeHandle",
})

# react-router-dom names. In v2 there is no platform basename wrapper, so the
# previously-"wrapped" five (Link/NavLink/Navigate/useNavigate/navigate) ALSO
# move to react-router-dom (the app owns its <BrowserRouter basename> via the
# scaffold). `navigate` is not a real RR export → maps to useNavigate usage, but
# as an import it's dropped (apps call useNavigate()).
_ROUTER = frozenset({
    "Outlet", "Link", "NavLink", "Navigate", "useNavigate",
    "useLocation", "useParams", "useSearchParams", "useOutletContext",
    "BrowserRouter", "HashRouter", "MemoryRouter", "Router", "RouterProvider",
    "Routes", "Route", "Form", "Await", "ScrollRestoration",
    "useHref", "useLinkClickHandler", "useInRouterContext",
    "useMatch", "useNavigationType", "useOutlet", "useResolvedPath",
    "useRoutes", "useBeforeUnload", "useFetcher", "useFetchers",
    "useLoaderData", "useNavigation", "useRevalidator", "useRouteError",
    "useRouteLoaderData", "useSubmit", "useBlocker", "unstable_usePrompt",
    "useActionData", "useAsyncError", "useAsyncValue",
    "createBrowserRouter", "createHashRouter", "createMemoryRouter",
    "createRoutesFromChildren", "createRoutesFromElements",
    "createSearchParams", "generatePath", "matchPath", "matchRoutes",
    "renderMatches", "resolvePath",
    # `navigate` (bare) is platform-only sugar — _classify drops it (apps call
    # useNavigate()); listed here so the completeness guard sees it as router.
    "navigate",
    # UNSAFE_* internals are react-router-dom re-exports.
    "UNSAFE_DataRouterContext", "UNSAFE_DataRouterStateContext",
    "UNSAFE_NavigationContext", "UNSAFE_LocationContext", "UNSAFE_RouteContext",
})

# Stay in the v2 "bifrost" SDK (hooks + tables + provider/header).
_BIFROST_KEEP = frozenset({
    "useWorkflow", "useWorkflowQuery", "useWorkflowMutation",
    "useTable", "useInfiniteTable", "tables",
    "BifrostProvider", "BifrostHeader", "useBifrostContext",
    # v1 platform hooks with no v2 SDK equivalent yet — keep so the build fails
    # loudly (a real "port this" signal) rather than silently mis-routing.
    "useUser", "useAppState", "RequireRole",
})

# cn / formatters → @/lib/utils (the scaffold ships cn there; format helpers are
# vendored alongside by the author if used).
_UTILS = frozenset({
    "cn", "clsx", "twMerge", "format",
    "formatDate", "formatDateShort", "formatTime", "formatRelativeTime",
    "formatBytes", "formatNumber", "formatCost", "formatDuration",
    "parseBackendDate",
})

_TOAST = frozenset({"toast"})

# JS globals re-asserted in the v1 `$` registry (Map/Set/Date…) — they were only
# there to win over Lucide icons of the same name. In a real app they're globals,
# so a v1 `import { Set } from "bifrost"` is dropped (Set is a JS global).
_JS_GLOBALS = frozenset({"Map", "Set", "WeakMap", "WeakSet", "Date"})

# shadcn UI symbol → its component file (kebab) under components/ui/. Derived
# from the platform UI surface; grouped exports share one file. A symbol in
# PLATFORM_EXPORT_NAMES that is NOT in react/router/utils/toast/keep/globals is
# a UI component and MUST appear here (a drift test enforces completeness).
_UI_COMPONENT_FILES: dict[str, str] = {}


def _reg(component: str, *symbols: str) -> None:
    for s in symbols:
        _UI_COMPONENT_FILES[s] = component


_reg("button", "Button", "buttonVariants")
_reg("input", "Input")
_reg("label", "Label")
_reg("textarea", "Textarea")
_reg("checkbox", "Checkbox")
_reg("switch", "Switch")
_reg("select", "Select", "SelectContent", "SelectGroup", "SelectItem",
     "SelectLabel", "SelectTrigger", "SelectValue", "SelectSeparator",
     "SelectScrollUpButton", "SelectScrollDownButton")
_reg("radio-group", "RadioGroup", "RadioGroupItem")
_reg("combobox", "Combobox", "MultiCombobox")
_reg("tags-input", "TagsInput")
_reg("slider", "Slider")
_reg("card", "Card", "CardHeader", "CardFooter", "CardTitle", "CardAction",
     "CardDescription", "CardContent")
_reg("badge", "Badge", "badgeVariants")
_reg("avatar", "Avatar", "AvatarImage", "AvatarFallback")
_reg("alert", "Alert", "AlertTitle", "AlertDescription", "AlertAction")
_reg("skeleton", "Skeleton")
_reg("progress", "Progress")
_reg("tabs", "Tabs", "TabsList", "TabsTrigger", "TabsContent", "tabsListVariants")
_reg("dialog", "Dialog", "DialogClose", "DialogContent", "DialogDescription",
     "DialogFooter", "DialogHeader", "DialogTitle", "DialogTrigger",
     "DialogOverlay", "DialogPortal")
_reg("alert-dialog", "AlertDialog", "AlertDialogTrigger", "AlertDialogContent",
     "AlertDialogMedia", "AlertDialogHeader", "AlertDialogFooter",
     "AlertDialogTitle", "AlertDialogDescription", "AlertDialogAction",
     "AlertDialogCancel", "AlertDialogOverlay", "AlertDialogPortal")
_reg("tooltip", "Tooltip", "TooltipContent", "TooltipProvider", "TooltipTrigger")
_reg("popover", "Popover", "PopoverContent", "PopoverTrigger", "PopoverAnchor",
     "PopoverHeader", "PopoverTitle", "PopoverDescription")
_reg("sheet", "Sheet", "SheetClose", "SheetContent", "SheetDescription",
     "SheetFooter", "SheetHeader", "SheetTitle", "SheetTrigger")
_reg("command", "Command", "CommandDialog", "CommandEmpty", "CommandGroup",
     "CommandInput", "CommandItem", "CommandList", "CommandSeparator",
     "CommandShortcut")
_reg("context-menu", "ContextMenu", "ContextMenuTrigger", "ContextMenuContent",
     "ContextMenuItem", "ContextMenuCheckboxItem", "ContextMenuRadioItem",
     "ContextMenuLabel", "ContextMenuSeparator", "ContextMenuShortcut",
     "ContextMenuGroup", "ContextMenuPortal", "ContextMenuSub",
     "ContextMenuSubContent", "ContextMenuSubTrigger", "ContextMenuRadioGroup")
_reg("hover-card", "HoverCard", "HoverCardTrigger", "HoverCardContent")
_reg("pagination", "Pagination", "PaginationContent", "PaginationItem",
     "PaginationLink", "PaginationNext", "PaginationPrevious",
     "PaginationEllipsis")
_reg("table", "Table", "TableHeader", "TableBody", "TableFooter",
     "TableHead", "TableRow", "TableCell", "TableCaption")
_reg("accordion", "Accordion", "AccordionContent", "AccordionItem",
     "AccordionTrigger")
_reg("collapsible", "Collapsible", "CollapsibleContent", "CollapsibleTrigger")
_reg("toggle", "Toggle", "toggleVariants")
_reg("toggle-group", "ToggleGroup", "ToggleGroupItem")
_reg("separator", "Separator")
_reg("dropdown-menu", "DropdownMenu", "DropdownMenuContent", "DropdownMenuItem",
     "DropdownMenuLabel", "DropdownMenuSeparator", "DropdownMenuTrigger",
     "DropdownMenuGroup", "DropdownMenuPortal", "DropdownMenuCheckboxItem",
     "DropdownMenuRadioGroup", "DropdownMenuRadioItem", "DropdownMenuShortcut",
     "DropdownMenuSub", "DropdownMenuSubContent", "DropdownMenuSubTrigger")
_reg("calendar", "Calendar", "CalendarPicker", "CalendarDayButton")
_reg("date-range-picker", "DateRangePicker")

# Components shadcn ships as a composed RECIPE (not a single `add`): they need
# their primitives added + a small vendored wrapper. The skill vendors these.
RECIPE_COMPONENTS: dict[str, tuple[str, ...]] = {
    "combobox": ("popover", "command"),
    "multi-combobox": ("popover", "command"),
}

_IMPORT_RE = re.compile(r'import\s*\{([^}]*)\}\s*from\s*["\']bifrost["\'];?')


def _classify(symbol: str, lucide_names: frozenset[str]) -> tuple[str, str | None]:
    """Return (target, detail). target ∈ {react, router, bifrost, utils, toast,
    ui, lucide, drop}. detail = component file for ui, else None."""
    name = symbol.split(" as ")[0].strip()
    if name in _BIFROST_KEEP:
        return ("bifrost", None)
    if name in _REACT:
        return ("react", None)
    if name in _ROUTER:
        # `navigate` isn't a real RR export — drop it (apps use useNavigate()).
        return ("drop", None) if name == "navigate" else ("router", None)
    if name in _UTILS:
        return ("utils", None)
    if name in _TOAST:
        return ("toast", None)
    if name in _JS_GLOBALS:
        return ("drop", None)
    if name in _UI_COMPONENT_FILES:
        return ("ui", _UI_COMPONENT_FILES[name])
    if name in lucide_names:
        return ("lucide", None)
    # Unknown: not in the platform surface and not a known lucide icon. Leave it
    # in bifrost with a marker so it surfaces (don't silently drop).
    return ("unknown", None)


def compute_shadcn_adds(sources: list[str]) -> list[str]:
    """The deterministic ``shadcn add`` list for a set of source files: every
    shadcn component any source imports from ``bifrost``. Recipe components
    (combobox) expand to their primitives too."""
    needed: set[str] = set()
    for src in sources:
        for m in _IMPORT_RE.finditer(src):
            for raw in m.group(1).split(","):
                comp = _UI_COMPONENT_FILES.get(raw.split(" as ")[0].strip())
                if comp:
                    needed.add(comp)
                    needed.update(RECIPE_COMPONENTS.get(comp, ()))
    return sorted(needed)


def rewrite_v2_imports(source: str, lucide_names: frozenset[str]) -> str:
    """Rewrite the ``from "bifrost"`` import(s) in one source file by origin."""
    m = _IMPORT_RE.search(source)
    if not m:
        return source
    buckets: dict[str, list[str]] = {
        "react": [], "router": [], "bifrost": [], "utils": [], "toast": [],
        "lucide": [], "unknown": [],
    }
    ui_by_file: dict[str, list[str]] = {}
    for raw in (s.strip() for s in m.group(1).split(",")):
        if not raw:
            continue
        target, detail = _classify(raw, lucide_names)
        if target == "drop":
            continue
        if target == "ui":
            ui_by_file.setdefault(detail or "", []).append(raw)  # type: ignore[arg-type]
        else:
            buckets[target].append(raw)

    lines: list[str] = []
    if buckets["react"]:
        lines.append(f'import {{ {", ".join(sorted(buckets["react"]))} }} from "react";')
    if buckets["router"]:
        lines.append(f'import {{ {", ".join(sorted(buckets["router"]))} }} from "react-router-dom";')
    if buckets["lucide"]:
        lines.append(f'import {{ {", ".join(sorted(buckets["lucide"]))} }} from "lucide-react";')
    if buckets["toast"]:
        lines.append('import { toast } from "sonner";')
    if buckets["bifrost"]:
        lines.append(f'import {{ {", ".join(sorted(buckets["bifrost"]))} }} from "bifrost";')
    for comp in sorted(ui_by_file):
        lines.append(f'import {{ {", ".join(sorted(ui_by_file[comp]))} }} from "@/components/ui/{comp}";')
    if buckets["utils"]:
        lines.append(f'import {{ {", ".join(sorted(buckets["utils"]))} }} from "@/lib/utils";')
    if buckets["unknown"]:
        lines.append(
            f'import {{ {", ".join(sorted(buckets["unknown"]))} }} from "bifrost"; '
            f'// TODO(migrate): unresolved v1 import — port by hand'
        )

    return source[:m.start()] + "\n".join(lines) + source[m.end():]


def is_ui_source(path: pathlib.Path) -> bool:
    """True for real shadcn source under components/ui — NEVER rewrite these."""
    return "components/ui" in path.as_posix()


# Bare module specifiers already provided by the scaffold / SDK / shadcn — NOT
# extra npm installs. Everything else a v1 page imports directly (e.g. recharts,
# date-fns) is a third-party dep migrate-app must `npm install` or the build
# breaks (the v2 rewrite only handles `from "bifrost"`).
_PROVIDED_PACKAGES = frozenset({
    "bifrost", "react", "react-dom", "react-router-dom", "lucide-react",
    "sonner", "clsx", "tailwind-merge", "class-variance-authority", "radix-ui",
})
_BARE_IMPORT_RE = re.compile(
    r'(?:import[^;]*?from|export[^;]*?from|import)\s*["\']([^."\'/][^"\']*)["\']'
)


def _package_root(spec: str) -> str:
    """Top-level npm package name from an import specifier (handles @scope/pkg)."""
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def scan_third_party_deps(sources: list[str]) -> list[str]:
    """Third-party npm packages a set of source files import DIRECTLY (not from
    ``bifrost``, not relative, not `@/`, not already provided by the scaffold).

    These are silently dropped by the v2 import rewrite (which only touches
    ``bifrost`` imports), so migrate-app must install them or the build fails on
    e.g. ``recharts``. Subpath imports (``radix-ui/react-foo``) collapse to root.
    """
    found: set[str] = set()
    for src in sources:
        for spec in _BARE_IMPORT_RE.findall(src):
            if spec.startswith("@/"):
                continue  # the src alias, not a package
            root = _package_root(spec)
            if root and root not in _PROVIDED_PACKAGES:
                found.add(root)
    return sorted(found)


# Completeness guard (used by the drift test): every platform UI symbol must map
# to a component file, so no shadcn import is ever misrouted to lucide.
def _unmapped_ui_symbols() -> set[str]:
    classified = _REACT | _ROUTER | _BIFROST_KEEP | _UTILS | _TOAST | _JS_GLOBALS
    out: set[str] = set()
    for name in PLATFORM_EXPORT_NAMES:
        if name in classified or name in _UI_COMPONENT_FILES:
            continue
        out.add(name)
    return out
