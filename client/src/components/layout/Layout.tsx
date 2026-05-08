import { Outlet } from "react-router-dom";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { useAuth } from "@/contexts/AuthContext";
import { NoAccess } from "@/components/NoAccess";
import { Skeleton } from "@/components/ui/skeleton";
import { PasskeySetupBanner } from "@/components/PasskeySetupBanner";
import { RouteErrorBoundary } from "@/components/PageErrorBoundary";
import { useSidebar } from "@/hooks/useSidebar";
import { useFileActivity } from "@/hooks/useFileActivity";

export function Layout() {
	const { isLoading, isPlatformAdmin, isOrgUser, hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");
	const { isMobileMenuOpen, setIsMobileMenuOpen, isSidebarCollapsed, toggleSidebar } = useSidebar();
	useFileActivity();

	const hasAccess = isPlatformAdmin || isOrgUser || isEmbed;

	// Show loading state while checking authentication
	if (isLoading) {
		return (
			<div className="min-h-screen bg-background">
				<Header />
				<div className="flex">
					<main className="flex-1 p-6 lg:p-8">
						<div className="space-y-6">
							<Skeleton className="h-12 w-64" />
							<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
								{[...Array(6)].map((_, i) => (
									<Skeleton key={i} className="h-64 w-full" />
								))}
							</div>
						</div>
					</main>
				</div>
			</div>
		);
	}

	// Show no access page if user has no role (only authenticated, no PlatformAdmin or OrgUser)
	if (!hasAccess) {
		return <NoAccess />;
	}

	// Embed users get bare content — no sidebar, header, or chrome
	if (isEmbed) {
		return <Outlet />;
	}

	return (
		<div className="h-screen flex bg-background overflow-hidden">
			{/* Sidebar - full height with logo */}
			<Sidebar
				isMobileMenuOpen={isMobileMenuOpen}
				setIsMobileMenuOpen={setIsMobileMenuOpen}
				isCollapsed={isSidebarCollapsed}
			/>

			{/* Main content area with header */}
			<div className="flex-1 flex flex-col overflow-hidden">
				<Header
					onMobileMenuToggle={() => setIsMobileMenuOpen(true)}
					onSidebarToggle={toggleSidebar}
					isSidebarCollapsed={isSidebarCollapsed}
				/>
				<main className="flex-1 overflow-auto p-6 lg:p-8">
					<PasskeySetupBanner />
					<RouteErrorBoundary>
						<Outlet />
					</RouteErrorBoundary>
				</main>
			</div>
		</div>
	);
}
