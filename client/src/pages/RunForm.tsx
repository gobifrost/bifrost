import { useState, useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { FormRenderer } from "@/components/forms/FormRenderer";
import { useFormRuntime } from "@/hooks/useForms";
import { useAuth } from "@/contexts/AuthContext";
import { Skeleton } from "@/components/ui/skeleton";
import { getEmbedTokenClaims } from "@/lib/auth-token";
import { parseFormEmbedPresentation } from "@/lib/form-embed-presentation";

const DEV_MODE_STORAGE_KEY = "bifrost.devMode";

export function RunForm() {
	const { formId } = useParams();
	const navigate = useNavigate();
	const location = useLocation();
	const { isPlatformAdmin, hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");
	const [embedClaims] = useState(() => getEmbedTokenClaims());
	const embedFormId = embedClaims?.form_id;
	const runtimeFormId = formId || embedFormId;
	const { data: form, isLoading, error } = useFormRuntime(runtimeFormId);
	const embedPresentation = parseFormEmbedPresentation(
		location.pathname,
		location.search,
	);

	useEffect(() => {
		const root = document.documentElement;
		root.classList.toggle(
			"embed-transparent",
			isEmbed && embedPresentation?.transparentBackground === true,
		);
		return () => root.classList.remove("embed-transparent");
	}, [embedPresentation?.transparentBackground, isEmbed]);

	// Developer mode state - persisted to localStorage
	const [devMode, setDevMode] = useState(() => {
		if (typeof window !== "undefined") {
			return localStorage.getItem(DEV_MODE_STORAGE_KEY) === "true";
		}
		return false;
	});

	// Persist dev mode changes to localStorage
	useEffect(() => {
		localStorage.setItem(DEV_MODE_STORAGE_KEY, String(devMode));
	}, [devMode]);

	if (isLoading) {
		return (
			<div className="space-y-6">
				<Skeleton className="h-12 w-64" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (error || !form) {
		return (
			<div className="space-y-6">
				<Alert variant="destructive">
					<XCircle className="h-4 w-4" />
					<AlertTitle>Error</AlertTitle>
					<AlertDescription>
						{error ? "Failed to load form" : "Form not found"}
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/forms")}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					Back to Forms
				</Button>
			</div>
		);
	}

	if (!form.is_active) {
		return (
			<div className="space-y-6">
				<Alert>
					<AlertTitle>Form Inactive</AlertTitle>
					<AlertDescription>
						This form is currently inactive and cannot be submitted.
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/forms")}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					Back to Forms
				</Button>
			</div>
		);
	}

	if (isEmbed) {
		return (
			<div className="mx-auto min-h-full max-w-2xl space-y-6 p-4 sm:p-6">
				{embedPresentation?.showHeader !== false ? (
					<div className="text-center">
						<h1 className="text-4xl font-extrabold tracking-tight">
							{form.name}
						</h1>
						{form.description && (
							<p className="mt-2 text-muted-foreground">
								{form.description}
							</p>
						)}
					</div>
				) : null}
				<FormRenderer
					form={form}
					preventNavigation={embedClaims?.grant !== "hmac"}
					allowScheduling={false}
				/>
			</div>
		);
	}

	return (
		<div className="space-y-6">
			{/* Header with back button on left, centered title/description */}
			<div className="flex justify-center">
				<div className="w-full max-w-2xl">
					<div className="flex items-start gap-4">
						<Button
							variant="outline"
							size="icon"
							onClick={() => navigate("/forms")}
							title="Back to Forms"
							className="shrink-0 mt-1"
						>
							<ArrowLeft className="h-4 w-4" />
						</Button>
						<div className="flex-1 text-center">
							<h1 className="text-4xl font-extrabold tracking-tight">
								{form.name}
							</h1>
							{form.description && (
								<p className="mt-2 text-muted-foreground">
									{form.description}
								</p>
							)}
						</div>
						{/* Spacer to balance the back button for true centering */}
						<div className="w-9 shrink-0" />
					</div>
				</div>
			</div>

			<FormRenderer
				form={form}
				devMode={isPlatformAdmin && devMode}
				onDevModeChange={isPlatformAdmin ? setDevMode : undefined}
			/>
		</div>
	);
}
