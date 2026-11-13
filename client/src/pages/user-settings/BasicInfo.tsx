import { useState, useEffect, useRef } from "react";
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
import { Loader2, AlertCircle, Check } from "lucide-react";
import { profileService, type ProfileResponse } from "@/services/profile";
import { useAuth } from "@/contexts/AuthContext";
import { LogoDropZone } from "@/components/LogoDropZone";

import { ProfilePasswordField } from "./ProfilePasswordField";

export function BasicInfo() {
	const { user } = useAuth();
	const [profile, setProfile] = useState<ProfileResponse | null>(null);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saveError, setSaveError] = useState(false);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	// Profile form state
	const [name, setName] = useState("");
	const [savingName, setSavingName] = useState(false);

	// Password form state
	const [currentPassword, setCurrentPassword] = useState("");
	const [newPassword, setNewPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [changingPassword, setChangingPassword] = useState(false);
	const [passwordError, setPasswordError] = useState<string | null>(null);

	const nameBusy = useRef(false);
	const passwordBusy = useRef(false);
	const nameErrorRef = useRef<HTMLParagraphElement>(null);
	const passwordErrorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (saveError) {
			nameErrorRef.current?.focus();
			nameErrorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [saveError]);
	useEffect(() => {
		if (passwordError) {
			passwordErrorRef.current?.focus();
			passwordErrorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [passwordError]);

	// Load profile data
	useEffect(() => {
		let active = true;
		async function loadProfile() {
			try {
				const data = await profileService.getProfile();
				if (!active) return;
				setProfile(data);
				setName(data.name || "");
			} catch (err) {
				console.error("Failed to load profile:", err);
				if (active)
					setError("Failed to load profile. Please try again.");
			} finally {
				if (active) setLoading(false);
			}
		}

		loadProfile();
		return () => {
			active = false;
		};
	}, [loadAttempt]);

	// Derived: dirty when local name differs from server-loaded profile name.
	const nameChanged = name !== (profile?.name || "");

	// Handle name save
	const handleSaveName = async () => {
		if (nameBusy.current) return;
		nameBusy.current = true;
		setSaveError(false);
		setSavingName(true);
		try {
			const updated = await profileService.updateProfile({
				name: name || null,
			});
			setProfile(updated);
			toast.success("Profile updated");
		} catch (err) {
			console.error("Failed to update profile:", err);
			setSaveError(true);
		} finally {
			nameBusy.current = false;
			setSavingName(false);
		}
	};

	// Handle password change/set
	const handleChangePassword = async () => {
		if (passwordBusy.current) return;
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

		passwordBusy.current = true;
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
			// The successful password response establishes this state.
			setProfile((current) =>
				current ? { ...current, has_password: true } : current,
			);
		} catch (err) {
			console.error("Failed to change password:", err);
			const errorMessage =
				err instanceof Error
					? err.message
					: "Failed to change password";
			setPasswordError(errorMessage);
		} finally {
			passwordBusy.current = false;
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
			<div
				role="status"
				aria-label="Loading profile"
				className="flex items-center justify-center py-16"
			>
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	if (error) {
		return (
			<Card>
				<CardContent className="space-y-4 p-[var(--bf-surface-pad)]">
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
					<Button
						type="button"
						onClick={() => {
							setLoading(true);
							setError(null);
							setLoadAttempt((value) => value + 1);
						}}
						className="min-h-11 w-full sm:w-auto"
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
				<CardContent className="flex justify-center sm:justify-start">
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
					<form
						onSubmit={(e) => {
							e.preventDefault();
							handleSaveName();
						}}
					>
						<div className="space-y-4">
							<div className="space-y-2">
								<Label htmlFor="name">Name</Label>
								<Input
									id="name"
									disabled={savingName}
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
									className="bg-muted text-muted-foreground"
								/>
								<p className="text-xs text-muted-foreground">
									Email cannot be changed
								</p>
							</div>

							{saveError && (
								<p
									ref={nameErrorRef}
									tabIndex={-1}
									role="alert"
									className="text-sm text-destructive"
								>
									Couldn't update your profile. Your name is
									ready to retry.
								</p>
							)}
							<div className="flex justify-end">
								<Button
									type="submit"
									className="min-h-11 w-full sm:w-auto"
									disabled={savingName || !nameChanged}
								>
									{savingName ? (
										<>
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
											Saving...
										</>
									) : nameChanged ? (
										saveError ? (
											"Retry save"
										) : (
											"Save Changes"
										)
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
						{profile?.has_password
							? "Change Password"
							: "Set Password"}
					</CardTitle>
					<CardDescription>
						{profile?.has_password
							? "Update your account password"
							: "Add a password to your account for email/password login"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form
						onSubmit={(e) => {
							e.preventDefault();
							void handleChangePassword();
						}}
					>
						<div className="space-y-4">
							{passwordError && (
								<Alert
									ref={passwordErrorRef}
									tabIndex={-1}
									variant="destructive"
								>
									<AlertCircle className="h-4 w-4" />
									<AlertDescription>
										{passwordError}
									</AlertDescription>
								</Alert>
							)}

							{profile?.has_password && (
								<ProfilePasswordField
									id="current-password"
									label="Current Password"
									value={currentPassword}
									onChange={setCurrentPassword}
									autoComplete="current-password"
									disabled={changingPassword}
								/>
							)}
							<ProfilePasswordField
								id="new-password"
								label={
									profile?.has_password
										? "New Password"
										: "Password"
								}
								value={newPassword}
								onChange={setNewPassword}
								autoComplete="new-password"
								disabled={changingPassword}
								hint="Minimum 8 characters"
							/>
							<ProfilePasswordField
								id="confirm-password"
								label="Confirm Password"
								value={confirmPassword}
								onChange={setConfirmPassword}
								autoComplete="new-password"
								disabled={changingPassword}
							/>

							<div className="flex justify-end">
								<Button
									type="submit"
									className="min-h-11 w-full sm:w-auto"
									disabled={
										changingPassword ||
										(profile?.has_password &&
											!currentPassword) ||
										!newPassword ||
										!confirmPassword
									}
								>
									{changingPassword ? (
										<>
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
											{profile?.has_password
												? "Changing..."
												: "Setting..."}
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
