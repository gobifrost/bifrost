import { useState } from "react";
import { AlertCircle, Users } from "lucide-react";
import { Link } from "react-router-dom";

import { IdentityName } from "@/components/identities/IdentityName";
import { ListPagination } from "@/components/pagination/ListPagination";
import { SearchBox } from "@/components/search/SearchBox";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoleUsersPage } from "@/hooks/useRoles";
import { getErrorMessage } from "@/lib/api-error";
import { placeLabel } from "@/lib/role-boundaries";

const PAGE_SIZE = 25;

/**
 * Who holds a built-in role, and where it applies for each of them.
 * Read-only: built-in roles are given from a person's Role Assignments.
 * The server lists only people in organizations where the caller can view
 * role assignments.
 */
export function RolePeoplePanel({ roleId }: { roleId: string }) {
	const [search, setSearch] = useState("");
	const [offset, setOffset] = useState(0);
	const query = useRoleUsersPage(roleId, {
		search,
		limit: PAGE_SIZE,
		offset,
	});
	const users = query.data?.users ?? [];

	return (
		<section aria-labelledby="role-people-heading" className="space-y-3">
			<div className="space-y-1">
				<h2
					id="role-people-heading"
					className="text-base font-semibold"
				>
					People with This Role
				</h2>
				<p className="text-xs text-muted-foreground">
					Give or remove this role from a person's Role Assignments.
				</p>
			</div>
			<SearchBox
				value={search}
				onChange={(value) => {
					setSearch(value);
					setOffset(0);
				}}
				placeholder="Search people by name or email..."
				className="w-full sm:max-w-sm"
			/>
			{query.isError && !query.data ? (
				<div className="space-y-3">
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							{getErrorMessage(
								query.error,
								"People could not be loaded.",
							)}
						</AlertDescription>
					</Alert>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={query.isFetching}
						onClick={() => void query.refetch()}
					>
						Retry People
					</Button>
				</div>
			) : query.isLoading ? (
				<div
					role="status"
					aria-label="Loading people"
					className="space-y-2"
				>
					<Skeleton className="h-12 w-full" />
					<Skeleton className="h-12 w-full" />
				</div>
			) : users.length === 0 ? (
				<EmptyState
					icon={Users}
					title={
						search
							? "No one with this role matches your search."
							: "No one has this role yet."
					}
				/>
			) : (
				<div className="rounded-[var(--bf-radius-surface)] border bg-card">
					<ul aria-label="People with This Role" className="divide-y">
						{users.map((person) => (
							<li
								key={person.id}
								className="flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between"
							>
								{person.identity_kind ? (
									<Link
										to={`/users/${person.id}`}
										className="min-w-0 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
									>
										<IdentityName
											identity={{
												name:
													person.name || person.email,
												organization_name:
													person.organization_name ??
													null,
											}}
										/>
									</Link>
								) : (
									<div className="min-w-0 space-y-0.5">
										<Link
											to={`/users/${person.id}`}
											className="font-medium [overflow-wrap:anywhere] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
										>
											{person.name || person.email}
										</Link>
										<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
											{person.email}
											{" · "}
											{person.organization_name ??
												"Global"}
										</p>
									</div>
								)}
								<ul
									aria-label={`Where it applies for ${person.name || person.email}`}
									className="flex flex-wrap gap-1.5 sm:justify-end"
								>
									{(person.boundaries ?? []).map(
										(boundary) => {
											const label = placeLabel(
												boundary.kind,
												boundary.organization_name ??
													"an organization you can't see",
											);
											return (
												<li key={label}>
													<Badge
														variant="secondary"
														className="h-auto whitespace-normal py-1"
													>
														{label}
													</Badge>
												</li>
											);
										},
									)}
								</ul>
							</li>
						))}
					</ul>
					<ListPagination
						offset={offset}
						limit={PAGE_SIZE}
						total={query.data?.total ?? 0}
						isFetching={query.isFetching}
						onPageChange={setOffset}
					/>
				</div>
			)}
		</section>
	);
}
