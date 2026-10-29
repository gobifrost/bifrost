import { useLayoutEffect, useRef, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type AuditView = "activity" | "access-checks";

const auditRoutes: Record<AuditView, string> = {
	activity: "/audit",
	"access-checks": "/audit/access-checks",
};

function shouldRestoreTabFocus(state: unknown, view: AuditView) {
	return (
		typeof state === "object" &&
		state !== null &&
		"auditTabFocus" in state &&
		(state as { auditTabFocus?: unknown }).auditTabFocus === view
	);
}

export function AuditPageLayout({
	view,
	actions,
	children,
}: {
	view: AuditView;
	actions?: ReactNode;
	children: ReactNode;
}) {
	const navigate = useNavigate();
	const location = useLocation();
	const keyboardTabNavigation = useRef(false);
	const restoreTabFocus = useRef(shouldRestoreTabFocus(location.state, view));
	const selectedTab = useRef<HTMLButtonElement>(null);

	useLayoutEffect(() => {
		if (!restoreTabFocus.current) return;
		restoreTabFocus.current = false;
		selectedTab.current?.focus();
	}, [view]);

	return (
		<PageWorkspace className="mx-auto flex w-full min-w-0 max-w-[1400px] flex-col gap-5 lg:flex-1">
			<div className="flex shrink-0 items-start justify-between gap-4">
				<div>
					<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
						Auditing
					</h1>
					<p className="mt-2 text-sm text-muted-foreground">
						Review administrative activity and access decisions
						across the platform.
					</p>
				</div>
				{actions && <div className="shrink-0">{actions}</div>}
			</div>

			<Tabs
				value={view}
				onValueChange={(nextView) => {
					const nextAuditView = nextView as AuditView;
					const restoreFocus = keyboardTabNavigation.current;
					keyboardTabNavigation.current = false;
					navigate(auditRoutes[nextAuditView], {
						state: restoreFocus
							? { auditTabFocus: nextAuditView }
							: undefined,
					});
				}}
				className="min-h-0 gap-5 lg:flex-1"
			>
				<TabsList
					variant="line"
					aria-label="Auditing views"
					onKeyDownCapture={(event) => {
						keyboardTabNavigation.current = [
							"ArrowLeft",
							"ArrowRight",
							"Home",
							"End",
						].includes(event.key);
					}}
				>
					<TabsTrigger
						ref={view === "activity" ? selectedTab : undefined}
						value="activity"
						className="min-h-11 px-4"
					>
						Activity
					</TabsTrigger>
					<TabsTrigger
						ref={view === "access-checks" ? selectedTab : undefined}
						value="access-checks"
						className="min-h-11 px-4"
					>
						Access Checks
					</TabsTrigger>
				</TabsList>
				<TabsContent
					value={view}
					className="flex min-h-0 min-w-0 flex-1 flex-col"
				>
					<div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
						{children}
					</div>
				</TabsContent>
			</Tabs>
		</PageWorkspace>
	);
}
