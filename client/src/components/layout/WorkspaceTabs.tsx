import { NavLink } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const TITLE_CLASS_NAME =
	"whitespace-nowrap font-display text-xl font-semibold tracking-tight sm:text-3xl";

export interface WorkspaceTab {
	to: string;
	label: string;
}

const HOME_TABS: WorkspaceTab[] = [
	{ to: "/", label: "Workspace" },
	{ to: "/dashboard", label: "Dashboard" },
];

/**
 * The page title as route navigation between sibling views. Without `tabs`
 * it is Workspace | Dashboard, the dashboard only for Platform Admins; a
 * single view is a plain title.
 */
export function WorkspaceTabs({
	tabs,
	label = "Workspace views",
}: {
	tabs?: WorkspaceTab[];
	label?: string;
}) {
	const { isPlatformAdmin } = useAuth();
	const views = tabs ?? (isPlatformAdmin ? HOME_TABS : HOME_TABS.slice(0, 1));
	if (views.length === 1) {
		return <h1 className={TITLE_CLASS_NAME}>{views[0].label}</h1>;
	}
	return (
		<nav
			aria-label={label}
			className="flex min-w-0 items-center gap-0 sm:gap-1"
		>
			{views.map(({ to, label: viewLabel }) => (
				<NavLink
					key={to}
					to={to}
					end
					className={({ isActive }) =>
						cn(
							"inline-flex min-h-11 shrink-0 items-center border-b-2 px-1 transition-colors focus-visible:outline-2 focus-visible:outline-ring sm:px-3",
							isActive
								? "border-primary text-primary"
								: "border-transparent text-muted-foreground hover:text-foreground",
						)
					}
				>
					{({ isActive }) => (
						<span
							className={TITLE_CLASS_NAME}
							role={isActive ? "heading" : undefined}
							aria-level={isActive ? 1 : undefined}
						>
							{viewLabel}
						</span>
					)}
				</NavLink>
			))}
		</nav>
	);
}
