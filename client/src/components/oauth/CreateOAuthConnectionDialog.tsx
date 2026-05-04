import { useMemo } from "react";
import { Button } from "@/components/ui/button";
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
}

const FORM_ID = "oauth-connection-form";

export function CreateOAuthConnectionDialog({
	open,
	onOpenChange,
	integrationId,
	editConnectionName,
}: CreateOAuthConnectionDialogProps) {
	const isEditMode = !!editConnectionName;
	const createMutation = useCreateOAuthConnection();
	const updateMutation = useUpdateOAuthConnection();
	const { data: existingConnection } = useOAuthConnection(
		editConnectionName || "",
	) as { data?: OAuthConnectionDetail | undefined };

	const initialValues = useMemo<Partial<OAuthProviderData> | undefined>(() => {
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

	const handleSubmit = async (data: OAuthProviderData) => {
		if (isEditMode) {
			const updateData: UpdateOAuthConnectionRequest = {
				oauth_flow_type: data.oauth_flow_type,
				client_id: data.client_id,
				client_secret: data.client_secret || null,
				authorization_url: data.authorization_url || null,
				token_url: data.token_url,
				scopes: data.scopes as unknown as string[],
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
	};

	const isPending = createMutation.isPending || updateMutation.isPending;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
				<DialogHeader>
					<DialogTitle>
						{isEditMode
							? `Edit OAuth Connection: ${editConnectionName}`
							: `Configure OAuth for Integration`}
					</DialogTitle>
					<DialogDescription>
						{isEditMode
							? "Update OAuth 2.0 connection details"
							: "Set up OAuth 2.0 credentials for this integration"}
					</DialogDescription>
				</DialogHeader>

				<div className="mt-4">
					<OAuthProviderEditor
						flowType="authorization_code"
						initialValues={initialValues}
						onSubmit={handleSubmit}
						redirectUri={redirectUri}
						isEditMode={isEditMode}
						formId={FORM_ID}
						disabled={isPending}
					/>
				</div>

				<DialogFooter className="mt-6">
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isPending}
					>
						Cancel
					</Button>
					<Button type="submit" form={FORM_ID} disabled={isPending}>
						{isEditMode
							? updateMutation.isPending
								? "Updating..."
								: "Update Connection"
							: createMutation.isPending
								? "Creating..."
								: "Create Connection"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
