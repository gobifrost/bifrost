import { useRunRetentionDays } from "@/services/runRetention";

/**
 * Says a referenced run is gone. Finished runs are deleted after the admin's
 * retention window, so a 404 for a run is expected, not an error to retry.
 * `page` replaces a run page; `inline` is a muted line inside a card or row.
 */
export function RunRemovedNotice({ variant }: { variant: "page" | "inline" }) {
	const days = useRunRetentionDays();
	const known = typeof days === "number";

	if (variant === "inline") {
		return (
			<p className="text-xs text-muted-foreground">
				{known
					? `Removed after ${days} days (retention)`
					: "Run details are no longer available"}
			</p>
		);
	}

	return (
		<p
			role="status"
			className="min-w-0 rounded-[var(--bf-radius-surface)] border bg-muted/30 p-4 text-sm text-muted-foreground"
		>
			<span className="font-medium text-foreground">
				This run isn't available.
			</span>{" "}
			{known
				? `Finished runs are removed after ${days} days (retention). `
				: ""}
			It may also be outside your access.
		</p>
	);
}
