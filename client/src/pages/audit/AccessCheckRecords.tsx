import { useEffect, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	useAuditLog,
	type AuditLogEntry,
	type GetAuditLogParams,
} from "@/hooks/useAuditLog";
import { getErrorMessage } from "@/lib/api-error";
import { cn } from "@/lib/utils";

const EMPTY_CHECKS: AuditLogEntry[] = [];

/** Records in a summary share its context; only their times need repeating. */
export function AccessCheckRecords({
	filters,
	count,
	selectedId,
	onSelect,
}: {
	filters: GetAuditLogParams;
	count: number;
	selectedId: string;
	onSelect: (entry: AuditLogEntry) => void;
}) {
	const [tokens, setTokens] = useState<string[]>([]);
	const [page, setPage] = useState(0);
	const { data, isLoading, isFetching, error, refetch } = useAuditLog(
		{
			...filters,
			action: "access.check",
			outcome: "failure",
			limit: 10,
			continuation_token: tokens[page],
		},
		true,
		{ preservePageData: true },
	);
	const entries = data?.entries ?? EMPTY_CHECKS;
	useEffect(() => {
		if (
			!isFetching &&
			entries.length &&
			!entries.some((entry) => entry.id === selectedId)
		)
			onSelect(entries[0]);
	}, [entries, isFetching, selectedId, onSelect]);
	return (
		<section
			aria-label="Recorded Checks"
			aria-busy={isFetching}
			className="space-y-2"
		>
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h3 className="text-sm font-semibold">
					Recorded Checks{" "}
					<span className="font-normal text-muted-foreground">
						({count})
					</span>
				</h3>
				<nav
					aria-label="Recorded check pages"
					className="flex items-center gap-1"
				>
					<Button
						variant="ghost"
						size="icon"
						className="size-11 sm:size-9"
						aria-label="Previous Check Page"
						disabled={page === 0 || isFetching}
						onClick={() => setPage(page - 1)}
					>
						<ChevronLeft aria-hidden="true" className="size-4" />
					</Button>
					<span
						className="text-xs text-muted-foreground"
						aria-live="polite"
					>
						Page {page + 1}
					</span>
					<Button
						variant="ghost"
						size="icon"
						className="size-11 sm:size-9"
						aria-label="Next Check Page"
						disabled={!data?.continuation_token || isFetching}
						onClick={() => {
							if (!data?.continuation_token) return;
							setTokens((current) => {
								const next = [...current];
								next[page + 1] = data.continuation_token!;
								return next;
							});
							setPage(page + 1);
						}}
					>
						<ChevronRight aria-hidden="true" className="size-4" />
					</Button>
				</nav>
			</div>
			{error ? (
				<Alert variant="destructive">
					<AlertDescription className="flex flex-wrap items-center gap-2">
						<span>
							{getErrorMessage(
								error,
								"The recorded checks could not load.",
							)}
						</span>
						<Button
							variant="outline"
							size="sm"
							onClick={() => void refetch()}
						>
							Try Again
						</Button>
					</AlertDescription>
				</Alert>
			) : isLoading && !data ? (
				<p
					role="status"
					className="flex items-center gap-2 text-sm text-muted-foreground"
				>
					<Loader2
						aria-hidden="true"
						className="size-4 animate-spin motion-reduce:animate-none"
					/>
					Loading recorded checks…
				</p>
			) : entries.length ? (
				<ol
					aria-label="Check Times"
					className="flex max-h-24 flex-wrap gap-1.5 overflow-auto"
				>
					{entries.map((entry) => (
						<li key={entry.id}>
							<button
								type="button"
								aria-pressed={entry.id === selectedId}
								disabled={isFetching}
								onClick={() => onSelect(entry)}
								className={cn(
									"inline-flex min-h-11 items-center gap-1.5 rounded-[var(--bf-radius-control)] border px-2.5 py-1.5 text-xs hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50 sm:min-h-9",
									entry.id === selectedId &&
										"border-primary bg-primary/10 text-primary",
								)}
							>
								{entry.id === selectedId && (
									<Check
										aria-hidden="true"
										className="size-3.5 shrink-0"
									/>
								)}
								<time dateTime={entry.timestamp}>
									{new Date(entry.timestamp).toLocaleString(
										undefined,
										{
											year: "numeric",
											month: "short",
											day: "numeric",
											hour: "numeric",
											minute: "2-digit",
											second: "2-digit",
											fractionalSecondDigits: 3,
										},
									)}
								</time>
							</button>
						</li>
					))}
				</ol>
			) : (
				<p className="text-sm text-muted-foreground">
					No recorded checks remain in this summary.
				</p>
			)}
		</section>
	);
}
