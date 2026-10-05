/**
 * App Router
 *
 * Universal router for App Builder applications.
 * Renders BundledAppShell (esbuild-bundled runtime) wrapped in AppLayout.
 *
 * Routes:
 * - /apps/:slug/preview/* - Preview mode (uses draft files)
 * - /apps/:slug/* - Published mode (uses live files)
 */

import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppLoadingSkeleton } from "@/components/jsx-app/AppLoadingSkeleton";
import { RouteUnavailableState } from "@/components/layout/RouteUnavailableState";
import { useApplication } from "@/hooks/useApplications";
import { useAuth } from "@/contexts/AuthContext";
import { useDocumentChrome } from "@/lib/useDocumentChrome";
import { useApplicationName } from "@/lib/applicationName";
import { term, useTerminology } from "@/lib/terminology";
import { BundledAppShell } from "@/components/jsx-app/BundledAppShell";
import { AppLayout } from "@/components/layout/AppLayout";

interface AppRouterProps {
	/** Whether to render in preview mode (uses draft version) */
	preview?: boolean;
}

export function AppRouter({ preview = false }: AppRouterProps) {
	const { applicationId: slugParam } = useParams();
	const navigate = useNavigate();
	const terminology = useTerminology();
	const applicationName = useApplicationName();
	const { hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");

	// Fetch application metadata
	const {
		data: application,
		isLoading,
		error,
		isFetching,
		refetch,
	} = useApplication(slugParam);

	// Drive the browser tab title + favicon from the open app. Skipped in embed
	// mode, where the host page owns its own chrome. Must run before the early
	// returns below to satisfy the Rules of Hooks.
	useDocumentChrome({
		title: application?.name
			? preview
				? `${application.name} (Preview) | ${applicationName}`
				: `${application.name} | ${applicationName}`
			: undefined,
		logo: application?.logo,
		enabled: !isEmbed,
	});

	// Loading state
	if (isLoading) {
		return <AppLoadingSkeleton message="Loading application..." />;
	}

	const backAction = !isEmbed && (
		<Button
			type="button"
			className="min-h-11"
			variant="outline"
			onClick={() => navigate("/apps")}
		>
			<ArrowLeft aria-hidden="true" className="size-4" />
			Back to {term(terminology, "app", "formalPlural")}
		</Button>
	);

	if (error) {
		return (
			<RouteUnavailableState
				title={`${term(terminology, "app", "formalSingular")} could not be loaded`}
				description="The app may be unavailable, or your access may have changed. Try loading it again."
			>
				<Button
					type="button"
					className="min-h-11"
					disabled={isFetching}
					onClick={() => void refetch()}
				>
					<RefreshCw
						aria-hidden="true"
						className={
							isFetching
								? "size-4 animate-spin motion-reduce:animate-none"
								: "size-4"
						}
					/>
					{isFetching ? "Retrying…" : "Try again"}
				</Button>
				{backAction}
			</RouteUnavailableState>
		);
	}

	if (!application) {
		return (
			<RouteUnavailableState
				title={`${term(terminology, "app", "formalSingular")} Not Found`}
				description={`The requested ${term(terminology, "app", "formalSingularLower")} does not exist or you don't have access to it.`}
			>
				{backAction}
			</RouteUnavailableState>
		);
	}

	// Preserve the separate V1 publish and V2 deploy contracts.
	if (!preview && !application.is_published) {
		const isV2 = application.app_model === "standalone_v2";
		return (
			<RouteUnavailableState
				title={isV2 ? "Not Deployed" : "Not Published"}
				description={
					isV2 ? (
						<>
							Deploy this App from its local project with{" "}
							<code className="font-mono text-xs">
								bifrost app deploy
							</code>
							.
						</>
					) : (
						"This application has not been published yet."
					)
				}
			>
				{backAction}
				{!isV2 && !isEmbed && (
					<Button
						type="button"
						className="min-h-11"
						onClick={() => navigate(`/apps/${slugParam}/edit`)}
					>
						Open Editor
					</Button>
				)}
			</RouteUnavailableState>
		);
	}

	const shell = (
		<BundledAppShell
			// Fresh instance per app so navigating between apps never carries the
			// previous app's v2 mount state into the next (Codex #10).
			key={application.id}
			appId={application.id}
			solutionId={application.solution_id}
			appSlug={application.slug}
			appName={application.name}
			appLogo={application.logo}
			isPreview={preview}
		/>
	);

	if (isEmbed) {
		return <div className="h-dvh overflow-auto">{shell}</div>;
	}

	// standalone_v2 apps are full-page: the app owns its whole document and
	// composes the platform header itself via the optional SDK <BifrostHeader>.
	// Wrapping it in AppLayout would impose platform chrome and double up with
	// the app's own header (v2 spec §2/§4; Codex R4).
	if (application.app_model === "standalone_v2") {
		return <div className="h-dvh w-full overflow-hidden">{shell}</div>;
	}

	return (
		<AppLayout appName={application.name} isPreview={preview}>
			{shell}
		</AppLayout>
	);
}

/**
 * Published app view
 */
export function AppPublished() {
	return <AppRouter preview={false} />;
}

/**
 * Preview app view (draft version)
 */
export function AppPreview() {
	return <AppRouter preview />;
}
