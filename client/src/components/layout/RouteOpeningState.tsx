import { AppWindow, Bot } from "lucide-react";

type OpeningKind = "agent" | "application" | "page";

function openingKind(pathname: string): OpeningKind {
	if (pathname.startsWith("/agents/")) return "agent";
	if (pathname.startsWith("/apps/")) return "application";
	return "page";
}

export function RouteOpeningState() {
	const kind = openingKind(window.location.pathname);
	const Icon = kind === "agent" ? Bot : AppWindow;
	const label =
		kind === "agent"
			? "Opening agent…"
			: kind === "application"
				? "Opening application…"
				: "Opening page…";

	return (
		<div
			className="flex h-dvh w-screen items-center justify-center bg-background"
			role="status"
			aria-live="polite"
			aria-label={label}
		>
			<div className="flex flex-col items-center gap-3 text-muted-foreground">
				<div className="relative grid h-12 w-12 place-items-center rounded-2xl bg-muted/70 ring-1 ring-foreground/10">
					<span
						aria-hidden="true"
						className="absolute inset-1 rounded-xl border-2 border-transparent border-t-primary motion-safe:animate-spin"
					/>
					<Icon
						aria-hidden="true"
						className="h-5 w-5 text-foreground"
					/>
				</div>
				<p className="text-sm font-medium">{label}</p>
			</div>
		</div>
	);
}

export { openingKind };
