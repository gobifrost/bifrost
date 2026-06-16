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
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppLoadingSkeleton } from "@/components/jsx-app/AppLoadingSkeleton";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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

	// Error state
	if (error) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-destructive">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>
								{term(terminology, "app", "formalSingular")} Error
							</CardTitle>
						</div>
						<CardDescription>
							{error instanceof Error
								? error.message
								: `Failed to load ${term(terminology, "app", "formalSingularLower")}`}
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Button
							variant="outline"
							onClick={() => navigate("/apps")}
						>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back to {term(terminology, "app", "formalPlural")}
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// No application found
	if (!application) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-muted-foreground">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>
								{term(terminology, "app", "formalSingular")} Not Found
							</CardTitle>
						</div>
						<CardDescription>
							The requested{" "}
							{term(terminology, "app", "formalSingularLower")} does
							not exist or you don't have access to it.
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Button
							variant="outline"
							onClick={() => navigate("/apps")}
						>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back to {term(terminology, "app", "formalPlural")}
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// Handle not published (only for non-preview mode). standalone_v2 apps have
	// NO publish concept (created == published; deploy serves the source) — the
	// v1 "Not Published"/"Open Editor" screen must never apply to them. The
	// server already reports is_published=true for v2; this guards the client too.
	if (!preview && application.app_model !== "standalone_v2" && !application.is_published) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-muted-foreground">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>Not Published</CardTitle>
						</div>
						<CardDescription>
							This application has not been published yet.
						</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						<Button
							variant="outline"
							onClick={() => navigate("/apps")}
						>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back
						</Button>
						<Button
							onClick={() =>
								navigate(`/apps/${slugParam}/edit`)
							}
						>
							Open Editor
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	const shell = (
		<BundledAppShell
			// Fresh instance per app so navigating between apps never carries the
			// previous app's v2 mount state into the next (Codex #10).
			key={application.id}
			appId={application.id}
			appSlug={application.slug}
			isPreview={preview}
		/>
	);

	if (isEmbed) {
		return <div className="h-screen overflow-auto">{shell}</div>;
	}

	// standalone_v2 apps are full-page: the app owns its whole document and
	// composes the platform header itself via the optional SDK <BifrostHeader>.
	// Wrapping it in AppLayout would impose platform chrome and double up with
	// the app's own header (v2 spec §2/§4; Codex R4).
	if (application.app_model === "standalone_v2") {
		return <div className="h-screen w-screen overflow-hidden">{shell}</div>;
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
