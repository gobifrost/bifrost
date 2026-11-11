import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	useCreateOAuthConnection,
	useUpdateOAuthConnection,
	useOAuthConnection,
} from "@/hooks/useOAuth";
import {
	OAuthProviderEditor,
	type OAuthProviderData,
} from "@/components/oauth/OAuthProviderEditor";
import type { components } from "@/lib/v1";
import { IntegrationSetupNotesContext } from "@/components/integrations/IntegrationSetupNotes";

type CreateOAuthConnectionRequest =
	components["schemas"]["CreateOAuthConnectionRequest"];
type UpdateOAuthConnectionRequest =
	components["schemas"]["UpdateOAuthConnectionRequest"];
type OAuthConnectionDetail = components["schemas"]["OAuthConnectionDetail"];

interface CreateOAuthConnectionDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	integrationId: string;
	editConnectionName?: string | undefined;
	notes?: string | null;
}

const FORM_ID = "oauth-connection-form";

export function CreateOAuthConnectionDialog({
	open,
	onOpenChange,
	integrationId,
	editConnectionName,
	notes,
}: CreateOAuthConnectionDialogProps) {
	const isEditMode = !!editConnectionName;
	const createMutation = useCreateOAuthConnection();
	const updateMutation = useUpdateOAuthConnection();
	const {
		data: existingConnection,
		isLoading: connectionLoading,
		error: connectionError,
		refetch: refetchConnection,
	} = useOAuthConnection(editConnectionName || "") as {
		data?: OAuthConnectionDetail | undefined;
		isLoading: boolean;
		error: unknown;
		refetch: () => Promise<unknown>;
	};
	const [submitError, setSubmitError] = useState<string | null>(null);

	const initialValues = useMemo<
		Partial<OAuthProviderData> | undefined
	>(() => {
		if (isEditMode && existingConnection) {
			// Backend can return a third flow type ("refresh_token") on legacy
			// records — narrow to the two flows the editor supports; anything
			// else falls through as authorization_code.
			const flow =
				existingConnection.oauth_flow_type === "client_credentials"
					? "client_credentials"
					: "authorization_code";
			return {
				oauth_flow_type: flow,
				client_id: existingConnection.client_id,
				client_secret: "", // Don't populate for security
				authorization_url: existingConnection.authorization_url ?? "",
				token_url: existingConnection.token_url,
				scopes: existingConnection.scopes || "",
				audience: existingConnection.audience || "",
			};
		}
		return undefined;
	}, [isEditMode, existingConnection]);

	const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;

	const getErrorMessage = (error: unknown, fallback: string) =>
		typeof error === "object" && error !== null && "detail" in error
			? String((error as { detail: unknown }).detail)
			: error instanceof Error
				? error.message
				: fallback;

	const normalizeScopesForUpdate = (scopes: string) => {
		const normalized = scopes
			.split(/[,\s]+/)
			.map((scope) => scope.trim())
			.filter(Boolean);
		return normalized.length ? normalized : null;
	};

	const handleSubmit = async (data: OAuthProviderData) => {
		setSubmitError(null);
		try {
			if (isEditMode) {
				const updateData: UpdateOAuthConnectionRequest = {
					oauth_flow_type: data.oauth_flow_type,
					client_id: data.client_id,
					client_secret: data.client_secret || null,
					authorization_url: data.authorization_url || null,
					token_url: data.token_url,
					scopes: normalizeScopesForUpdate(data.scopes),
					audience: data.audience || null,
				};

				await updateMutation.mutateAsync({
					params: { path: { connection_name: editConnectionName! } },
					body: updateData,
				});
			} else {
				const createData: CreateOAuthConnectionRequest = {
					description: "",
					oauth_flow_type: data.oauth_flow_type,
					client_id: data.client_id,
					client_secret: data.client_secret,
					authorization_url: data.authorization_url ?? "",
					token_url: data.token_url,
					scopes: data.scopes,
					integration_id: integrationId,
					audience: data.audience ?? "",
				};

				await createMutation.mutateAsync({ body: createData });
			}

			onOpenChange(false);
		} catch (error) {
			setSubmitError(
				getErrorMessage(
					error,
					isEditMode
						? "Failed to update OAuth connection. Your changes are still here."
						: "Failed to create OAuth connection. Your changes are still here.",
				),
			);
		}
	};

	const isPending = createMutation.isPending || updateMutation.isPending;
	const isLoadingExisting =
		isEditMode && connectionLoading && !existingConnection;
	const existingConnectionError = isEditMode ? connectionError : null;

	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!nextOpen && !isPending) onOpenChange(nextOpen);
			}}
		>
			<DialogContent
				className={`flex max-h-[calc(100dvh-1rem)] flex-col ${notes ? "w-[min(70rem,calc(100vw-1rem))]" : "w-[min(56rem,calc(100vw-1rem))]"} max-w-none overflow-hidden rounded-[var(--bf-radius-surface)] p-0`}
				showCloseButton={!isPending}
				onEscapeKeyDown={(event) => {
					if (isPending) event.preventDefault();
				}}
				onInteractOutside={(event) => {
					if (isPending) event.preventDefault();
				}}
			>
				<div className="shrink-0 border-b border-border/70 px-4 py-4 sm:px-6">
					<DialogHeader className="space-y-2">
						<DialogTitle className="pr-10 text-base font-semibold [overflow-wrap:anywhere] sm:text-lg">
							{isEditMode
								? "Edit OAuth Connection"
								: "Configure OAuth for Integration"}
						</DialogTitle>
						<DialogDescription className="text-sm leading-6 text-muted-foreground">
							{isEditMode
								? "Update OAuth 2.0 connection details."
								: "Set up OAuth 2.0 credentials for this integration."}
						</DialogDescription>
					</DialogHeader>
				</div>
				<div className="min-h-0 flex-1 overflow-y-auto">
					{isLoadingExisting ? (
						<div className="px-4 py-6 sm:px-6">
							<div className="flex items-center gap-3 rounded-[var(--bf-radius-surface)] border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
								<Loader2
									aria-hidden="true"
									className="size-4 animate-spin motion-reduce:animate-none"
								/>
								<span>Loading connection details…</span>
							</div>
						</div>
					) : existingConnectionError ? (
						<div className="px-4 py-6 sm:px-6">
							<Alert variant="destructive">
								<AlertTitle>
									Could not load the existing connection
								</AlertTitle>
								<AlertDescription className="space-y-3">
									<p>
										{getErrorMessage(
											existingConnectionError,
											"Try loading the connection again.",
										)}
									</p>
									<Button
										type="button"
										variant="outline"
										className="h-11"
										onClick={() => {
											void refetchConnection();
										}}
									>
										Retry loading connection
									</Button>
								</AlertDescription>
							</Alert>
						</div>
					) : (
						<div className="px-4 py-4 sm:px-6">
							<IntegrationSetupNotesContext notes={notes}>
							<OAuthProviderEditor
								flowType="authorization_code"
								initialValues={initialValues}
								onSubmit={handleSubmit}
								redirectUri={redirectUri}
								isEditMode={isEditMode}
								formId={FORM_ID}
								disabled={isPending}
							/>
							</IntegrationSetupNotesContext>
						</div>
					)}
				</div>
				{submitError ? (
					<Alert
						variant="destructive"
						className="mx-4 mb-4 w-auto shrink-0 sm:mx-6"
					>
						<AlertTitle>
							{isEditMode
								? "Could not update the connection"
								: "Could not create the connection"}
						</AlertTitle>
						<AlertDescription>{submitError}</AlertDescription>
					</Alert>
				) : null}
				{!isLoadingExisting && !existingConnectionError && (
					<DialogFooter className="shrink-0 border-t border-border/70 bg-popover px-4 py-4 sm:px-6">
						<div className="flex w-full flex-col-reverse gap-2 sm:flex-row sm:justify-end">
							<Button
								type="button"
								variant="outline"
								onClick={() => onOpenChange(false)}
								disabled={isPending}
								className="min-h-11 w-full sm:w-auto"
							>
								Cancel
							</Button>
							<Button
								type="submit"
								form={FORM_ID}
								disabled={isPending}
								className="min-h-11 w-full sm:w-auto"
							>
								{isEditMode
									? updateMutation.isPending
										? "Updating..."
										: "Update Connection"
									: createMutation.isPending
										? "Creating..."
										: "Create Connection"}
							</Button>
						</div>
					</DialogFooter>
				)}
			</DialogContent>
		</Dialog>
	);
}
