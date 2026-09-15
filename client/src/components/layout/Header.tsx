import { AccountMenuContent } from "./AccountMenuContent";
import { useNavigate } from "react-router-dom";
import {
	ChevronDown,
	Menu,
	PanelLeftClose,
	PanelLeft,
	Terminal,
	Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuth } from "@/contexts/AuthContext";
import { useEditorStore } from "@/stores/editorStore";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { NotificationCenter } from "@/components/layout/NotificationCenter";
import { PasskeySetupBadge } from "@/components/PasskeySetupBadge";
import { HeaderStatusIndicators } from "./HeaderStatusIndicators";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useProfile } from "@/hooks/useProfile";
import { profileService } from "@/services/profile";
import { BifrostRunMenu } from "@/components/layout/BifrostRunMenu";

interface HeaderProps {
	onMobileMenuToggle?: () => void;
	onSidebarToggle?: () => void;
	isSidebarCollapsed?: boolean;
}

export function Header({
	onMobileMenuToggle,
	onSidebarToggle,
	isSidebarCollapsed = false,
}: HeaderProps = {}) {
	const navigate = useNavigate();
	const compactHeader = useMediaQuery("(max-width: 1279px)");
	const mobileHeader = useMediaQuery("(max-width: 639px)");
	const { user, logout, isPlatformAdmin } = useAuth();
	const openEditor = useEditorStore((state) => state.openEditor);
	const openQuickAccess = useQuickAccessStore(
		(state) => state.openQuickAccess,
	);

	// Profile data via React Query (cached)
	// dataUpdatedAt provides a stable timestamp for cache-busting avatar URLs
	const { data: profile, dataUpdatedAt } = useProfile();
	const userEmail = profile?.email || user?.email || "Loading...";
	const userName = profile
		? profile.name || profile.email.split("@")[0]
		: user?.name || user?.email?.split("@")[0] || "User";

	// Compute avatar URL with cache-busting timestamp from React Query
	// Using dataUpdatedAt avoids calling Date.now() during render
	const avatarUrl =
		profile?.has_avatar && dataUpdatedAt
			? `${profileService.getAvatarUrl()}?t=${dataUpdatedAt}`
			: null;

	// Get initials for avatar fallback
	const getInitials = () => {
		if (profile?.name) {
			return profile.name
				.split(" ")
				.map((n) => n[0])
				.join("")
				.toUpperCase()
				.slice(0, 2);
		}
		if (profile?.email || userEmail) {
			return (profile?.email || userEmail)[0].toUpperCase();
		}
		return "U";
	};

	return (
		<header className="sticky top-0 z-40 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
			<div className="flex min-h-16 items-center px-1 py-2 sm:py-0 sm:px-4 lg:px-6">
				{/* Mobile Menu Button */}
				<Button
					variant="ghost"
					size="icon-lg"
					aria-label="Open navigation"
					data-mobile-navigation-trigger
					className="md:hidden sm:mr-2"
					onClick={onMobileMenuToggle}
				>
					<Menu className="h-5 w-5" />
				</Button>

				{/* Desktop Sidebar Toggle */}
				<Button
					variant="ghost"
					size="icon-lg"
					className="hidden md:flex mr-2"
					onClick={onSidebarToggle}
					title={
						isSidebarCollapsed
							? "Expand sidebar"
							: "Collapse sidebar"
					}
				>
					{isSidebarCollapsed ? (
						<PanelLeft className="h-5 w-5" />
					) : (
						<PanelLeftClose className="h-5 w-5" />
					)}
				</Button>

				{/* Spacer */}
				<div className="hidden flex-1 sm:block" />

				<div className="flex min-w-0 flex-1 flex-wrap items-center justify-end sm:flex-none sm:flex-nowrap">
					{!compactHeader && (
						<HeaderStatusIndicators
							isPlatformAdmin={isPlatformAdmin}
						/>
					)}

					{!mobileHeader && <PasskeySetupBadge />}

					{/* AI assistant connection — only renders while MCP is enabled */}
					<BifrostRunMenu />

					{/* Search Button */}
					<Button
						variant="ghost"
						size="icon-lg"
						className="sm:mr-2 lg:mr-4"
						onClick={() => openQuickAccess()}
						title="Search (Cmd+K)"
					>
						<Search className="h-4 w-4" />
					</Button>

					{/* Shell Button (Platform Admin only) */}
					{isPlatformAdmin && (
						<Button
							variant="ghost"
							size="icon-lg"
							className="sm:mr-2"
							onClick={() => openEditor()}
							title="Shell (Cmd+/)"
							data-editor-launcher
						>
							<Terminal className="h-4 w-4" />
						</Button>
					)}

					{/* Notification Center */}
					<div className="sm:mr-2">
						<NotificationCenter triggerClassName="size-11" />
					</div>

					{/* Theme Toggle */}
					<div className="sm:mr-2">
						<ThemeToggle className="size-11" />
					</div>
				</div>

				{/* User Menu */}
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							variant="ghost"
							className="min-h-11 min-w-11 shrink-0 gap-2 px-2 sm:px-4"
							aria-label="Account menu"
						>
							<Avatar className="h-6 w-6">
								<AvatarImage src={avatarUrl || undefined} />
								<AvatarFallback className="text-xs">
									{getInitials()}
								</AvatarFallback>
							</Avatar>
							<span className="hidden max-w-48 truncate xl:inline-block">
								{userName}
							</span>
							<ChevronDown className="hidden h-4 w-4 sm:block" />
						</Button>
					</DropdownMenuTrigger>
					<AccountMenuContent
						name={userName}
						email={userEmail}
						initials={getInitials()}
						avatarUrl={avatarUrl}
						onSettings={() => navigate("/user-settings")}
						onLogout={logout}
					/>
				</DropdownMenu>
			</div>
			{compactHeader && !mobileHeader && (
				<div
					className="flex flex-wrap items-center justify-end gap-1 px-3 pb-2 empty:hidden sm:px-4"
					aria-label="Workspace status"
				>
					<HeaderStatusIndicators isPlatformAdmin={isPlatformAdmin} />
				</div>
			)}
		</header>
	);
}
