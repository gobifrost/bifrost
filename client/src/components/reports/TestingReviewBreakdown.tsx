import { useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, RotateCw } from "lucide-react";
import type { components } from "@/lib/v1";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { formatCurrency, formatNumber } from "./formatters";

type Breakdown = components["schemas"]["QualityUsageBreakdownResponse"];
type Coverage = components["schemas"]["QualityUsageCoverage"];
type Totals = components["schemas"]["QualityUsageTotals"];

export type TestingReviewFilters = {
	purpose?: string | null;
	provider?: string | null;
	model?: string | null;
	profile_id?: string | null;
	profile_fingerprint?: string | null;
};

type GroupKind =
	| "purpose"
	| "providerModel"
	| "profile"
	| "operation"
	| "organization";

interface TestingReviewBreakdownProps {
	data: Breakdown | undefined;
	isLoading: boolean;
	error: unknown;
	isFetching: boolean;
	onRetry: () => void;
	filters: TestingReviewFilters;
	onFiltersChange: (filters: TestingReviewFilters) => void;
}

function formatDuration(ms: number) {
	if (ms < 1000) return `${formatNumber(ms)} ms`;
	return `${(ms / 1000).toFixed(1)} s`;
}

function tokenTotal(totals: Totals) {
	return totals.input_tokens + totals.output_tokens;
}

function updateTextFilter(
	filters: TestingReviewFilters,
	key: keyof TestingReviewFilters,
	value: string,
) {
	return {
		...filters,
		[key]: value.trim() || null,
	};
}

function CoverageLine({ coverage }: { coverage: Coverage }) {
	const parts = [
		`${formatNumber(coverage.unobserved_attempt_count)} unobserved attempts`,
		`${formatNumber(coverage.missing_cost_call_count)} calls missing cost`,
		`${formatNumber(coverage.unassigned_operation_call_count)} calls without operation`,
	];
	if (coverage.legacy_coverage_unknown) parts.push("Legacy coverage unknown");
	if (coverage.legacy_call_count) {
		parts.push(`${formatNumber(coverage.legacy_call_count)} legacy calls`);
	}
	return (
		<p className="text-sm text-muted-foreground">
			{parts.filter(Boolean).join(" · ")}
		</p>
	);
}

function SummaryMetric({
	label,
	value,
	detail,
}: {
	label: string;
	value: string;
	detail?: string;
}) {
	return (
		<div className="rounded-[var(--bf-radius-card)] border bg-muted/20 p-3">
			<p className="text-xs font-medium uppercase tracking-normal text-muted-foreground">
				{label}
			</p>
			<p className="mt-1 text-lg font-semibold">{value}</p>
			{detail && <p className="mt-1 text-xs text-muted-foreground">{detail}</p>}
		</div>
	);
}

