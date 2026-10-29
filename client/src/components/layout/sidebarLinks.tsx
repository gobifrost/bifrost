import { Link, NavLink, type LinkProps } from "react-router-dom";
import type { ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { navigationSelectionClasses } from "./navigationStyles";

export function SidebarLink({
	to,
	label,
	icon: Icon,
	isCollapsed = false,
	onClick,
	isActive,
	end,
	children,
}: {
	to: LinkProps["to"];
	label: string;
	icon: ElementType;
	isCollapsed?: boolean;
	onClick?: () => void;
	isActive?: boolean;
	/** Match only `to` itself, not the routes below it. */
	end?: boolean;
	children?: ReactNode;
}) {
	const linkClassName = (active: boolean) =>
		cn(
			"flex min-h-10 items-center text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
			navigationSelectionClasses(active),
			isCollapsed
				? "justify-center w-10 h-10 mx-auto"
				: "gap-3 px-3 py-2",
		);
	const content = (
		<>
			<Icon
				className={cn(
					isCollapsed ? "h-5 w-5 shrink-0" : "h-4 w-4 shrink-0",
				)}
			/>
			{!isCollapsed &&
				(children ?? (
					<span className="min-w-0 break-words">{label}</span>
				))}
		</>
	);

	if (isActive !== undefined) {
		return (
			<Link
				to={to}
				aria-label={label}
				aria-current={isActive ? "page" : undefined}
				title={isCollapsed ? label : undefined}
				onClick={onClick}
				className={linkClassName(isActive)}
			>
				{content}
			</Link>
		);
	}

	return (
		<NavLink
			to={to}
			end={end}
			aria-label={label}
			title={isCollapsed ? label : undefined}
			onClick={onClick}
			className={({ isActive: routeActive }) =>
				linkClassName(routeActive)
			}
		>
			{content}
		</NavLink>
	);
}
