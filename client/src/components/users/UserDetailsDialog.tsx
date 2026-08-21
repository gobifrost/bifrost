import { UserCog, FileCode, Shield, Clock } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useUserRoles, useUserForms } from "@/hooks/useUsers";
import { useRoles } from "@/hooks/useRoles";
import { useOrganizationGroups, useOrganizations } from "@/hooks/useOrganizations";
import {
	organizationBoundary,
	useAdministrativeBoundary,
} from "@/hooks/useAdministrativeBoundary";
import { summarizeBoundaries } from "@/components/users/RoleAssignmentEditor";
import { formatDate, formatDateShort } from "@/lib/utils";
import type { components } from "@/lib/v1";
type User = components["schemas"]["UserPublic"];
type RoleAssignment = components["schemas"]["RoleAssignmentPublic"];
type UserFormsResponse = components["schemas"]["RoleFormsResponse"];

interface UserDetailsDialogProps {
	user?: User | undefined;
	open: boolean;
	onClose: () => void;
}

export function UserDetailsDialog({
	user,
	open,
	onClose,
}: UserDetailsDialogProps) {
	const administrativeBoundary =
		useAdministrativeBoundary("organizations.read");
	const { data: roles, isLoading: rolesLoading } = useUserRoles(
		user?.id,
		user ? organizationBoundary(user.organization_id) : undefined,
	);
	const { data: roleDefinitions = [] } = useRoles(administrativeBoundary);
	const { data: organizations = [] } = useOrganizations({ boundary: administrativeBoundary });
	const { data: organizationGroups = [] } = useOrganizationGroups({ boundary: administrativeBoundary });
	const { data: formsAccess, isLoading: formsLoading } = useUserForms(
		user?.id,
		user ? organizationBoundary(user.organization_id) : undefined,
	);

	if (!user) return null;

	return (
		<Dialog open={open} onOpenChange={onClose}>
			<DialogContent className="sm:max-w-[700px]">
				<DialogHeader>
					<DialogTitle>{user.name || user.email}</DialogTitle>
					<DialogDescription>
						{user.email} •{" "}
						{user.is_superuser
							? "Platform Administrator"
							: "Organization User"}
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4 mt-4">
					{/* User Info Card */}
					<Card>
						<CardHeader>
							<CardTitle className="text-base">
								User Information
							</CardTitle>
						</CardHeader>
						<CardContent className="space-y-3">
							<div className="flex items-center justify-between">
								<span className="text-sm text-muted-foreground">
									User Type
								</span>
								<Badge
									variant={
										user.is_superuser
											? "default"
											: "secondary"
									}
								>
									{user.is_superuser ? (
										<>
											<Shield className="mr-1 h-3 w-3" />
											Platform Admin
										</>
									) : (
										"Organization User"
									)}
								</Badge>
							</div>

							<div className="flex items-center justify-between">
								<span className="text-sm text-muted-foreground">
									Status
								</span>
								<Badge
									variant={
										user.is_active ? "default" : "secondary"
									}
								>
									{user.is_active ? "Active" : "Inactive"}
								</Badge>
							</div>

							<div className="flex items-center justify-between">
								<span className="text-sm text-muted-foreground">
									Last Login
								</span>
								<span className="text-sm flex items-center gap-1">
									<Clock className="h-3 w-3" />
									{user.last_login
										? formatDate(user.last_login)
										: "Never logged in"}
								</span>
							</div>

							<div className="flex items-center justify-between">
								<span className="text-sm text-muted-foreground">
									Created
								</span>
								<span className="text-sm">
									{user.created_at
										? formatDateShort(user.created_at)
										: "N/A"}
								</span>
							</div>
						</CardContent>
					</Card>

					{/* Role assignments are visible for every account; form access applies to org users. */}
					{user.organization_id && (
						<Tabs defaultValue="roles">
							<TabsList className={`grid w-full ${user.is_superuser ? "grid-cols-1" : "grid-cols-2"}`}>
								<TabsTrigger value="roles">
									<UserCog className="mr-2 h-4 w-4" />
									Roles
								</TabsTrigger>
								{!user.is_superuser ? (
									<TabsTrigger value="forms">
										<FileCode className="mr-2 h-4 w-4" />
										Form Access
									</TabsTrigger>
								) : null}
							</TabsList>

							<TabsContent value="roles" className="mt-4">
								<Card>
									<CardHeader>
										<CardTitle className="text-base">
											Assigned Roles
										</CardTitle>
										<CardDescription>
										Roles define what this user can do and where that access applies.
										</CardDescription>
									</CardHeader>
									<CardContent>
										{rolesLoading ? (
											<div className="space-y-2">
												{[...Array(2)].map((_, i) => (
													<Skeleton
														key={i}
														className="h-10 w-full"
													/>
												))}
											</div>
									) : roles && roles.length > 0 ? (
										<div className="space-y-2">
											{(roles as RoleAssignment[]).map(
												(assignment) => (
													<div
														key={assignment.id}
														className="flex items-center justify-between rounded-lg bg-muted/50 p-3 ring-1 ring-foreground/5"
													>
														<div>
															<p className="font-medium">
																{roleDefinitions.find((role) => role.id === assignment.role_id)?.name ?? "Unknown role"}
															</p>
															<p className="text-sm text-muted-foreground">
																{summarizeBoundaries(assignment.boundaries ?? [], {
																	organizations: new Map(organizations.map((org) => [org.id, org.name])),
																	organizationGroups: new Map(organizationGroups.map((group) => [group.id, group.name])),
																})}
															</p>
															</div>
														</div>
													),
												)}
											</div>
										) : (
											<div className="flex flex-col items-center justify-center py-8 text-center">
												<UserCog className="h-12 w-12 text-muted-foreground" />
												<p className="mt-2 text-sm text-muted-foreground">
													No roles assigned to this
													user
												</p>
											</div>
										)}
									</CardContent>
								</Card>
							</TabsContent>

							{!user.is_superuser ? <TabsContent value="forms" className="mt-4">
								<Card>
									<CardHeader>
										<CardTitle className="text-base">
											Form Access
										</CardTitle>
										<CardDescription>
											Forms this user can execute based on
											their roles
										</CardDescription>
									</CardHeader>
									<CardContent>
										{formsLoading ? (
											<div className="space-y-2">
												{[...Array(2)].map((_, i) => (
													<Skeleton
														key={i}
														className="h-10 w-full"
													/>
												))}
											</div>
										) : formsAccess &&
										  (formsAccess as UserFormsResponse)
												.form_ids ? (
											(formsAccess as UserFormsResponse)
												.form_ids.length > 0 ? (
												<div className="space-y-2">
													{(
														formsAccess as UserFormsResponse
													).form_ids.map(
														(formId: string) => (
															<div
																key={formId}
																className="rounded-lg bg-muted/50 p-3 ring-1 ring-foreground/5"
															>
																<p className="font-medium">
																	{formId}
																</p>
															</div>
														),
													)}
												</div>
											) : (
												<div className="flex flex-col items-center justify-center py-8 text-center">
													<FileCode className="h-12 w-12 text-muted-foreground" />
													<p className="mt-2 text-sm text-muted-foreground">
														No forms accessible to
														this user
													</p>
												</div>
											)
										) : null}
									</CardContent>
								</Card>
							</TabsContent> : null}
						</Tabs>
					)}

					{/* Platform admins have full access */}
					{user.is_superuser && (
						<Card>
							<CardHeader>
								<CardTitle className="text-base">
									Access Level
								</CardTitle>
							</CardHeader>
							<CardContent>
								<div className="rounded-lg bg-blue-50 p-4 ring-1 ring-blue-200 dark:bg-blue-950 dark:ring-blue-800">
									<p className="text-sm font-medium text-blue-900 dark:text-blue-100">
										Full Platform Access
									</p>
									<p className="text-sm text-blue-700 dark:text-blue-300">
										{user.is_superuser
											? "MSP Admin - Full access to all platform features"
											: "MSP Technician - Access to manage workflows and configurations"}
									</p>
								</div>
							</CardContent>
						</Card>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}
