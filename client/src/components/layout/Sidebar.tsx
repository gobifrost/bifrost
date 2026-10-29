import { Fragment, useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import {
	Home,
	Workflow,
	History,
	Building,
	Users,
	FileCode,
	Key,
	UserCog,
	Settings as SettingsIcon,
	X,
	Stethoscope,
	ShieldCheck,
	MessageSquare,
	Bot,
	Plug,
	DollarSign,
	Activity,
	Webhook,
	Database,
	FolderOpen,
	AppWindow,
	Network,
	BookOpen,
	ServerCog,
	Boxes,
	Radio,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/branding/Logo";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { term, useTerminology, type ProductTermKey } from "@/lib/terminology";
import type { PermissionRequirement } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import { SidebarLink } from "./sidebarLinks";

interface NavItem {
	title: string;
	termKey?: ProductTermKey;
	href: string;
	icon: React.ElementType;
	requiresPermission?: PermissionRequirement;
	dividerBefore?: boolean;
	/** Active only on this exact path, not on the pages below it. */
	end?: boolean;
}

interface NavSection {
	title: string;
	items: NavItem[];
}

const navSections: NavSection[] = [
	{
		title: "Workspace",
		items: [
			{ title: "Home", href: "/", icon: Home },
			{
				title: "Chat",
				href: "/chat",
				icon: MessageSquare,
			},
			{
				title: "History",
				href: "/history",
				icon: History,
			},
		],
	},
	{
		title: "Management",
		items: [
			{
				title: "Apps",
				termKey: "app",
				href: "/apps",
				icon: AppWindow,
				requiresPermission: { permission: "apps.readbasic" },
			},
			{
				title: "Forms",
				termKey: "form",
				href: "/forms",
				icon: FileCode,
				requiresPermission: { permission: "forms.readbasic" },
			},
			{
				title: "Agents",
				termKey: "agent",
				href: "/agents",
				icon: Bot,
				requiresPermission: { permission: "agents.readbasic" },
			},
			{
				title: "Workflows",
				href: "/workflows",
				icon: Workflow,
				requiresPermission: { permission: "workflows.read" },
			},
			{
				title: "Services",
				href: "/services",
				icon: Radio,
				requiresPermission: { permission: "platform.read" },
			},
		],
	},
	{
		title: "Data",
		items: [
			{
				title: "Config",
				href: "/config",
				icon: Key,
				requiresPermission: { permission: "configs.read" },
			},
			{
				title: "Tables",
				href: "/tables",
				icon: Database,
				requiresPermission: { permission: "tables.read" },
			},
			{
				title: "Files",
				href: "/files",
				icon: FolderOpen,
				requiresPermission: {
					permission: "filepolicies.read",
				},
			},
			{
				title: "Knowledge",
				href: "/knowledge",
				icon: BookOpen,
				requiresPermission: { permission: "knowledge.read" },
			},
			{
				title: "Integrations",
				href: "/integrations",
				icon: Plug,
				requiresPermission: { permission: "integrations.read" },
			},
			{
				title: "MCP Servers",
				href: "/mcp-servers",
				icon: ServerCog,
				requiresPermission: { permission: "mcp.readbasic" },
			},
			{
				title: "Events",
				href: "/event-sources",
				icon: Webhook,
				requiresPermission: { permission: "events.read" },
			},
			{
				title: "Entity Management",
				href: "/entity-management",
				icon: Network,
				requiresPermission: {
					allOf: [
						{ permission: "workflows.read" },
						{ permission: "forms.readbasic" },
						{ permission: "agents.readbasic" },
						{ permission: "apps.readbasic" },
						{ permission: "organizations.read" },
						{ permission: "roles.read", at: "global" },
					],
				},
			},
		],
	},
	{
		title: "Platform",
		items: [
			{
				title: "Organizations",
				href: "/organizations",
				icon: Building,
				requiresPermission: { permission: "organizations.read" },
			},
			{
				title: "Users",
				href: "/users",
				icon: Users,
				requiresPermission: { permission: "users.read" },
			},
			{
				title: "Roles",
				href: "/roles",
				icon: UserCog,
				requiresPermission: { permission: "roles.read", at: "global" },
			},
			{
				title: "Solutions",
				href: "/solutions",
				icon: Boxes,
				requiresPermission: { permission: "solutions.read" },
			},
			{
				title: "Settings",
				href: "/settings",
				icon: SettingsIcon,
				requiresPermission: {
					permission: "settings.read",
					at: "global",
				},
			},
			{
				title: "Diagnostics",
				href: "/diagnostics",
				icon: Stethoscope,
				requiresPermission: {
					allOf: [
						{ permission: "metrics.read", at: "global" },
						{ permission: "platform.read", at: "global" },
					],
				},
			},
			{
				title: "Auditing",
				href: "/audit",
				icon: ShieldCheck,
				requiresPermission: { permission: "roleassignments.read" },
			},
		],
	},
	{
		title: "Reports",
		items: [
			{
				title: "ROI",
				href: "/reports/roi",
				icon: DollarSign,
				requiresPermission: { permission: "reports.read" },
			},
			{
				title: "Usage",
				href: "/reports/usage",
				icon: Activity,
				requiresPermission: {
					permission: "metrics.read",
					at: "global",
				},
			},
		],
	},
];

interface SidebarProps {
	isMobileMenuOpen: boolean;
	setIsMobileMenuOpen: (open: boolean) => void;
	isCollapsed: boolean;
}

export function Sidebar({
	isMobileMenuOpen,
	setIsMobileMenuOpen,
	isCollapsed,
}: SidebarProps) {
	const { meets } = useAuthorization();
	const terminology = useTerminology();
	const location = useLocation();
	const desktopNavRef = useRef<HTMLElement | null>(null);
	const desktopNavScrollTopRef = useRef(0);

	// A mobile modal must not keep the desktop shell inert after a resize.
	useEffect(() => {
		if (!isMobileMenuOpen) return;
		const media = window.matchMedia("(min-width: 768px)");
		const closeOnDesktop = () => {
			if (media.matches) setIsMobileMenuOpen(false);
		};
		closeOnDesktop();
		media.addEventListener("change", closeOnDesktop);
		return () => media.removeEventListener("change", closeOnDesktop);
	}, [isMobileMenuOpen, setIsMobileMenuOpen]);

	// Filter sections and items based on user permissions
	const visibleSections = navSections
		.map((section) => ({
			...section,
			items: section.items.filter(
				(item) =>
					!item.requiresPermission || meets(item.requiresPermission),
			),
		}))
		.filter((section) => section.items.length > 0); // Remove empty sections

	useLayoutEffect(() => {
		const nav = desktopNavRef.current;
		if (!nav) return;
		nav.scrollTop = desktopNavScrollTopRef.current;
	}, [location.pathname, location.search]);

	return (
		<>
			{/* Desktop Sidebar */}
			<aside
				className={cn(
					"bf-platform-rail relative hidden h-full md:flex shrink-0 flex-col border-r bg-sidebar transition-[width] duration-200 motion-reduce:transition-none",
					isCollapsed ? "w-16" : "w-[248px]",
				)}
			>
				{/* Logo Section */}
				<div
					className={cn(
						"h-16 flex items-center border-b",
						isCollapsed
							? "justify-center px-4"
							: "justify-start px-7",
					)}
				>
					{isCollapsed ? (
						<Logo type="square" className="h-10 w-10" alt="Logo" />
					) : (
						<Logo type="rectangle" className="h-8" alt="Logo" />
					)}
				</div>

				{/* Navigation */}
				<nav
					ref={desktopNavRef}
					aria-label="Primary navigation"
					className={cn(
						"flex-1 flex flex-col gap-4 overflow-y-auto",
						isCollapsed ? "px-2 py-4" : "p-4",
					)}
					onScroll={(event) => {
						desktopNavScrollTopRef.current =
							event.currentTarget.scrollTop;
					}}
				>
					{visibleSections.map((section) => (
						<Fragment key={section.title}>
							<div className="space-y-1">
								{!isCollapsed && (
									<h3 className="text-xs font-semibold text-muted-foreground mb-2 px-3 uppercase tracking-wider">
										{section.title}
									</h3>
								)}
								{section.items.map((item) => {
									const itemTitle = item.termKey
										? term(
												terminology,
												item.termKey,
												"plural",
											)
										: item.title;
									return (
										<div key={item.href}>
											{item.dividerBefore &&
												!isCollapsed && (
													<div className="my-2 mx-3 border-t border-border" />
												)}
											{item.dividerBefore &&
												isCollapsed && (
													<div className="my-2 mx-2 border-t border-border" />
												)}
											<SidebarLink
												to={item.href}
												label={itemTitle}
												icon={item.icon}
												isCollapsed={isCollapsed}
												end={item.end}
												isActive={
													item.href === "/"
														? (location.pathname ===
																"/" &&
																!location.search) ||
															location.pathname ===
																"/dashboard"
														: undefined
												}
											/>
										</div>
									);
								})}
							</div>
						</Fragment>
					))}
				</nav>
			</aside>

			{/* Mobile Sidebar Overlay */}
			<Sheet open={isMobileMenuOpen} onOpenChange={setIsMobileMenuOpen}>
				<SheetContent
					side="left"
					showCloseButton={false}
					className="bf-platform-rail w-[min(20rem,calc(100vw-2rem))] gap-0 bg-sidebar"
					aria-describedby={undefined}
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						document
							.querySelector<HTMLElement>(
								"[data-mobile-navigation-trigger]",
							)
							?.focus();
					}}
				>
					<SheetTitle className="sr-only">Navigation</SheetTitle>
					{/* Logo Section with Close Button */}
					<div className="h-16 flex items-center justify-between border-b px-4">
						<Logo type="rectangle" className="h-8" alt="Logo" />
						<Button
							variant="ghost"
							size="icon"
							onClick={() => setIsMobileMenuOpen(false)}
							aria-label="Close navigation"
						>
							<X className="h-5 w-5" />
						</Button>
					</div>

					{/* Navigation */}
					<nav
						aria-label="Primary navigation"
						className="flex-1 min-h-0 flex flex-col gap-4 p-4 overflow-y-auto"
					>
						{visibleSections.map((section) => (
							<Fragment key={section.title}>
								<div className="space-y-1">
									<h3 className="text-xs font-semibold text-muted-foreground mb-2 px-3 uppercase tracking-wider">
										{section.title}
									</h3>
									{section.items.map((item) => {
										const itemTitle = item.termKey
											? term(
													terminology,
													item.termKey,
													"plural",
												)
											: item.title;
										return (
											<div key={item.href}>
												{item.dividerBefore && (
													<div className="my-2 mx-3 border-t border-border" />
												)}
												<SidebarLink
													to={item.href}
													label={itemTitle}
													icon={item.icon}
													end={item.end}
													onClick={() =>
														setIsMobileMenuOpen(
															false,
														)
													}
													isActive={
														item.href === "/"
															? (location.pathname ===
																	"/" &&
																	!location.search) ||
																location.pathname ===
																	"/dashboard"
															: undefined
													}
												/>
											</div>
										);
									})}
								</div>
							</Fragment>
						))}
					</nav>
				</SheetContent>
			</Sheet>
		</>
	);
}
