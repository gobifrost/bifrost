"""
Canonical list of names exposed by the Bifrost platform to bundled apps.

This is the **single source of truth** for "what does `import { X } from 'bifrost'`
resolve to." It is consumed by:

- `api/src/services/app_bundler/__init__.py` — the bundler uses it to decide
  which names go through the `globalThis.__bifrost_platform` proxy (vs. being
  user components or Lucide icons).
- `api/bifrost/cli.py` (`bifrost migrate-imports`) — the classifier uses it
  to decide which imports stay in `"bifrost"` vs. get moved to
  `"lucide-react"` / `"react-router-dom"` / a local `./components/*` path.

A drift test (`tests/unit/test_platform_names_match_runtime.py`) verifies that
every key in the client's `$` registry (`client/src/lib/app-code-runtime.ts`)
is present in this set, so a new platform export cannot silently ship without
the bundler and classifier knowing about it.

If you add a new platform export:
1. Add the export to `client/src/lib/app-code-runtime.ts`'s `$` registry
   (or a module spread into it).
2. Add the name here.
3. Run `./test.sh tests/unit/test_platform_names_match_runtime.py` to confirm.

Lives in the standalone `bifrost` package (not `src/*`) so the CLI, which
cannot import from `src.*`, can share it.
"""
from __future__ import annotations


PLATFORM_EXPORT_NAMES: frozenset[str] = frozenset({
    # React
    "React", "Fragment", "Suspense", "lazy", "memo", "forwardRef",
    "useState", "useEffect", "useCallback", "useMemo", "useRef",
    "useContext", "useReducer", "useLayoutEffect", "useId",
    "useTransition", "useDeferredValue", "useImperativeHandle",
    # Router — platform-provided (wrapped) and raw re-exports from react-router-dom
    "Outlet", "Link", "NavLink", "Navigate", "useNavigate", "navigate",
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
    "UNSAFE_DataRouterContext", "UNSAFE_DataRouterStateContext",
    "UNSAFE_NavigationContext", "UNSAFE_LocationContext", "UNSAFE_RouteContext",
    # Platform
    "useUser", "useAppState",
    "useWorkflowQuery", "useWorkflowMutation",
    "RequireRole",
    # Tables SDK
    "tables", "useTable", "useTablePaged",
    # Global JS built-ins re-asserted in `$` to win over Lucide icons of the
    # same name (e.g. Lucide ships a `Map` icon — without these entries
    # `new Map()` in user code would resolve to the icon component).
    "Map", "Set", "WeakMap", "WeakSet", "Date",
    # Utilities
    "cn", "clsx", "twMerge", "format",
    "formatDate", "formatDateShort", "formatTime", "formatRelativeTime",
    "formatBytes", "formatNumber", "formatCost", "formatDuration",
    # Toast
    "toast",
    # UI components
    "Button", "buttonVariants",
    "Input", "Label", "Textarea", "Checkbox", "Switch",
    "Select", "SelectContent", "SelectGroup", "SelectItem", "SelectLabel",
    "SelectTrigger", "SelectValue", "SelectSeparator",
    "SelectScrollUpButton", "SelectScrollDownButton",
    "RadioGroup", "RadioGroupItem", "Combobox", "MultiCombobox",
    "TagsInput", "Slider",
    "Card", "CardHeader", "CardFooter", "CardTitle", "CardAction",
    "CardDescription", "CardContent",
    "Badge", "badgeVariants",
    "Avatar", "AvatarImage", "AvatarFallback",
    "Alert", "AlertTitle", "AlertDescription",
    "Skeleton", "Progress",
    "Tabs", "TabsList", "TabsTrigger", "TabsContent",
    "Dialog", "DialogClose", "DialogContent", "DialogDescription",
    "DialogFooter", "DialogHeader", "DialogTitle", "DialogTrigger",
    "DialogOverlay", "DialogPortal",
    "AlertDialog", "AlertDialogTrigger", "AlertDialogContent",
    "AlertDialogHeader", "AlertDialogFooter", "AlertDialogTitle",
    "AlertDialogDescription", "AlertDialogAction", "AlertDialogCancel",
    "AlertDialogOverlay", "AlertDialogPortal",
    "Tooltip", "TooltipContent", "TooltipProvider", "TooltipTrigger",
    "Popover", "PopoverContent", "PopoverTrigger", "PopoverAnchor",
    "Sheet", "SheetClose", "SheetContent", "SheetDescription",
    "SheetFooter", "SheetHeader", "SheetTitle", "SheetTrigger",
    "Command", "CommandDialog", "CommandEmpty", "CommandGroup",
    "CommandInput", "CommandItem", "CommandList", "CommandSeparator",
    "CommandShortcut",
    "ContextMenu", "ContextMenuTrigger", "ContextMenuContent",
    "ContextMenuItem", "ContextMenuCheckboxItem", "ContextMenuRadioItem",
    "ContextMenuLabel", "ContextMenuSeparator", "ContextMenuShortcut",
    "ContextMenuGroup", "ContextMenuPortal", "ContextMenuSub",
    "ContextMenuSubContent", "ContextMenuSubTrigger", "ContextMenuRadioGroup",
    "HoverCard", "HoverCardTrigger", "HoverCardContent",
    "Pagination", "PaginationContent", "PaginationItem", "PaginationLink",
    "PaginationNext", "PaginationPrevious", "PaginationEllipsis",
    "Table", "TableHeader", "TableBody", "TableFooter",
    "TableHead", "TableRow", "TableCell", "TableCaption",
    "Accordion", "AccordionContent", "AccordionItem", "AccordionTrigger",
    "Collapsible", "CollapsibleContent", "CollapsibleTrigger",
    "Toggle", "toggleVariants", "ToggleGroup", "ToggleGroupItem",
    "Separator",
    "DropdownMenu", "DropdownMenuContent", "DropdownMenuItem",
    "DropdownMenuLabel", "DropdownMenuSeparator", "DropdownMenuTrigger",
    "DropdownMenuGroup", "DropdownMenuPortal",
    "DropdownMenuCheckboxItem", "DropdownMenuRadioGroup",
    "DropdownMenuRadioItem",
    "DropdownMenuShortcut", "DropdownMenuSub", "DropdownMenuSubContent",
    "DropdownMenuSubTrigger",
    # `Calendar` imported from "bifrost" resolves to the Lucide calendar
    # icon (via the Lucide spread in `$`), NOT a picker component. Apps that
    # want a date picker import `CalendarPicker` instead. `CalendarPicker`
    # and `CalendarDayButton` are the shadcn Calendar parts, aliased to
    # avoid the Lucide shadow. `DateRangePicker` is the platform component.
    "Calendar", "CalendarPicker", "CalendarDayButton", "DateRangePicker",
    # Lucide icons are NOT enumerated here — there are ~1000 of them and they
    # come from the real `lucide-react` package. The bundler resolves names
    # imported from "bifrost" that aren't in this set (and aren't user
    # components) as lucide icons; the classifier in `bifrost migrate-imports`
    # does the same.
})
