import { ExecutionOrganizationSelect } from "@/components/execution/ExecutionOrganizationSelect";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
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
	const [selection, setSelection] = useState<{
		formId?: string;
		orgId?: string;
	}>();
	const orgId =
		!isEmbed && selection?.formId === runtimeFormId
			? selection?.orgId
			: undefined;
	const [busyFormId, setBusyFormId] = useState<string | null>(null);
	const {
		data: form,
		isLoading,
		error,
		isFetching,
		refetch,
	} = useFormRuntime(runtimeFormId, orgId);
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

	const fixedOrgId =
		form && "organization_id" in form ? form.organization_id : null;
	const executionOrgId = fixedOrgId ? undefined : orgId;
	const organizationPicker = !isEmbed ? (
		<div className="mx-auto w-full max-w-2xl">
			<ExecutionOrganizationSelect
				value={executionOrgId}
				onChange={(orgId) =>
					setSelection({ formId: runtimeFormId, orgId })
				}
				organizationId={fixedOrgId}
				disabled={busyFormId === runtimeFormId}
				resetInputs
				resourceType="form"
			/>
		</div>
	) : null;

	if (isLoading && !form) {
		return (
			<div
				role="status"
				aria-label="Loading form"
				className="mx-auto w-full max-w-2xl space-y-6"
			>
				{organizationPicker}
				<Skeleton className="h-12 w-64 max-w-full" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (!form) {
		return (
			<div className="space-y-6">
				{organizationPicker}
				<Alert variant="destructive">
					<XCircle className="h-4 w-4" />
					<AlertTitle>
						{error ? "Form unavailable" : "Form not found"}
					</AlertTitle>
					<AlertDescription>
						{error
							? "Could not load this form. Try again."
							: "This form is no longer available."}
					</AlertDescription>
				</Alert>
				{error && (
					<Button
						size="lg"
						disabled={isFetching}
						onClick={() => void refetch()}
					>
						{isFetching ? "Retrying…" : "Retry form"}
					</Button>
				)}
				{!isEmbed && (
					<Button size="lg" onClick={() => navigate("/forms")}>
						<ArrowLeft className="mr-2 h-4 w-4" />
						Back to Forms
					</Button>
				)}
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
				{!isEmbed && (
					<Button size="lg" onClick={() => navigate("/forms")}>
						<ArrowLeft className="mr-2 h-4 w-4" />
						Back to Forms
					</Button>
				)}
			</div>
		);
	}

	const refreshNotice = error ? (
		<Alert variant="destructive" className="mx-auto max-w-2xl">
			<AlertDescription>
				Form details could not refresh. Your entries are preserved.
			</AlertDescription>
			<Button
				variant="outline"
				className="mt-3 min-h-11"
				disabled={isFetching}
				onClick={() => void refetch()}
			>
				{isFetching ? "Retrying…" : "Retry form"}
			</Button>
		</Alert>
	) : null;

	if (isEmbed) {
		return (
			<div className="mx-auto min-h-full max-w-2xl space-y-6 p-4 sm:p-6">
				{refreshNotice}
				{embedPresentation?.showHeader !== false ? (
					<div className="text-center">
						<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
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
		<PageWorkspace>
			<div className="shrink-0 space-y-6">
				{refreshNotice}
				{/* Header with back button on left, centered title/description */}
				<div className="flex justify-center">
					<div className="w-full max-w-2xl">
						<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-4">
							<Button
								variant="outline"
								size="icon-lg"
								onClick={() => navigate("/forms")}
								title="Back to Forms"
								aria-label="Back to Forms"
								className="shrink-0 self-start sm:mt-1"
							>
								<ArrowLeft className="h-4 w-4" />
							</Button>
							<div className="flex-1 text-left sm:text-center">
								<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
									{form.name}
								</h1>
								{form.description && (
									<p className="mt-2 text-muted-foreground">
										{form.description}
									</p>
								)}
							</div>
							{/* Spacer to balance the back button for true centering */}
							<div className="hidden w-11 shrink-0 sm:block" />
						</div>
					</div>
				</div>
			</div>

			<PageScrollArea className="space-y-6">
				<div className="space-y-6">
					{organizationPicker}
					<FormRenderer
						key={`${form.id}:${executionOrgId ?? "default"}`}
						form={form}
						orgId={executionOrgId}
						onBusyChange={(busy) =>
							setBusyFormId(busy ? (runtimeFormId ?? null) : null)
						}
						devMode={isPlatformAdmin && devMode}
						onDevModeChange={
							isPlatformAdmin ? setDevMode : undefined
						}
					/>
				</div>
			</PageScrollArea>
		</PageWorkspace>
	);
}
