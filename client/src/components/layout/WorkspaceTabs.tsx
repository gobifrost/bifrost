import { NavLink } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const TITLE_CLASS_NAME =
	"[overflow-wrap:anywhere] font-display text-2xl font-semibold tracking-tight sm:text-3xl";

/** The workspace title becomes route navigation when the dashboard is available. */
export function WorkspaceTabs() {
	const { isPlatformAdmin } = useAuth();
	if (!isPlatformAdmin) {
		return <h1 className={TITLE_CLASS_NAME}>Workspace</h1>;
	}
	return (
		<nav aria-label="Workspace views" className="flex items-center gap-1">
			{[
				{ to: "/", label: "Workspace" },
				{ to: "/dashboard", label: "Dashboard" },
			].map(({ to, label }) => (
				<NavLink
					key={to}
					to={to}
					end
					className={({ isActive }) =>
						cn(
							"inline-flex min-h-11 items-center border-b-2 px-3 transition-colors focus-visible:outline-2 focus-visible:outline-ring",
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
							{label}
						</span>
					)}
				</NavLink>
			))}
		</nav>
	);
}