function GroupTable({
	label,
	rows,
}: {
	label: string;
	rows: Array<{
		key: string;
		name: string;
		context?: string;
		totals: Totals;
		coverage: Coverage;
	}>;
}) {
	if (rows.length === 0) {
		return <p className="text-sm text-muted-foreground">No groups found.</p>;
	}
	return (
		<DataTable role="region" aria-label={`${label} breakdown`}>
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead>Group</DataTableHead>
					<DataTableHead>Context</DataTableHead>
					<DataTableHead className="text-right">Calls</DataTableHead>
					<DataTableHead className="text-right">Tokens</DataTableHead>
					<DataTableHead className="text-right">Known cost</DataTableHead>
					<DataTableHead>Coverage</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{rows.map((row) => (
					<DataTableRow key={row.key}>
						<DataTableCell className="min-w-48 font-medium [overflow-wrap:anywhere]">
							{row.name}
						</DataTableCell>
						<DataTableCell className="text-muted-foreground [overflow-wrap:anywhere]">
							{row.context ?? "—"}
						</DataTableCell>
						<DataTableCell className="text-right font-mono tabular-nums">
							{formatNumber(row.totals.call_count)}
						</DataTableCell>
						<DataTableCell className="text-right font-mono tabular-nums">
							{formatNumber(tokenTotal(row.totals))}
						</DataTableCell>
						<DataTableCell className="text-right font-mono tabular-nums">
							{formatCurrency(row.totals.known_cost)}
						</DataTableCell>
						<DataTableCell>
							<CoverageLine coverage={row.coverage} />
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}

function groupRows(data: Breakdown, kind: GroupKind) {
	switch (kind) {
		case "purpose":
			return data.by_purpose.items.map((row) => ({
				key: row.purpose ?? "unknown",
				name: row.purpose ?? "Unknown purpose",
				totals: row.totals,
				coverage: row.coverage,
			}));
		case "providerModel":
			return data.by_provider_model.items.map((row, index) => ({
				key: `${row.provider ?? "unknown"}-${row.model ?? "unknown"}-${row.purpose ?? "all"}-${index}`,
				name: `${row.provider ?? "Unknown provider"} / ${row.model ?? "Unknown model"}`,
				context: row.purpose ?? "All purposes",
				totals: row.totals,
				coverage: row.coverage,
			}));
		case "profile":
			return data.by_profile.items.map((row, index) => ({
				key:
					row.profile_id ??
					row.profile_fingerprint ??
					`${row.profile_name ?? "unknown"}-${index}`,
				name:
					row.profile_name ??
					row.profile_id ??
					row.profile_fingerprint ??
					"Unknown profile",
				context: [row.purpose, row.provider, row.model]
					.filter(Boolean)
					.join(" · "),
				totals: row.totals,
				coverage: row.coverage,
			}));
		case "operation":
			return data.by_operation.items.map((row, index) => ({
				key: `${row.operation_type ?? "unknown"}-${row.operation_id ?? index}`,
				name: `${row.operation_type ?? "Unknown operation"}: ${row.operation_id ?? "unassigned"}`,
				context: row.purpose ?? "Unknown purpose",
				totals: row.totals,
				coverage: row.coverage,
			}));
		case "organization":
			return data.by_organization.items.map((row, index) => ({
				key: row.organization_id ?? `global-${index}`,
				name: row.organization_name ?? "Global / no organization",
				totals: row.totals,
				coverage: row.coverage,
			}));
	}
}

export function TestingReviewBreakdown({
	data,
	isLoading,
	error,
	isFetching,
	onRetry,
	filters,
	onFiltersChange,
}: TestingReviewBreakdownProps) {
	const [openGroup, setOpenGroup] = useState<GroupKind>("purpose");

	if (isLoading) {
		return (
			<Card>
				<CardHeader>
					<Skeleton className="h-6 w-56" />
					<Skeleton className="h-4 w-80" />
				</CardHeader>
				<CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					{Array.from({ length: 4 }).map((_, index) => (
						<Skeleton key={index} className="h-20" />
					))}
				</CardContent>
			</Card>
		);
	}

	return (
		<Card>
			<CardHeader>
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<CardTitle>Testing & review breakdown</CardTitle>
						<CardDescription>
							Quality-gate usage by purpose, model, profile, operation, and
							organization.
						</CardDescription>
					</div>
					{isFetching && (
						<div className="flex items-center gap-2 text-sm text-muted-foreground">
							<RotateCw className="h-4 w-4 animate-spin" />
							Refreshing
						</div>
					)}
				</div>
			</CardHeader>
			<CardContent className="space-y-4">
				{Boolean(error) && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription className="flex flex-col items-start gap-3">
							<span>
								Testing and review usage could not be loaded. Try again to
								retrieve this breakdown.
							</span>
							<Button
								type="button"
								variant="outline"
								className="min-h-11 w-fit"
								disabled={isFetching}
								onClick={onRetry}
							>
								{isFetching ? "Retrying…" : "Retry breakdown"}
							</Button>
						</AlertDescription>
					</Alert>
				)}

				<section
					aria-label="Testing review filters"
					className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5"
				>
					{(
						[
							["purpose", "Purpose"],
							["provider", "Provider"],
							["model", "Model"],
							["profile_id", "Profile ID"],
							["profile_fingerprint", "Profile fingerprint"],
						] as const
					).map(([key, label]) => (
						<div key={key} className="space-y-1">
							<Label htmlFor={`quality-${key}`}>{label}</Label>
							<Input
								id={`quality-${key}`}
								value={filters[key] ?? ""}
								onChange={(event) =>
									onFiltersChange(
										updateTextFilter(filters, key, event.target.value),
									)
								}
							/>
						</div>
					))}
				</section>

				{data ? (
					<>
						<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
							<SummaryMetric
								label="Known cost"
								value={formatCurrency(data.overall.known_cost)}
								detail={`${formatNumber(data.overall.missing_cost_call_count)} calls missing cost`}
							/>
							<SummaryMetric
								label="Tokens"
								value={formatNumber(tokenTotal(data.overall))}
								detail={`${formatNumber(data.overall.input_tokens)} input · ${formatNumber(data.overall.output_tokens)} output`}
							/>
							<SummaryMetric
								label="Calls"
								value={formatNumber(data.overall.call_count)}
								detail={`${formatNumber(data.coverage.started_attempt_count)} started attempts`}
							/>
							<SummaryMetric
								label="Duration"
								value={formatDuration(data.overall.duration_ms)}
								detail={`${formatNumber(data.overall.duration_missing_count)} calls missing duration`}
							/>
							<SummaryMetric
								label="Cache read tokens"
								value={formatNumber(data.overall.cache_read_tokens)}
								detail={
									data.overall.input_tokens > 0
										? `Cached Input: ${(
												(data.overall.cache_read_tokens /
													data.overall.input_tokens) *
												100
											).toFixed(1)}%`
										: undefined
								}
							/>
							<SummaryMetric
								label="Cache write tokens"
								value={formatNumber(data.overall.cache_write_tokens)}
							/>
						</div>

						<CoverageLine coverage={data.coverage} />

						<div className="space-y-2">
							{(
								[
									["purpose", "Purpose"],
									["providerModel", "Provider / model"],
									["profile", "Profile"],
									["operation", "Operation"],
									["organization", "Organization"],
								] as const
							).map(([kind, label]) => (
								<div key={kind} className="rounded-[var(--bf-radius-card)] border">
									<Button
										type="button"
										variant="ghost"
										className="flex min-h-11 w-full justify-start gap-2 rounded-[var(--bf-radius-card)] px-3"
										onClick={() =>
											setOpenGroup((current) =>
												current === kind ? "purpose" : kind,
											)
										}
									>
										{openGroup === kind ? (
											<ChevronDown className="h-4 w-4" />
										) : (
											<ChevronRight className="h-4 w-4" />
										)}
										{label}
									</Button>
									{openGroup === kind && (
										<div className="overflow-auto border-t p-3">
											<GroupTable label={label} rows={groupRows(data, kind)} />
										</div>
									)}
								</div>
							))}
						</div>
					</>
				) : (
					!error && (
						<p className="text-sm text-muted-foreground">
							No testing or review usage found for these filters.
						</p>
					)
				)}
			</CardContent>
		</Card>
	);
}
