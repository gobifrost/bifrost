import type { AuditLogEntry } from "@/hooks/useAuditLog";
import { AuditOutcome } from "./AuditOutcome";

/** Audit events as cards for narrow screens; each card's action opens the event. */
export function AuditEventCards({
	entries,
	context,
	onOpen,
}: {
	entries: AuditLogEntry[];
	context: (entry: AuditLogEntry) => string;
	onOpen: (entry: AuditLogEntry, opener: HTMLElement) => void;
}) {
	return (
		<ol aria-label="Audit events" className="space-y-3">
			{entries.map((entry) => (
				<li
					key={entry.id}
					className="relative min-w-0 rounded-[var(--bf-radius-surface)] border bg-card p-4 [overflow-wrap:anywhere] has-[button:hover]:bg-muted/40"
				>
					<div className="flex flex-wrap items-center justify-between gap-2">
						<h2 className="min-w-0 text-sm font-semibold">
							{/* Stretched over the card, so the whole card opens the event. */}
							<button
								type="button"
								className="text-left after:absolute after:inset-0 after:rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring"
								onClick={(event) =>
									onOpen(entry, event.currentTarget)
								}
							>
								{entry.action}
							</button>
						</h2>
						<AuditOutcome outcome={entry.outcome} />
					</div>
					<time
						dateTime={entry.timestamp}
						className="mt-2 block text-xs text-muted-foreground"
					>
						{new Date(entry.timestamp).toLocaleString()}
					</time>
					<dl className="mt-4 space-y-3 text-sm">
						{[
							[
								"Actor",
								entry.actor.user_email ||
									entry.actor.user_name ||
									(entry.source !== "http"
										? `(${entry.source})`
										: "(unauthenticated)"),
							],
							[
								"Resource",
								entry.resource_type
									? `${entry.resource_type}${entry.resource_id ? ` / ${entry.resource_id}` : ""}`
									: "—",
							],
							["Context", context(entry)],
							["IP address", entry.ip_address || "—"],
						].map(([label, value]) => (
							<div key={label}>
								<dt className="text-xs text-muted-foreground">
									{label}
								</dt>
								<dd className="mt-1 whitespace-pre-wrap">
									{value}
								</dd>
							</div>
						))}
					</dl>
				</li>
			))}
		</ol>
	);
}
