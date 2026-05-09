import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
	ChevronDown,
	LogOut,
	Settings,
	Menu,
	PanelLeftClose,
	PanelLeft,
	Terminal,
	Search,
	Play,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { useAuth } from "@/contexts/AuthContext";
import { APP_VERSION } from "@/lib/version";
import { useEditorStore } from "@/stores/editorStore";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { NotificationCenter } from "@/components/layout/NotificationCenter";
import { VersionUpdateBanner } from "@/components/layout/VersionUpdateBanner";
import { useProfile } from "@/hooks/useProfile";
import { profileService } from "@/services/profile";
import { webSocketService } from "@/services/websocket";
import { FileActivityIndicator } from "@/components/layout/FileActivityIndicator";
import { cn } from "@/lib/utils";

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
	const location = useLocation();
	const { user, logout, isPlatformAdmin } = useAuth();
	const openEditor = useEditorStore((state) => state.openEditor);
	const openQuickAccess = useQuickAccessStore(
		(state) => state.openQuickAccess,
	);

	const userEmail = user?.email || "Loading...";
	const userName = user?.name || user?.email?.split("@")[0] || "User";

	// Track if there's an active CLI session
	const [hasActiveCLISession, setHasActiveCLISession] = useState(false);
	const isOnCLIPage = location.pathname.startsWith("/cli");

	// Profile data via React Query (cached)
	// dataUpdatedAt provides a stable timestamp for cache-busting avatar URLs
	const { data: profile, dataUpdatedAt } = useProfile();

	// Compute avatar URL with cache-busting timestamp from React Query
	// Using dataUpdatedAt avoids calling Date.now() during render
	const avatarUrl =
		profile?.has_avatar && dataUpdatedAt
			? `${profileService.getAvatarUrl()}?t=${dataUpdatedAt}`
			: null;

	// Subscribe to CLI session updates via websocket (platform admins only)
	useEffect(() => {
		if (!isPlatformAdmin || !user?.id) return;

		// Subscribe to cli-sessions channel for this user
		const channel = `cli-sessions:${user.id}`;
		webSocketService.connect([channel]);

		// Listen for session updates
		const handleMessage = (event: MessageEvent) => {
			try {
				const data = JSON.parse(event.data);
				if (data.type === "cli_session_update") {
					setHasActiveCLISession(data.state !== null);
				}
			} catch {
				// ignore
			}
		};

		const ws = (webSocketService as unknown as { ws: WebSocket | null }).ws;
		ws?.addEventListener("message", handleMessage);

		return () => {
			ws?.removeEventListener("message", handleMessage);
			webSocketService.unsubscribe(channel);
		};
	}, [isPlatformAdmin, user?.id]);

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
			<div className="flex h-16 items-center px-4 lg:px-6">
				{/* Mobile Menu Button */}
				<Button
					variant="ghost"
					size="icon"
					className="md:hidden mr-2"
					onClick={onMobileMenuToggle}
				>
					<Menu className="h-5 w-5" />
				</Button>

				{/* Desktop Sidebar Toggle */}
				<Button
					variant="ghost"
					size="icon"
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
				<div className="flex-1" />

				{/* Version Update Banner — only renders when /api/version
				    differs from the baked-in client version. */}
				<VersionUpdateBanner />

				{/* File Activity Indicator (Platform Admin only) */}
				{isPlatformAdmin && <FileActivityIndicator />}

				{/* Search Button */}
				<Button
					variant="ghost"
					size="icon"
					className="mr-4"
					onClick={() => openQuickAccess()}
					title="Search (Cmd+K)"
				>
					<Search className="h-4 w-4" />
				</Button>

				{/* Shell Button (Platform Admin only) */}
				{isPlatformAdmin && (
					<Button
						variant="ghost"
						size="icon"
						className="mr-2"
						onClick={() => openEditor()}
						title="Shell (Cmd+/)"
					>
						<Terminal className="h-4 w-4" />
					</Button>
				)}

				{/* CLI Sessions Button (Platform Admin only) */}
				{isPlatformAdmin && (
					<Button
						variant={isOnCLIPage ? "secondary" : "ghost"}
						size="icon"
						className={cn(
							"mr-4 relative",
							hasActiveCLISession &&
								!isOnCLIPage &&
								"animate-pulse",
						)}
						onClick={() => navigate("/cli")}
						title={
							hasActiveCLISession
								? "CLI Sessions (Active)"
								: "CLI Sessions"
						}
					>
						<Play className="h-4 w-4" />
						{hasActiveCLISession && !isOnCLIPage && (
							<span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-green-500 ring-2 ring-background" />
						)}
					</Button>
				)}

					{/* Notification Center */}
				<div className="mr-2">
					<NotificationCenter />
				</div>

				{/* Theme Toggle */}
				<div className="mr-2">
					<ThemeToggle />
				</div>

				{/* User Menu */}
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button variant="ghost" className="gap-2">
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
