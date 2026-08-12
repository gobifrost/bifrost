import { useOAuthRefreshJobStatus } from "@/hooks/useOAuth";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import { CheckCircle2, XCircle, FileText, AlertCircle } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";

interface RefreshJobStatusProps {
	className?: string;
}

export function RefreshJobStatus({ className }: RefreshJobStatusProps) {
	const { data: jobStatus, isLoading } = useOAuthRefreshJobStatus();

	if (isLoading) {
		return (
			<Card className={className}>
				<CardHeader className="pb-3">
					<CardTitle className="text-sm font-medium">
						Token Refresh Job
					</CardTitle>
					<CardDescription>Loading status...</CardDescription>
				</CardHeader>
			</Card>
		);
	}

	if (!jobStatus) {
		return (
			<Card className={className}>
				<CardHeader className="pb-3">
					<CardTitle className="text-sm font-medium">
						Token Refresh Job
					</CardTitle>
					<CardDescription>No job runs yet</CardDescription>
				</CardHeader>
				<CardContent>
					<p className="text-xs text-muted-foreground">
						The automatic token refresh job runs every 15 minutes.
					</p>
				</CardContent>
			</Card>
		);
	}

	// Check if this is an error result (job itself failed, not individual connection failures)
	const isError = jobStatus.error != null;
	const hasFailures = jobStatus.refresh_failed > 0;
	const isSuccess = !isError && !hasFailures;

	if (isError) {
		return (
			<Card className={className}>
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<CardTitle className="text-sm font-medium">
							Token Refresh Job
						</CardTitle>
						<Badge variant="destructive" className="text-xs">
							Error
						</Badge>
					</div>
					<CardDescription>
						{jobStatus.updated_at
							? formatDistanceToNow(
									new Date(jobStatus.updated_at),
									{
										addSuffix: true,
									},
								)
							: "Unknown time"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="flex items-start gap-2 text-sm text-destructive">
						<XCircle className="h-4 w-4 mt-0.5 shrink-0" />
						<span className="text-xs">{jobStatus.error}</span>
					</div>
				</CardContent>
			</Card>
		);
	}

	return (
		<Card className={className}>
			<CardHeader className="pb-2">
				<div className="flex items-center justify-between">
					<CardTitle className="text-sm font-medium">
						Token Refresh Job
					</CardTitle>
					<Badge
						variant={hasFailures ? "destructive" : "outline"}
						className={cn(
							"text-xs",
							isSuccess &&
								"border-green-600 text-green-600 bg-green-600/10",
						)}
					>
						{isSuccess ? (
							<>
								<CheckCircle2 className="h-3 w-3 mr-1" />
								OK
							</>
						) : (
							<>
								<AlertCircle className="h-3 w-3 mr-1" />
								{jobStatus.refresh_failed} Failed
							</>
						)}
					</Badge>
				</div>
				<CardDescription>
					{formatDistanceToNow(new Date(jobStatus.updated_at + "Z"), {
						addSuffix: true,
					})}
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-3">
				{/* Summary Stats */}
				<div className="flex items-center gap-4 text-sm">
					<div>
						<span className="text-muted-foreground">Total: </span>
						<span className="font-medium">
							{jobStatus.total_connections}
						</span>
					</div>
					{jobStatus.refreshed_successfully > 0 && (
						<div>
							<span className="text-muted-foreground">
								Refreshed:{" "}
							</span>
							<span className="font-medium text-green-600">
								{jobStatus.refreshed_successfully}
							</span>
						</div>
					)}
				</div>

				{/* View Details Button */}
				<Dialog>
					<DialogTrigger asChild>
						<Button
							variant="ghost"
							size="sm"
							className="w-full h-8 text-xs"
						>
							<FileText className="mr-2 h-3 w-3" />
							View Logs
						</Button>
					</DialogTrigger>
					<DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
						<DialogHeader>
							<DialogTitle>Token Refresh Job Logs</DialogTitle>
							<DialogDescription>
								Last run:{" "}
								{new Date(
									jobStatus.updated_at + "Z",
								).toLocaleString()}
							</DialogDescription>
						</DialogHeader>

						<div className="space-y-4 mt-4">
							{/* Summary */}
							<div>
								<h4 className="font-semibold mb-2">Summary</h4>
								<p className="text-sm text-muted-foreground">
									Found {jobStatus.total_connections}{" "}
									connection
									{jobStatus.total_connections !== 1
										? "s"
										: ""}
									.
									{jobStatus.needs_refresh > 0 ? (
										<>
											{" "}
											{jobStatus.needs_refresh} needed
											refresh.
											{jobStatus.refreshed_successfully >
												0 && (
												<span className="text-green-600">
													{" "}
													Refreshed{" "}
													{
														jobStatus.refreshed_successfully
													}{" "}
													successfully.
												</span>
											)}
											{jobStatus.refresh_failed > 0 && (
												<span className="text-destructive">
													{" "}
													Failed to refresh{" "}
													{jobStatus.refresh_failed}.
												</span>
											)}
										</>
									) : (
										" All tokens are up to date."
									)}
								</p>
							</div>

							{/* Errors */}
							{hasFailures &&
								jobStatus.errors &&
								jobStatus.errors.length > 0 && (
									<div>
										<h4 className="font-semibold mb-2 flex items-center gap-2">
											<XCircle className="h-4 w-4 text-destructive" />
											Errors ({jobStatus.errors.length})
										</h4>
										<div className="space-y-3">
											{jobStatus.errors.map(
												(error, index) => (
													<div
														key={index}
														className="rounded-lg bg-destructive/5 p-3 ring-1 ring-destructive/20"
													>
														<div className="flex items-start gap-2">
															<div className="flex-1">
																<p className="text-sm text-muted-foreground">
																	{typeof error === "string"
																		? error
																		: JSON.stringify(error)}
																</p>
															</div>
														</div>
													</div>
												),
											)}
										</div>
									</div>
								)}

							{/* Success Message */}
							{!hasFailures && jobStatus.needs_refresh > 0 && (
								<div className="rounded-lg bg-green-50 p-4 ring-1 ring-green-200 dark:bg-green-950/30 dark:ring-green-900">
									<div className="flex items-center gap-2">
										<CheckCircle2 className="h-5 w-5 text-green-600" />
										<p className="text-sm font-medium text-green-900 dark:text-green-100">
											All tokens refreshed successfully!
										</p>
									</div>
								</div>
							)}
						</div>
					</DialogContent>
				</Dialog>
			</CardContent>
		</Card>
	);
}
