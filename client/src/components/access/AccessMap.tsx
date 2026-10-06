import { KeyRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import type {
	AccessGrant,
	AccessRow,
	PermissionCatalogEntry,
} from "@/services/access";

import { BRIDGE_EDGE, PermissionChip } from "./PermissionChip";
import { PlaceLabel } from "./PlaceLabel";

const WILDCARD = "*";
/** Where a grant goes when the catalog can't name its area. */
const UNSORTED = "Permissions";

interface Layout {
	columns: string[];
	areaOf: (grant: AccessGrant) => string;
	entryFor: (grant: AccessGrant) => PermissionCatalogEntry | undefined;
}

/** Columns are the catalog's areas, in catalog order, that hold a grant. */
function layoutFor(
	rows: AccessRow[],
	catalog: PermissionCatalogEntry[] | undefined,
): Layout {
	const byDomain = new Map(catalog?.map((entry) => [entry.domain, entry]));
	const entryFor = (grant: AccessGrant) => byDomain.get(grant.domain);
	const areaOf = (grant: AccessGrant): string =>
		entryFor(grant)?.area ?? UNSORTED;
	const held = new Set(
		rows.flatMap((row) =>
			row.grants
				.filter((grant) => grant.permission !== WILDCARD)
				.map(areaOf),
		),
	);
	const order = [...new Set(catalog?.map((entry) => entry.area)), UNSORTED];
	const columns = order.filter((area) => held.has(area));
	return {
		columns: columns.length > 0 ? columns : [UNSORTED],
		areaOf,
		entryFor,
	};
}

/** A Platform Admin's wildcard: one privileged chip across the whole row. */
function WildcardChip() {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Badge asChild variant="warning">
					<button
						type="button"
						data-variant="privileged"
						className={cn(
							"h-auto min-h-6 w-full cursor-default justify-start",
							BRIDGE_EDGE,
						)}
					>
						Every permission
					</button>
				</Badge>
			</TooltipTrigger>
			<TooltipContent>
				Platform Admin: every permission in every organization (secret
				values excepted)
			</TooltipContent>
		</Tooltip>
	);
}

function Chips({
	grants,
	row,
	layout,
}: {
	grants: AccessGrant[];
	row: AccessRow;
	layout: Layout;
}) {
	if (grants.length === 0)
		return (
			<>
				<span aria-hidden="true" className="text-muted-foreground/60">
					—
				</span>
				<span className="sr-only">None</span>
			</>
		);
	return (
		<div className="flex flex-wrap gap-1.5">
			{grants.map((grant) =>
				grant.permission === WILDCARD ? (
					<WildcardChip key={grant.permission} />
				) : (
					<PermissionChip
						key={grant.permission}
						grant={grant}
						catalogEntry={layout.entryFor(grant)}
						place={row.place}
					/>
				),
			)}
		</div>
	);
}

function DesktopMap({ rows, layout }: { rows: AccessRow[]; layout: Layout }) {
	return (
		<DataTable>
			<caption className="sr-only">Access by place</caption>
			<DataTableHeader>
				<DataTableRow className="hover:bg-transparent">
					<DataTableHead className="w-56">Place</DataTableHead>
					{layout.columns.map((area) => (
						<DataTableHead key={area}>{area}</DataTableHead>
					))}
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{rows.map((row) => {
					const wildcard = row.grants.some(
						(grant) => grant.permission === WILDCARD,
					);
					return (
						<DataTableRow
							key={`${row.place.kind}:${row.place.organization_id}`}
							className="hover:bg-transparent"
						>
							<DataTableHead
								scope="row"
								className="h-auto whitespace-normal py-3 align-top text-[var(--bf-reach)]"
							>
								<PlaceLabel place={row.place} />
							</DataTableHead>
							{wildcard ? (
								<DataTableCell
									colSpan={layout.columns.length}
									className="align-top"
								>
									<Chips
										grants={row.grants}
										row={row}
										layout={layout}
									/>
								</DataTableCell>
							) : (
								layout.columns.map((area) => (
									<DataTableCell
										key={area}
										className="align-top"
									>
										<Chips
											grants={row.grants.filter(
												(grant) =>
													layout.areaOf(grant) ===
													area,
											)}
											row={row}
											layout={layout}
										/>
									</DataTableCell>
								))
							)}
						</DataTableRow>
					);
				})}
			</DataTableBody>
		</DataTable>
	);
}

function MobileMap({ rows, layout }: { rows: AccessRow[]; layout: Layout }) {
	return (
		<ul
			aria-label="Access by place"
			className="divide-y divide-border/60 overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card"
		>
			{rows.map((row) => {
				const wildcard = row.grants.filter(
					(grant) => grant.permission === WILDCARD,
				);
				const groups = layout.columns
					.map((area) => ({
						area,
						grants: row.grants.filter(
							(grant) =>
								grant.permission !== WILDCARD &&
								layout.areaOf(grant) === area,
						),
					}))
					.filter((group) => group.grants.length > 0);
				return (
					<li
						key={`${row.place.kind}:${row.place.organization_id}`}
						className="space-y-3 px-4 py-3"
					>
						<h3 className="text-sm font-medium text-[var(--bf-reach)]">
							<PlaceLabel place={row.place} />
						</h3>
						{wildcard.length > 0 && (
							<Chips
								grants={wildcard}
								row={row}
								layout={layout}
							/>
						)}
						{groups.map((group) => (
							<div key={group.area} className="space-y-1.5">
								<p className="text-xs text-muted-foreground">
									{group.area}
								</p>
								<Chips
									grants={group.grants}
									row={row}
									layout={layout}
								/>
							</div>
						))}
					</li>
				);
			})}
		</ul>
	);
}

const SWATCH = "relative h-3 w-5 shrink-0 rounded-sm";

/** How to read the chips: scope by edge, privilege by tone. */
function Legend() {
	return (
		<ul
			aria-label="How to read permissions"
			className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
		>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)]`}
				/>
				Per organization
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)] before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:rounded-l-sm before:bg-[image:var(--bf-bridge-vertical)]`}
				/>
				Platform-wide
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)] text-[var(--bf-power)] before:absolute before:inset-y-0.5 before:left-0 before:border-l-2 before:border-dashed before:border-current`}
				/>
				Varies by operation
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-warning-soft)] ring-1 ring-[var(--bf-warning)]/40`}
				/>
				Privileged
			</li>
		</ul>
	);
}

/**
 * What a person can do, and where: one row per place their roles apply,
 * one column per permission area they hold. Narrow screens get one record
 * per place instead of a sideways-scrolling grid.
 */
export function AccessMap({
	rows,
	catalog,
}: {
	rows: AccessRow[];
	catalog?: PermissionCatalogEntry[];
}) {
	const narrow = useMediaQuery("(max-width: 1023px)");
	if (rows.length === 0)
		return (
			<EmptyState
				icon={KeyRound}
				title="No permissions yet"
				description="Their roles don't grant anything anywhere. Assign a role to give them access."
			/>
		);
	const layout = layoutFor(rows, catalog);
	return (
		<div className="space-y-3">
			<Legend />
			{narrow ? (
				<MobileMap rows={rows} layout={layout} />
			) : (
				<DesktopMap rows={rows} layout={layout} />
			)}
		</div>
	);
}
