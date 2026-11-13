import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
} from "@/components/ui/card";
import { Loader2, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { handleOAuthCallback } from "@/hooks/useOAuth";
import {
	EntityIdSourcePicker,
	type Candidate,
} from "@/components/integrations/EntityIdSourcePicker";
import { useSetEntityIdSource } from "@/services/integrations";

export function OAuthCallback() {
	const navigate = useNavigate();
	const { integrationId } = useParams<{ integrationId: string }>();
	const [searchParams] = useSearchParams();
	const [status, setStatus] = useState<
		"processing" | "success" | "error" | "warning" | "picker"
	>("processing");
	const [message, setMessage] = useState("Processing OAuth callback...");
	const [warning, setWarning] = useState<string | null>(null);
	const [pickerError, setPickerError] = useState<string | null>(null);
	const [pickerCandidates, setPickerCandidates] = useState<Candidate[]>([]);
	const [triggeringMappingId, setTriggeringMappingId] = useState<
		string | null
	>(null);
	const [capturedEntityId, setCapturedEntityId] = useState<string | null>(
		null,
	);
	const [capturedEntityIdFrom, setCapturedEntityIdFrom] = useState<
		string | null
	>(null);
	const hasProcessed = useRef(false);
	const setEntityIdSource = useSetEntityIdSource();

	const finishPicker = () => {
		if (window.opener) {
			window.opener.postMessage({ type: "oauth_success", integrationId }, window.location.origin);
			window.close();
		} else {
			navigate("/integrations", { replace: true });
		}
	};

	useEffect(() => {
		const handleCallback = async () => {
			// Prevent double-processing (React.StrictMode, refresh, etc.)
			if (hasProcessed.current) {
				return;
			}
			hasProcessed.current = true;
			if (!integrationId) {
				setStatus("error");
				setMessage("Missing integration ID in URL");
				return;
			}

			// Get query parameters from OAuth provider
			const code = searchParams.get("code");
			const error = searchParams.get("error");
			const errorDescription = searchParams.get("error_description");
			const state = searchParams.get("state");

			// Check for error from OAuth provider
			if (error) {
				setStatus("error");
				setMessage(
					`OAuth authorization failed: ${errorDescription || error}`,
				);
				return;
			}

			// Check for authorization code
			if (!code) {
				setStatus("error");
				setMessage("Missing authorization code from OAuth provider");
				return;
			}

			try {
				// Send the authorization code to the API for token exchange
				// Include redirect_uri - must match what was sent during authorization
				const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;
				const response = await handleOAuthCallback(
					integrationId,
					code,
					state,
					redirectUri,
				);
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				const responseData = response as any; // Response may include error_message or warning_message

				// Check for error_message
				if (
					responseData &&
					typeof responseData === "object" &&
					"error_message" in responseData &&
					responseData.error_message
				) {
					setStatus("error");
					setMessage(responseData.error_message as string);
					// DO NOT auto-close - user must manually close after reading error
					return;
				}

				// Check for warning_message (e.g., no refresh token)
				if (
					responseData &&
					typeof responseData === "object" &&
					"warning_message" in responseData &&
					responseData.warning_message
				) {
					setWarning(responseData.warning_message as string);
					setStatus("warning");
					setMessage("Connection established with limitations");

					// Notify parent window to refresh connections (even with warning)
					if (window.opener) {
						window.opener.postMessage(
							{
								type: "oauth_success",
								integrationId,
							},
							window.location.origin,
						);
					}

					// DO NOT auto-close - user must manually close after reading warning
					return;
				}

				// If the backend surfaced picker candidates, show them BEFORE
				// closing — the admin picks the entity_id field, we PATCH it,
				// then close. Skipping just closes (picker reappears next connect).
				const picker = (responseData?.entity_id_picker ?? null) as
					Candidate[] | null;
				if (picker && picker.length > 0) {
					setPickerCandidates(picker);
					setTriggeringMappingId(
						(responseData?.triggering_mapping_id as
							string | null | undefined) ?? null,
					);
					setStatus("picker");
					setMessage("Connection established. Choose an entity ID source below.");
					return;
				}

				// No warning or error - proceed with normal success flow
				setStatus("success");
				setMessage("OAuth connection completed successfully!");

				// Capture confirmation: when the backend filled an Entity ID
				// via the provider's configured source, show it and require
				// manual close so the admin sees what landed in the mapping.
				const captured =
					(responseData?.captured_entity_id as
						string | null | undefined) ?? null;
				const capturedFrom =
					(responseData?.captured_entity_id_from as
						string | null | undefined) ?? null;
				if (captured) {
					setCapturedEntityId(captured);
					setCapturedEntityIdFrom(capturedFrom);
				}

				// Notify parent window to refresh connections
				if (window.opener) {
					window.opener.postMessage(
						{
							type: "oauth_success",
							integrationId,
						},
						window.location.origin,
					);
				}

				// Auto-close only when there's nothing for the admin to see.
				// When we captured an Entity ID, leave the popup open so the
				// admin can confirm the value before closing.
				if (!captured) {
					setTimeout(() => {
						if (window.opener) window.close();
						else navigate("/integrations", { replace: true });
					}, 1500);
				}
			} catch (err: unknown) {
				setStatus("error");
				const errorMsg =
					(err as Error).message ||
					"Failed to complete OAuth connection";

				// Provide helpful message for common errors
				if (
					errorMsg.includes("already been redeemed") ||
					errorMsg.includes("already been used")
				) {
					setMessage(
						"This authorization has already been processed. You can close this window.",
					);
					// Auto-close since this is likely a refresh/duplicate
					setTimeout(() => {
						window.close();
					}, 2000);
				} else {
					setMessage(errorMsg);
				}
			}
		};

		void handleCallback();
	}, [integrationId, searchParams, navigate]);

	return (
		<div className="flex items-center justify-center min-h-svh bg-background px-4 py-8">
			<Card className="max-w-md w-full rounded-[var(--bf-radius-feature)]">
				<CardHeader>
					<div className="flex items-start gap-3">
						{status === "processing" && (
							<Loader2 className="h-6 w-6 shrink-0 animate-spin motion-reduce:animate-none text-primary" />
						)}
						{status === "success" && (
							<CheckCircle2 className="h-6 w-6 shrink-0 text-[var(--bf-success)]" />
						)}
						{status === "warning" && (
							<AlertTriangle className="h-6 w-6 shrink-0 text-[var(--bf-warning)]" />
						)}
						{status === "error" && (
							<XCircle className="h-6 w-6 shrink-0 text-destructive" />
						)}
						<h1 className="font-display text-2xl font-semibold tracking-tight">
							{status === "processing" &&
								"Processing OAuth Callback"}
							{status === "success" && "Authorization Successful"}
							{status === "warning" && "Warning"}
							{status === "error" && "Authorization Failed"}
							{status === "picker" && "Authorization Successful"}
						</h1>
					</div>
					<CardDescription>
						Integration:{" "}
						<code className="font-mono [overflow-wrap:anywhere]">
							{integrationId}
						</code>
					</CardDescription>
				</CardHeader>
				<CardContent>
					<p role={status === "error" ? "alert" : "status"} className="text-sm text-muted-foreground mb-4 [overflow-wrap:anywhere]">
						{message}
					</p>

					{/* Warning state */}
					{status === "warning" && warning && (
						<>
							<p role="alert" className="text-sm mb-4 [overflow-wrap:anywhere]">{warning}</p>
							<div className="flex justify-center">
								<Button
									onClick={() => {
										if (window.opener) window.close();
										else navigate("/integrations");
									}}
									variant="default"
									className="min-h-11 w-full sm:w-auto"
								>
									{window.opener
										? "Close"
										: "Return to integrations"}
								</Button>
							</div>
						</>
					)}

					{/* Success state */}
					{status === "success" && capturedEntityId && (
						<>
							<div className="rounded-md bg-muted/50 p-3 mb-4 ring-1 ring-foreground/5">
								<p className="text-xs text-muted-foreground mb-1">
									Captured Entity ID
								</p>
								<p className="font-mono text-sm break-all">
									{capturedEntityId}
								</p>
								{capturedEntityIdFrom && (
									<p className="text-xs text-muted-foreground mt-2">
										from{" "}
										<code className="font-mono [overflow-wrap:anywhere]">
											{capturedEntityIdFrom}
										</code>
									</p>
								)}
							</div>
							<div className="flex justify-center">
								<Button
									onClick={() => {
										if (window.opener) window.close();
										else navigate("/integrations");
									}}
									variant="default"
									className="min-h-11 w-full sm:w-auto"
								>
									{window.opener
										? "Close"
										: "Return to integrations"}
								</Button>
							</div>
						</>
					)}
					{status === "success" && !capturedEntityId && (
						<p className="text-xs text-muted-foreground">
							{window.opener ? "This window will close automatically…" : "Returning to integrations…"}
						</p>
					)}

					{/* Error state - manual close */}
					{status === "error" && (
						<div className="flex justify-center">
							<Button
								onClick={() => {
									if (window.opener) window.close();
									else navigate("/integrations");
								}}
								variant="outline"
								className="min-h-11 w-full sm:w-auto"
							>
								{window.opener
									? "Close"
									: "Return to integrations"}
							</Button>
						</div>
					)}

					{/* Picker state - admin picks entity_id source */}
					{status === "picker" && (
						<EntityIdSourcePicker
							candidates={pickerCandidates}
							isPending={setEntityIdSource.isPending}
							error={pickerError}
							onSkip={finishPicker}
							onSelect={(candidate) => {
								if (!integrationId || setEntityIdSource.isPending) return;
								setPickerError(null);
								setEntityIdSource.mutate(
									{
										params: {
											path: {
												integration_id: integrationId,
											},
										},
										body: {
											type: candidate.type,
											key: candidate.key,
											apply_to_mapping_id:
												triggeringMappingId,
											apply_value: candidate.value,
										},
									},
									{
										onSuccess: finishPicker,
										onError: () => setPickerError("Could not save the entity ID source. Your selection is preserved. Try again or skip setup."),
									},
								);
							}}
						/>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
