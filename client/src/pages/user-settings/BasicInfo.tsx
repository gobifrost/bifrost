import { useState, useEffect } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { toast } from "sonner";
import {
	Loader2,
	AlertCircle,
	Check,
	Eye,
	EyeOff,
} from "lucide-react";
import { profileService, type ProfileResponse } from "@/services/profile";
import { useAuth } from "@/contexts/AuthContext";
import { LogoDropZone } from "@/components/LogoDropZone";

export function BasicInfo() {
	const { user } = useAuth();
	const [profile, setProfile] = useState<ProfileResponse | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	// Profile form state
	const [name, setName] = useState("");
	const [savingName, setSavingName] = useState(false);

	// Password form state
	const [currentPassword, setCurrentPassword] = useState("");
	const [newPassword, setNewPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [showCurrentPassword, setShowCurrentPassword] = useState(false);
	const [showNewPassword, setShowNewPassword] = useState(false);
	const [showConfirmPassword, setShowConfirmPassword] = useState(false);
	const [changingPassword, setChangingPassword] = useState(false);
	const [passwordError, setPasswordError] = useState<string | null>(null);

	// Load profile data
	useEffect(() => {
		async function loadProfile() {
			try {
				const data = await profileService.getProfile();
				setProfile(data);
				setName(data.name || "");
			} catch (err) {
				console.error("Failed to load profile:", err);
				setError("Failed to load profile. Please try again.");
			} finally {
				setLoading(false);
			}
		}

		loadProfile();
	}, []);

	// Derived: dirty when local name differs from server-loaded profile name.
	const nameChanged = name !== (profile?.name || "");

	// Handle name save
	const handleSaveName = async () => {
		setSavingName(true);
		try {
			const updated = await profileService.updateProfile({
				name: name || null,
			});
			setProfile(updated);
			toast.success("Profile updated");
		} catch (err) {
			console.error("Failed to update profile:", err);
			toast.error("Failed to update profile");
		} finally {
			setSavingName(false);
		}
	};

	// Handle password change/set
	const handleChangePassword = async () => {
		setPasswordError(null);

		const hasPassword = profile?.has_password ?? false;

		// Validate passwords
		if (hasPassword && !currentPassword) {
			setPasswordError("Current password is required");
			return;
		}
		if (!newPassword) {
			setPasswordError("New password is required");
			return;
		}
		if (newPassword.length < 8) {
			setPasswordError("New password must be at least 8 characters");
			return;
		}
		if (newPassword !== confirmPassword) {
			setPasswordError("Passwords do not match");
			return;
		}

		setChangingPassword(true);
		try {
			await profileService.changePassword(
				hasPassword ? currentPassword : null,
				newPassword,
			);
			toast.success(
				hasPassword
					? "Password changed successfully"
					: "Password set successfully",
			);
			// Clear form and update profile state
			setCurrentPassword("");
			setNewPassword("");
			setConfirmPassword("");
			// Refresh profile to get updated has_password
			const updatedProfile = await profileService.getProfile();
			setProfile(updatedProfile);
		} catch (err) {
			console.error("Failed to change password:", err);
			const errorMessage =
				err instanceof Error
					? err.message
					: "Failed to change password";
			setPasswordError(errorMessage);
		} finally {
			setChangingPassword(false);
		}
	};

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
		if (profile?.email) {
			return profile.email[0].toUpperCase();
		}
		return "U";
	};

	if (loading) {
		return (
			<div className="flex items-center justify-center py-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	if (error) {
		return (
			<Card>
				<CardContent className="p-6">
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
					<Button
						onClick={() => window.location.reload()}
						className="mt-4"
					>
						Retry
					</Button>
				</CardContent>
			</Card>
		);
	}

	return (
		<div className="space-y-6">
			{/* Profile Picture */}
			<Card>
				<CardHeader>
					<CardTitle>Profile Picture</CardTitle>
					<CardDescription>
						Upload a profile picture (PNG or JPEG, max 2MB)
					</CardDescription>
				</CardHeader>
				<CardContent>
					<LogoDropZone
						uploadUrl="/api/profile/avatar"
						deleteUrl="/api/profile/avatar"
						previewUrl="/api/profile/avatar"
						fallback={
							<span className="text-2xl font-medium">
								{getInitials()}
							</span>
						}
						shape="circle"
						size={96}
						accept="image/png,image/jpeg,image/jpg"
						maxBytes={2 * 1024 * 1024}
						ariaLabel="Upload profile picture"
						onChange={async () => {
							try {
								const fresh = await profileService.getProfile();
								setProfile(fresh);
							} catch {
								/* preview cache-busts itself */
							}
						}}
					/>
				</CardContent>
			</Card>

			{/* Display Name */}
			<Card>
				<CardHeader>
					<CardTitle>Display Name</CardTitle>
					<CardDescription>
						This is how your name appears to other users
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={(e) => { e.preventDefault(); handleSaveName(); }}>
						<div className="space-y-4">
							<div className="space-y-2">
								<Label htmlFor="name">Name</Label>
								<Input
									id="name"
									placeholder="Enter your name"
									value={name}
									onChange={(e) => setName(e.target.value)}
								/>
							</div>

							<div className="space-y-2">
								<Label htmlFor="email">Email</Label>
								<Input
									id="email"
									value={profile?.email || user?.email || ""}
									disabled
									className="bg-muted"
								/>
								<p className="text-xs text-muted-foreground">
									Email cannot be changed
								</p>
							</div>

							<div className="flex justify-end">
								<Button
									type="submit"
									disabled={savingName || !nameChanged}
								>
									{savingName ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin" />
											Saving...
										</>
									) : nameChanged ? (
										"Save Changes"
									) : (
										<>
											<Check className="h-4 w-4 mr-2" />
											Saved
										</>
									)}
								</Button>
							</div>
						</div>
					</form>
				</CardContent>
			</Card>

			{/* Change/Set Password */}
			<Card>
				<CardHeader>
					<CardTitle>
						{profile?.has_password ? "Change Password" : "Set Password"}
					</CardTitle>
					<CardDescription>
						{profile?.has_password
							? "Update your account password"
							: "Add a password to your account for email/password login"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={(e) => { e.preventDefault(); handleChangePassword(); }}>
						<div className="space-y-4">
							{passwordError && (
								<Alert variant="destructive">
									<AlertCircle className="h-4 w-4" />
									<AlertDescription>{passwordError}</AlertDescription>
								</Alert>
							)}

							{profile?.has_password && (
								<div className="space-y-2">
									<Label htmlFor="current-password">
										Current Password
									</Label>
									<div className="relative">
										<Input
											id="current-password"
											type={showCurrentPassword ? "text" : "password"}
											value={currentPassword}
											onChange={(e) =>
												setCurrentPassword(e.target.value)
											}
											placeholder="Enter current password"
										/>
										<Button
											type="button"
											variant="ghost"
											size="icon"
											className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
											onClick={() =>
												setShowCurrentPassword(!showCurrentPassword)
											}
										>
											{showCurrentPassword ? (
												<EyeOff className="h-4 w-4 text-muted-foreground" />
											) : (
												<Eye className="h-4 w-4 text-muted-foreground" />
											)}
										</Button>
									</div>
								</div>
							)}

							<div className="space-y-2">
								<Label htmlFor="new-password">
									{profile?.has_password ? "New Password" : "Password"}
								</Label>
								<div className="relative">
									<Input
										id="new-password"
										type={showNewPassword ? "text" : "password"}
										value={newPassword}
										onChange={(e) => setNewPassword(e.target.value)}
										placeholder={profile?.has_password ? "Enter new password" : "Enter password"}
									/>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
										onClick={() =>
											setShowNewPassword(!showNewPassword)
										}
									>
										{showNewPassword ? (
											<EyeOff className="h-4 w-4 text-muted-foreground" />
										) : (
											<Eye className="h-4 w-4 text-muted-foreground" />
										)}
									</Button>
								</div>
								<p className="text-xs text-muted-foreground">
									Minimum 8 characters
								</p>
							</div>

							<div className="space-y-2">
								<Label htmlFor="confirm-password">
									Confirm Password
								</Label>
								<div className="relative">
									<Input
										id="confirm-password"
										type={showConfirmPassword ? "text" : "password"}
										value={confirmPassword}
										onChange={(e) =>
											setConfirmPassword(e.target.value)
										}
										placeholder="Confirm password"
									/>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
										onClick={() =>
											setShowConfirmPassword(!showConfirmPassword)
										}
									>
										{showConfirmPassword ? (
											<EyeOff className="h-4 w-4 text-muted-foreground" />
										) : (
											<Eye className="h-4 w-4 text-muted-foreground" />
										)}
									</Button>
								</div>
							</div>

							<div className="flex justify-end">
								<Button
									type="submit"
									disabled={
										changingPassword ||
										(profile?.has_password && !currentPassword) ||
										!newPassword ||
										!confirmPassword
									}
								>
									{changingPassword ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin" />
											{profile?.has_password ? "Changing..." : "Setting..."}
										</>
									) : profile?.has_password ? (
										"Change Password"
									) : (
										"Set Password"
									)}
								</Button>
							</div>
						</div>
					</form>
				</CardContent>
			</Card>
		</div>
	);
}
