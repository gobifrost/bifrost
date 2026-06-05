import { Outlet } from "react-router-dom";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { useAuth } from "@/contexts/AuthContext";
import { NoAccess } from "@/components/NoAccess";
import { Skeleton } from "@/components/ui/skeleton";
import { RouteErrorBoundary } from "@/components/PageErrorBoundary";
import { useSidebar } from "@/hooks/useSidebar";

export function ContentLayout() {
	const { isLoading, isPlatformAdmin, isOrgUser, hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");
	const { isMobileMenuOpen, setIsMobileMenuOpen, isSidebarCollapsed, toggleSidebar } = useSidebar();

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
	const hasAccess = isPlatformAdmin || isOrgUser || isEmbed;
	if (!hasAccess) {
		return <NoAccess />;
	}

	// Embed users get bare content — no sidebar, header, or chrome
	if (isEmbed) {
		return <Outlet />;
	}

	return (
		<div className="h-dvh flex bg-background overflow-hidden">
			{/* Sidebar - full height with logo */}
			<Sidebar
				isMobileMenuOpen={isMobileMenuOpen}
				setIsMobileMenuOpen={setIsMobileMenuOpen}
				isCollapsed={isSidebarCollapsed}
			/>

			{/* Main content area with header */}
			<div className="flex-1 min-w-0 flex flex-col overflow-hidden">
				<Header
					onMobileMenuToggle={() => setIsMobileMenuOpen(true)}
					onSidebarToggle={toggleSidebar}
					isSidebarCollapsed={isSidebarCollapsed}
				/>
				<main className="flex-1 min-h-0 overflow-auto">
					<RouteErrorBoundary>
						<Outlet />
					</RouteErrorBoundary>
				</main>
			</div>
		</div>
	);
}
