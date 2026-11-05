import { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AppConnectionApproval } from "@/components/auth/AppConnectionApproval";
import { AuthTransition } from "@/components/auth/AuthTransition";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { apiClient } from "@/lib/api-client";
import { parseApiError } from "@/lib/api-error";
import type { components } from "@/lib/v1";

/** Opening a connection link displays consent and never issues a code. */
export function MCPCallback() {
	const [search] = useSearchParams();
	const requestId = search.get("request") ?? search.get("internal_state");
	return <MCPConnection key={requestId} requestId={requestId} />;
}

function MCPConnection({ requestId }: { requestId: string | null }) {
	const { isAuthenticated, isLoading, user } = useAuth();
	const location = useLocation();
	const navigate = useNavigate();
	const [details, setDetails] = useState<
		components["schemas"]["AppConnectionDetails"] | null
	>(null);
	const [error, setError] = useState<string | null>(
		requestId
			? null
			: "This connection link is incomplete. Start again from your app.",
	);
	const [pendingDecision, setPendingDecision] = useState<boolean | null>(
		null,
	);
	const [outcome, setOutcome] = useState<boolean | null>(null);

	useEffect(() => {
		if (isLoading) return;
		if (!isAuthenticated) {
			const returnTo = `${location.pathname}${location.search}`;
			navigate(`/login?return_to=${encodeURIComponent(returnTo)}`, {
				replace: true,
			});
			return;
		}
		if (!requestId) return;
		const controller = new AbortController();
		async function load() {
			try {
				const result = await apiClient.GET(
					"/auth/app-connections/{request_id}",
					{
						params: { path: { request_id: requestId! } },
						signal: controller.signal,
					},
				);
				if (!result.data && result.error)
					throw parseApiError(result.error, result.response.status);
				if (!result.data)
					throw new Error(
						"This connection is unavailable or has expired. Start again from your app.",
					);
				if (!controller.signal.aborted) setDetails(result.data);
			} catch (cause) {
				if (!controller.signal.aborted)
					setError(
						cause instanceof Error
							? cause.message
							: "Could not load this connection.",
					);
			}
		}
		void load();
		return () => controller.abort();
	}, [
		requestId,
		isAuthenticated,
		isLoading,
		location.pathname,
		location.search,
		navigate,
	]);

	async function decide(approved: boolean) {
		if (!details || !requestId || pendingDecision !== null) return;
		setPendingDecision(approved);
		setError(null);
		try {
			const result = await apiClient.POST(
				"/auth/app-connections/{request_id}",
				{
					params: { path: { request_id: requestId } },
					body: { approved, consent_token: details.consent_token },
				},
			);
			if (!result.data && result.error)
				throw parseApiError(result.error, result.response.status);
			if (!result.data?.redirect_url)
				throw new Error(
					"This connection could not be completed. Start again from your app.",
				);
			setOutcome(approved);
			window.location.assign(result.data.redirect_url);
		} catch (cause) {
			setError(
				cause instanceof Error
					? cause.message
					: "Could not complete this connection.",
			);
		} finally {
			setPendingDecision(null);
		}
	}

	if (isLoading || (!details && !error))
		return <AuthTransition message="Loading app connection…" />;
	return (
		<div className="min-h-svh flex items-center justify-center bg-background px-4 py-8">
			<Card className="w-full max-w-md rounded-[var(--bf-radius-feature)] border-border shadow-none">
				<CardHeader>
					<p className="font-display font-semibold">Bifrost</p>
					<h1 className="font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
						{outcome !== null
							? outcome
								? "App Connected"
								: "Connection Denied"
							: details
								? `Allow ${details.client_name} to Access Bifrost?`
								: "Connection Unavailable"}
					</h1>
				</CardHeader>
				<CardContent className="space-y-4">
					{error && (
						<Alert variant="destructive" role="alert">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					{outcome !== null ? (
						<p className="text-sm text-muted-foreground">
							Return to your app. You can close this window.
						</p>
					) : (
						details && (
							<AppConnectionApproval
								email={user?.email}
								clientName={details.client_name}
								returnAddress={details.redirect_uri}
								pendingDecision={pendingDecision}
								onDecision={decide}
							/>
						)
					)}
					<Button
						variant="ghost"
						className="min-h-11 w-full"
						onClick={() => navigate("/")}
					>
						Return to Dashboard
					</Button>
				</CardContent>
			</Card>
		</div>
	);
}
