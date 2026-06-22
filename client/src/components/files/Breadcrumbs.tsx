import { Fragment } from "react";
import { ChevronRight } from "lucide-react";

interface BreadcrumbsProps {
	/** Label for the scope root (e.g. "Global" or an org name). */
	scopeLabel: string;
	/** Current location/share, or null when at the shares root. */
	location: string | null;
	/** Path segments under the location. */
	segments: string[];
	/**
	 * Navigate to a depth: -1 = shares root, 0 = location root, n = after the
	 * nth path segment.
	 */
	onNavigate: (depth: number) => void;
}

function Crumb({
	label,
	onClick,
	isCurrent,
}: {
	label: string;
	onClick: () => void;
	isCurrent: boolean;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			title={label}
			className={
				"max-w-[12rem] truncate rounded px-1 text-sm hover:bg-muted " +
				(isCurrent
					? "font-medium text-foreground"
					: "text-muted-foreground")
			}
		>
			{label}
		</button>
	);
}

export function Breadcrumbs({
	scopeLabel,
	location,
	segments,
	onNavigate,
}: BreadcrumbsProps) {
	const lastDepth = location === null ? -1 : segments.length;
	return (
		<nav
			aria-label="Breadcrumb"
			className="flex min-w-0 flex-wrap items-center gap-0.5"
		>
			<Crumb
				label={scopeLabel}
				onClick={() => onNavigate(-1)}
				isCurrent={lastDepth === -1}
			/>
			{location !== null && (
				<Fragment>
					<ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
					<Crumb
						label={location}
						onClick={() => onNavigate(0)}
						isCurrent={lastDepth === 0}
					/>
				</Fragment>
			)}
			{segments.map((segment, index) => (
				<Fragment key={`${segment}-${index}`}>
					<ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
					<Crumb
						label={segment}
						onClick={() => onNavigate(index + 1)}
						isCurrent={lastDepth === index + 1}
					/>
				</Fragment>
			))}
		</nav>
	);
}
