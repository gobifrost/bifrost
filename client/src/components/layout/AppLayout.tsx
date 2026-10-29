/**
 * App Layout
 *
 * Full-screen layout for App Builder applications (preview and published).
 * No sidebar - just AppHeader + full content area.
 */

import { AppHeader } from "./AppHeader";
import { ImpersonationBanner } from "./ImpersonationBanner";

interface AppLayoutProps {
	/** App name to display in header */
	appName: string;
	/** Whether this is preview mode */
	isPreview?: boolean;
	/** Content to render */
	children: React.ReactNode;
}

export function AppLayout({
	appName,
	isPreview = false,
	children,
}: AppLayoutProps) {
	return (
		<div className="h-dvh flex flex-col bg-background overflow-hidden">
			<ImpersonationBanner />
			<AppHeader appName={appName} isPreview={isPreview} />
			<main className="min-h-0 min-w-0 flex-1 overflow-auto">
				{children}
			</main>
		</div>
	);
}
