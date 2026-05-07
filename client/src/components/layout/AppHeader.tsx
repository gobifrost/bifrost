/**
 * App Header
 *
 * Header variant for App Builder applications (preview and published).
 * Shows app name with back navigation, optional preview badge,
 * and standard user controls (search, notifications, theme, profile).
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/theme-toggle";
import { NotificationCenter } from "@/components/layout/NotificationCenter";
import { VersionUpdateBanner } from "@/components/layout/VersionUpdateBanner";
import { useAuth } from "@/contexts/AuthContext";
import { APP_VERSION } from "@/lib/version";
import { useProfile } from "@/hooks/useProfile";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { profileService } from "@/services/profile";
import { ChevronDown, LogOut, Settings } from "lucide-react";

interface AppHeaderProps {
	/** App name to display */
	appName: string;
	/** Whether this is preview mode */
	isPreview?: boolean;
}

export function AppHeader({ appName, isPreview = false }: AppHeaderProps) {
	const navigate = useNavigate();
	const { user, logout } = useAuth();
	const openQuickAccess = useQuickAccessStore((state) => state.openQuickAccess);

	const userEmail = user?.email || "Loading...";
	const userName = user?.name || user?.email?.split("@")[0] || "User";

	// Profile data via React Query (cached)
	const { data: profile, dataUpdatedAt } = useProfile();

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
		<header className="sticky top-0 z-40 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
			<div className="flex h-14 items-center px-4 lg:px-6">
				{/* Left: Back button + App name + Preview badge */}
				<div className="flex items-center gap-3">
					<Button
						variant="ghost"
						size="icon"
						onClick={handleBack}
						title={isPreview ? "Back to Editor" : "Back to Apps"}
					>
						<ArrowLeft className="h-5 w-5" />
					</Button>

					<div className="flex items-center gap-2">
						<span className="font-semibold text-sm">{appName}</span>
						{isPreview && (
							<Badge variant="secondary" className="text-xs">
								Preview
							</Badge>
						)}
					</div>
				</div>

				{/* Spacer */}
				<div className="flex-1" />

				{/* Right: Version banner, Search, Notifications, Theme, Profile */}
				<div className="flex items-center gap-1">
					{/* Version Update Banner — only renders on version mismatch */}
					<VersionUpdateBanner />

					{/* Search Button */}
					<Button
						variant="ghost"
						size="icon"
						onClick={() => openQuickAccess()}
						title="Search (Cmd+K)"
					>
						<Search className="h-4 w-4" />
					</Button>

					{/* Notification Center */}
					<NotificationCenter />

					{/* Theme Toggle */}
					<ThemeToggle />

					{/* User Menu */}
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button variant="ghost" className="gap-2 ml-2">
								<Avatar className="h-6 w-6">
									<AvatarImage src={avatarUrl || undefined} />
									<AvatarFallback className="text-xs">
										{getInitials()}
									</AvatarFallback>
								</Avatar>
								<span className="hidden md:inline-block">
									{userName}
								</span>
								<ChevronDown className="h-4 w-4" />
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="end" className="w-56">
							<DropdownMenuLabel>
								<div className="flex items-center gap-3">
									<Avatar className="h-10 w-10">
										<AvatarImage src={avatarUrl || undefined} />
										<AvatarFallback>
											{getInitials()}
										</AvatarFallback>
									</Avatar>
									<div className="flex flex-col space-y-1">
										<p className="text-sm font-medium">
											{userName}
										</p>
										<p className="text-xs text-muted-foreground">
											{userEmail}
										</p>
									</div>
								</div>
							</DropdownMenuLabel>
							<DropdownMenuSeparator />
							<DropdownMenuItem
								onClick={() => navigate("/user-settings")}
							>
								<Settings className="mr-2 h-4 w-4" />
								Settings
							</DropdownMenuItem>
							<DropdownMenuSeparator />
							<DropdownMenuItem
								className="text-destructive"
								onClick={logout}
							>
								<LogOut className="mr-2 h-4 w-4" />
								Log out
							</DropdownMenuItem>
							<DropdownMenuSeparator />
							<VersionMenuItem />
						</DropdownMenuContent>
					</DropdownMenu>
				</div>
			</div>
		</header>
	);
}

function VersionMenuItem() {
	const [copied, setCopied] = useState(false);
	return (
		<DropdownMenuItem
			className="text-xs text-muted-foreground font-mono justify-center focus:bg-transparent cursor-pointer"
			onSelect={(e) => {
				e.preventDefault();
				void navigator.clipboard.writeText(APP_VERSION);
				setCopied(true);
				setTimeout(() => setCopied(false), 1500);
			}}
			title="Click to copy"
		>
			{copied ? "Copied!" : APP_VERSION}
		</DropdownMenuItem>
	);
}
