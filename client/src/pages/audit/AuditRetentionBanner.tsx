import { Archive, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AuditRetentionInfo } from "@/services/auditRetention";
import { formatAuditTime } from "./auditRetentionFormat";

/** How far back the database goes, what is archived, and a way to export both. */
export function AuditRetentionBanner({
	retention,
	onExport,
}: {
	retention: AuditRetentionInfo | null | undefined;
	onExport: () => void;
}) {
	if (!retention) return null;
	const sentences = [
		retention.oldest_in_database &&
			`Showing events since ${formatAuditTime(retention.oldest_in_database)}.`,
		retention.archived_through &&
			`Older events are archived through ${formatAuditTime(retention.archived_through)}.`,
	].filter(Boolean);
	return (
		<section
			aria-label="Audit retention"
			className="flex min-w-0 flex-col gap-3 rounded-[var(--bf-radius-surface)] border bg-card px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
		>
			<div className="flex min-w-0 items-start gap-2.5">
				<Archive
					aria-hidden="true"
					className="mt-0.5 size-4 shrink-0 text-muted-foreground"
				/>
				<p className="min-w-0 text-muted-foreground [overflow-wrap:anywhere]">
					{sentences.length > 0
						? sentences.join(" ")
						: "No audit events yet."}
				</p>
			</div>
			<Button
				type="button"
				variant="outline"
				className="min-h-11 shrink-0"
				onClick={onExport}
			>
				<Download className="mr-2 h-4 w-4" />
				Export
			</Button>
		</section>
	);
}
