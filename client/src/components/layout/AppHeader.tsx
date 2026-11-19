/**
 * App Header
 *
 * Header variant for App Builder applications (preview and published).
 * Shows app name with back navigation, optional preview badge,
 * and standard user controls (search, notifications, theme, profile).
 */

import { AccountMenuContent } from "./AccountMenuContent";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	DropdownMenu,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/theme-toggle";
import { NotificationCenter } from "@/components/layout/NotificationCenter";
import { VersionUpdateBanner } from "@/components/layout/VersionUpdateBanner";
import { useAuth } from "@/contexts/AuthContext";
import { term, useTerminology } from "@/lib/terminology";
import { useProfile } from "@/hooks/useProfile";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { profileService } from "@/services/profile";
import { ChevronDown } from "lucide-react";

interface AppHeaderProps {
	/** App name to display */
	appName: string;
	/** Whether this is preview mode */
	isPreview?: boolean;
}

export function AppHeader({ appName, isPreview = false }: AppHeaderProps) {
	const navigate = useNavigate();
	const terminology = useTerminology();
	const { user, logout } = useAuth();
	const openQuickAccess = useQuickAccessStore(
		(state) => state.openQuickAccess,
	);

	// Profile data via React Query (cached)
	const { data: profile, dataUpdatedAt } = useProfile();
	const userEmail = profile?.email || user?.email || "Loading...";
	const userName = profile
		? profile.name || profile.email.split("@")[0]
		: user?.name || user?.email?.split("@")[0] || "User";

	// Compute avatar URL with cache-busting timestamp
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

	// Handle back navigation - always go to apps list
	const handleBack = () => {
		navigate("/apps");
	};

	return (
		<header className="sticky top-0 z-40 w-full shrink-0 border-b bg-background">
			<div className="flex min-w-0 flex-col gap-2 px-4 py-2 lg:flex-row lg:items-center lg:px-6">
				{/* Left: Back button + App name + Preview badge */}
				<div className="flex min-w-0 flex-1 items-center gap-2">
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						onClick={handleBack}
						aria-label={`Back to ${term(terminology, "app", "plural")}`}
					>
						<ArrowLeft className="h-5 w-5" />
					</Button>

					<div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
						<span className="min-w-0 font-display text-base font-semibold leading-snug [overflow-wrap:anywhere]">
							{appName}
						</span>
						{isPreview && (
							<Badge variant="secondary" className="text-xs">
								Preview
							</Badge>
						)}
					</div>
				</div>

				{/* Spacer */}

				{/* Right: Version banner, Search, Notifications, Theme, Profile */}
				<div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
					{/* Version Update Banner — only renders on version mismatch */}
					<VersionUpdateBanner />

					{/* Search Button */}
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						onClick={() => openQuickAccess()}
						title="Search (Cmd+K)"
						aria-label="Search (Cmd+K)"
					>
						<Search className="h-4 w-4" />
					</Button>

					{/* Notification Center */}
					<NotificationCenter triggerClassName="size-11" />

					{/* Theme Toggle */}
					<ThemeToggle className="size-11" />

					{/* User Menu */}
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								type="button"
								variant="ghost"
								className="min-h-11 gap-2 px-2"
								aria-label="Account menu"
							>
								<Avatar data-pii data-pii-avatar className="h-6 w-6">
									<AvatarImage src={avatarUrl || undefined} />
									<AvatarFallback className="text-xs">
										{getInitials()}
									</AvatarFallback>
								</Avatar>
								<span data-pii className="hidden max-w-40 truncate md:inline-block">
									{userName}
								</span>
								<ChevronDown className="hidden h-4 w-4 md:block" />
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
			</div>
		</header>
	);
}
