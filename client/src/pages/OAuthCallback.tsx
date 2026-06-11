import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
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
					| Candidate[]
					| null;
				if (picker && picker.length > 0) {
					setPickerCandidates(picker);
					setTriggeringMappingId(
						(responseData?.triggering_mapping_id as
							| string
							| null
							| undefined) ?? null,
					);
					setStatus("picker");
					return;
				}

				// No warning or error - proceed with normal success flow
				setStatus("success");
				setMessage("OAuth connection completed successfully!");

				// Capture confirmation: when the backend filled an Entity ID
				// via the provider's configured source, show it and require
				// manual close so the admin sees what landed in the mapping.
				const captured =
					(responseData?.captured_entity_id as string | null | undefined) ??
					null;
				const capturedFrom =
					(responseData?.captured_entity_id_from as
						| string
						| null
						| undefined) ?? null;
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
						window.close();
						setTimeout(() => {
							navigate("/integrations");
						}, 100);
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

		handleCallback();
	}, [integrationId, searchParams, navigate]);

	return (
		<div className="flex items-center justify-center min-h-screen bg-background p-4">
			<Card className="max-w-md w-full hover:!transform-none">
				<CardHeader>
					<div className="flex items-center gap-2">
						{status === "processing" && (
							<Loader2 className="h-6 w-6 animate-spin text-blue-500" />
						)}
						{status === "success" && (
							<CheckCircle2 className="h-6 w-6 text-green-500" />
						)}
						{status === "warning" && (
							<AlertTriangle className="h-6 w-6 text-yellow-600" />
						)}
						{status === "error" && (
							<XCircle className="h-6 w-6 text-red-500" />
						)}
						<CardTitle>
							{status === "processing" &&
								"Processing OAuth Callback"}
							{status === "success" && "Authorization Successful"}
							{status === "warning" && "Warning"}
							{status === "error" && "Authorization Failed"}
							{status === "picker" && "Authorization Successful"}
						</CardTitle>
					</div>
					<CardDescription>
						Integration:{" "}
						<code className="font-mono">{integrationId}</code>
					</CardDescription>
				</CardHeader>
				<CardContent>
					<p className="text-sm text-muted-foreground mb-4">
						{message}
					</p>

					{/* Warning state */}
					{status === "warning" && warning && (
						<>
							<p className="text-sm mb-4">{warning}</p>
							<div className="flex justify-center">
								<Button
									onClick={() => window.close()}
									variant="default"
									className="w-48"
								>
									Close
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
										<code className="font-mono">
											{capturedEntityIdFrom}
										</code>
									</p>
								)}
							</div>
							<div className="flex justify-center">
								<Button
									onClick={() => window.close()}
									variant="default"
									className="w-48"
								>
									Close
								</Button>
							</div>
						</>
					)}
					{status === "success" && !capturedEntityId && (
						<p className="text-xs text-muted-foreground">
							This window will close automatically...
						</p>
					)}

					{/* Error state - manual close */}
					{status === "error" && (
						<div className="flex justify-center">
							<Button
								onClick={() => window.close()}
								variant="outline"
								className="w-48"
							>
								Close
							</Button>
						</div>
					)}

					{/* Picker state - admin picks entity_id source */}
					{status === "picker" && (
						<EntityIdSourcePicker
							candidates={pickerCandidates}
							isPending={setEntityIdSource.isPending}
							onSkip={() => {
								if (window.opener) {
									window.opener.postMessage(
										{
											type: "oauth_success",
											integrationId,
										},
										window.location.origin,
									);
								}
								window.close();
							}}
							onSelect={(candidate) => {
								if (!integrationId) return;
								setEntityIdSource.mutate(
									{
										params: {
											path: { integration_id: integrationId },
										},
										body: {
											type: candidate.type,
											key: candidate.key,
											apply_to_mapping_id: triggeringMappingId,
											apply_value: candidate.value,
										},
									},
									{
										onSuccess: () => {
											if (window.opener) {
												window.opener.postMessage(
													{
														type: "oauth_success",
														integrationId,
													},
													window.location.origin,
												);
											}
											window.close();
										},
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
