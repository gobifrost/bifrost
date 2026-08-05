import { useCallback, useEffect, useMemo, useState } from "react";
import {
	AlertTriangle,
	Check,
	ChevronDown,
	Copy,
	ExternalLink,
	Globe2,
	RefreshCw,
	ShieldCheck,
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import { FormConfirmationMarkdown } from "@/components/forms/FormConfirmation";
import { FormEmbedSection } from "@/components/forms/FormEmbedSection";
import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

type FormPublication = components["schemas"]["FormPublicationPublic"];
type PublicationReview = components["schemas"]["FormPublicationReview"];
type FormPublic = components["schemas"]["FormPublic"];

interface FormShareDialogProps {
	formId: string;
	formName: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

type PublicAction = "publish" | "rotate" | "unpublish" | null;
type RestrictionSaveState = "idle" | "saving" | "saved" | "error";
type EmbedTheme = "light" | "dark" | "system";
const DEFAULT_CONFIRMATION_MARKDOWN = "## Form submitted\n\nThank you!";

function parseAllowedOrigins(value: string): string[] {
	return value
		.split(/[\n,]/)
		.map((origin) => origin.trim())
		.filter(Boolean);
}

export function FormShareDialog({
	formId,
	formName,
	open,
	onOpenChange,
}: FormShareDialogProps) {
	const [publication, setPublication] = useState<FormPublication | null>(
		null,
	);
	const [review, setReview] = useState<PublicationReview | null>(null);
	const [allowedOrigins, setAllowedOrigins] = useState("");
	const [savedAllowedOrigins, setSavedAllowedOrigins] = useState("");
	const [restrictionSaveState, setRestrictionSaveState] =
		useState<RestrictionSaveState>("idle");
	const [isLoading, setIsLoading] = useState(false);
	const [loadError, setLoadError] = useState(false);
	const [publicAction, setPublicAction] = useState<PublicAction>(null);
	const [isUpdating, setIsUpdating] = useState(false);
	const [confirmationMarkdown, setConfirmationMarkdown] = useState(
		DEFAULT_CONFIRMATION_MARKDOWN,
	);
	const [savedConfirmationMarkdown, setSavedConfirmationMarkdown] = useState(
		DEFAULT_CONFIRMATION_MARKDOWN,
	);
	const [isSavingConfirmation, setIsSavingConfirmation] = useState(false);
	const [confirmationView, setConfirmationView] = useState<
		"edit" | "preview"
	>("edit");
	const [spamProtectionEnabled, setSpamProtectionEnabled] = useState(true);
	const [isSavingSpamProtection, setIsSavingSpamProtection] = useState(false);
	const [restrictionsOpen, setRestrictionsOpen] = useState(false);
	const [embedTheme, setEmbedTheme] = useState<EmbedTheme>("light");
	const [embedHeaderVisible, setEmbedHeaderVisible] = useState(true);
	const [embedTransparent, setEmbedTransparent] = useState(false);
	const [copiedTarget, setCopiedTarget] = useState<
		"private" | "embed" | null
	>(null);

	const privateUrl = `${window.location.origin}/execute/${formId}`;
	const publicIframeSnippet = useMemo(() => {
		if (publication?.status !== "published" || !publication.iframe_path) {
			return "";
		}
		const appearanceParameters = new URLSearchParams({
			theme: embedTheme,
			header: String(embedHeaderVisible),
			background: embedTransparent ? "transparent" : "solid",
		});
		return `<iframe
  src="${window.location.origin}${publication.iframe_path}?${appearanceParameters.toString()}"
  title="${formName.replaceAll('"', "&quot;")}"
  loading="lazy"
  style="width:100%;min-height:640px;border:0"
  sandbox="allow-forms allow-scripts allow-same-origin"
></iframe>`;
	}, [
		embedHeaderVisible,
		embedTheme,
		embedTransparent,
		formName,
		publication,
	]);

	const fetchPublication = useCallback(async () => {
		setIsLoading(true);
		setLoadError(false);
		try {
			const [publicationResponse, reviewResponse, formResponse] =
				await Promise.all([
					authFetch(`/api/forms/${formId}/publication`),
					authFetch(`/api/forms/${formId}/publication-review`),
					authFetch(`/api/forms/${formId}`),
				]);
			if (
				!publicationResponse.ok ||
				!reviewResponse.ok ||
				!formResponse.ok
			) {
				throw new Error("Unable to load sharing settings");
			}
			const nextPublication: FormPublication =
				await publicationResponse.json();
			setPublication(nextPublication);
			setSpamProtectionEnabled(
				nextPublication.spam_protection_enabled ?? true,
			);
			setReview(await reviewResponse.json());
			const formData: FormPublic = await formResponse.json();
			const nextConfirmation =
				formData.confirmation_markdown || DEFAULT_CONFIRMATION_MARKDOWN;
			setConfirmationMarkdown(nextConfirmation);
			setSavedConfirmationMarkdown(nextConfirmation);
			const nextAllowedOrigins = (
				nextPublication.allowed_origins || []
			).join("\n");
			setAllowedOrigins(nextAllowedOrigins);
			setSavedAllowedOrigins(nextAllowedOrigins);
			setRestrictionSaveState("idle");
			setRestrictionsOpen(
				(nextPublication.allowed_origins || []).length > 0,
			);
		} catch {
			setLoadError(true);
		} finally {
			setIsLoading(false);
		}
	}, [formId]);

	const saveConfirmation = async () => {
		setIsSavingConfirmation(true);
		try {
			const response = await authFetch(`/api/forms/${formId}`, {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					confirmation_markdown: confirmationMarkdown,
				}),
			});
			if (!response.ok) throw new Error("Confirmation update failed");
			setSavedConfirmationMarkdown(confirmationMarkdown);
			toast.success("Confirmation Message saved");
		} catch {
			toast.error("Could not save the Confirmation Message");
		} finally {
			setIsSavingConfirmation(false);
		}
	};

	useEffect(() => {
		if (!open) return;
		void (async () => {
			await fetchPublication();
		})();
	}, [fetchPublication, open]);

	const handleOpenChange = (nextOpen: boolean) => {
		if (!nextOpen) setConfirmationView("edit");
		onOpenChange(nextOpen);
	};

	const saveAllowedOrigins = useCallback(
		async (nextAllowedOrigins: string) => {
			if (!review || publication?.status !== "published") return;
			setRestrictionSaveState("saving");
			try {
				const response = await authFetch(
					`/api/forms/${formId}/publication`,
					{
						method: "PUT",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							reviewed_fingerprint: review.fingerprint,
							allowed_origins:
								parseAllowedOrigins(nextAllowedOrigins),
							spam_protection_enabled: spamProtectionEnabled,
						}),
					},
				);
				if (!response.ok) throw new Error("Restriction update failed");
				setSavedAllowedOrigins(nextAllowedOrigins);
				setPublication((current) =>
					current
						? {
								...current,
								allowed_origins:
									parseAllowedOrigins(nextAllowedOrigins),
							}
						: current,
				);
				setRestrictionSaveState("saved");
			} catch {
				setRestrictionSaveState("error");
			}
		},
		[formId, publication?.status, review, spamProtectionEnabled],
	);

	const saveSpamProtection = async (enabled: boolean) => {
		const previous = spamProtectionEnabled;
		setSpamProtectionEnabled(enabled);
		if (!review || publication?.status !== "published") return;

		setIsSavingSpamProtection(true);
		try {
			const response = await authFetch(
				`/api/forms/${formId}/publication`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						reviewed_fingerprint: review.fingerprint,
						allowed_origins: parseAllowedOrigins(allowedOrigins),
						spam_protection_enabled: enabled,
					}),
				},
			);
			if (!response.ok) throw new Error("Spam protection update failed");
			const nextPublication: FormPublication = await response.json();
			setPublication(nextPublication);
			toast.success(
				enabled
					? "Spam Protection enabled"
					: "Spam Protection disabled",
			);
		} catch {
			setSpamProtectionEnabled(previous);
			toast.error("Could not update Spam Protection");
		} finally {
			setIsSavingSpamProtection(false);
		}
	};

	useEffect(() => {
		if (
			!open ||
			publication?.status !== "published" ||
			allowedOrigins === savedAllowedOrigins
		) {
			return;
		}
		const timer = window.setTimeout(() => {
			void saveAllowedOrigins(allowedOrigins);
		}, 700);
		return () => window.clearTimeout(timer);
	}, [
		allowedOrigins,
		open,
		publication?.status,
		saveAllowedOrigins,
		savedAllowedOrigins,
	]);

	const copy = async (target: "private" | "embed", value: string) => {
		try {
			await navigator.clipboard.writeText(value);
			setCopiedTarget(target);
			toast.success(
				target === "private"
					? "Private link copied"
					: "Embed code copied",
			);
			window.setTimeout(() => setCopiedTarget(null), 2000);
		} catch {
			toast.error("Could not copy to the clipboard");
		}
	};

	const updatePublication = async () => {
		if (!review || publicAction !== "publish") return;
		const origins = parseAllowedOrigins(allowedOrigins);
		setIsUpdating(true);
		try {
			const response = await authFetch(
				`/api/forms/${formId}/publication`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						reviewed_fingerprint: review.fingerprint,
						allowed_origins: origins,
						spam_protection_enabled: spamProtectionEnabled,
					}),
				},
			);
			if (!response.ok) throw new Error("Publication failed");
			await fetchPublication();
			toast.success("Public embed published");
		} catch {
			toast.error("Could not publish this form");
		} finally {
			setIsUpdating(false);
			setPublicAction(null);
		}
	};

	const rotateOrUnpublish = async () => {
		if (publicAction !== "rotate" && publicAction !== "unpublish") return;
		const rotate = publicAction === "rotate";
		setIsUpdating(true);
		try {
			const response = await authFetch(
				rotate
					? `/api/forms/${formId}/publication/rotate-key`
					: `/api/forms/${formId}/publication`,
				{ method: rotate ? "POST" : "DELETE" },
			);
			if (!response.ok) throw new Error("Public access update failed");
			await fetchPublication();
			toast.success(
				rotate ? "Public embed code rotated" : "Public embed disabled",
			);
		} catch {
			toast.error("Could not update public access");
		} finally {
			setIsUpdating(false);
			setPublicAction(null);
		}
	};

	const providerFields = review?.provider_fields || [];
	const fileFields = review?.file_fields || [];
	const blockers = review?.blockers || [];
	const warnings = review?.warnings || [];
	const isPublished = publication?.status === "published";
	const needsReview = publication?.status === "needs_review";

	return (
		<>
			<Dialog open={open} onOpenChange={handleOpenChange}>
				<DialogContent className="max-h-[90vh] max-w-3xl overflow-x-hidden overflow-y-auto">
					<DialogHeader className="min-w-0 pr-8">
						<DialogTitle>Share {formName}</DialogTitle>
						<DialogDescription>
							Choose how people access this form and what they see
							after submitting it.
						</DialogDescription>
					</DialogHeader>

					<Tabs defaultValue="private" className="min-w-0">
						<TabsList className="grid w-full grid-cols-3">
							<TabsTrigger value="private">
								Private Link
							</TabsTrigger>
							<TabsTrigger value="website">
								Website Embed
							</TabsTrigger>
							<TabsTrigger value="hmac">HMAC</TabsTrigger>
						</TabsList>

						<TabsContent value="private" className="pt-3">
							<section className="min-w-0 space-y-3 rounded-2xl border p-4">
								<div className="flex flex-col items-start justify-between gap-3 sm:flex-row">
									<div className="flex gap-3">
										<ShieldCheck className="mt-0.5 h-5 w-5 text-muted-foreground" />
										<div>
											<h3 className="font-medium">
												Private Link
											</h3>
											<p className="text-sm text-muted-foreground">
												Recipients must sign in and
												already have access to this
												form.
											</p>
										</div>
									</div>
									<Badge variant="secondary">Private</Badge>
								</div>
								<div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_auto] gap-2">
									<Input
										aria-label="Private form link"
										readOnly
										value={privateUrl}
									/>
									<Button
										variant="outline"
										size="icon"
										onClick={() =>
											void copy("private", privateUrl)
										}
										title="Copy private link"
									>
										{copiedTarget === "private" ? (
											<Check className="h-4 w-4" />
										) : (
											<Copy className="h-4 w-4" />
										)}
										<span className="sr-only">
											Copy private link
										</span>
									</Button>
									<Button
										variant="outline"
										size="icon"
										asChild
										title="Open private link"
									>
										<a
											href={privateUrl}
											target="_blank"
											rel="noreferrer"
										>
											<ExternalLink className="h-4 w-4" />
											<span className="sr-only">
												Open private link
											</span>
										</a>
									</Button>
								</div>
							</section>
						</TabsContent>

						<TabsContent value="website" className="pt-3">
							<section className="min-w-0 space-y-4 rounded-2xl border p-4">
								<div className="flex flex-col items-start justify-between gap-3 sm:flex-row">
									<div className="flex gap-3">
										<Globe2 className="mt-0.5 h-5 w-5 text-muted-foreground" />
										<div>
											<h3 className="font-medium">
												Website Embed
											</h3>
											<p className="text-sm text-muted-foreground">
												Anonymous visitors can submit
												this form without a Bifrost
												account.
											</p>
										</div>
									</div>
									<div className="flex shrink-0 items-center gap-2">
										<Label
											htmlFor={`public-form-enabled-${formId}`}
										>
											{needsReview
												? "Review Required"
												: isPublished
													? "Published"
													: "Not Published"}
										</Label>
										<Switch
											id={`public-form-enabled-${formId}`}
											checked={isPublished}
											disabled={
												isLoading ||
												isUpdating ||
												(!isPublished &&
													blockers.length > 0)
											}
											onCheckedChange={(checked) =>
												setPublicAction(
													checked
														? "publish"
														: "unpublish",
												)
											}
										/>
									</div>
								</div>

								{isLoading ? (
									<p className="text-sm text-muted-foreground">
										Loading sharing settings…
									</p>
								) : loadError ? (
									<Alert variant="destructive">
										<AlertTriangle />
										<AlertTitle>
											Sharing settings could not be loaded
										</AlertTitle>
										<AlertDescription>
											<Button
												variant="outline"
												size="sm"
												onClick={() =>
													void fetchPublication()
												}
											>
												<RefreshCw className="h-4 w-4" />{" "}
												Retry
											</Button>
										</AlertDescription>
									</Alert>
								) : (
									<>
										{needsReview ? (
											<Alert variant="destructive">
												<AlertTriangle />
												<AlertTitle>
													The form changed after
													publication
												</AlertTitle>
												<AlertDescription>
													The existing embed is paused
													until its capabilities are
													reviewed and approved again.
												</AlertDescription>
											</Alert>
										) : null}

										<div className="flex items-start justify-between gap-4 border-t py-3">
											<div className="flex gap-3">
												<ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
												<div>
													<Label
														htmlFor={`public-form-spam-protection-${formId}`}
														className="font-medium"
													>
														Spam Protection
													</Label>
													<p className="mt-1 text-sm text-muted-foreground">
														Require anonymous
														visitors to complete a
														private, self-hosted
														verification. No
														external service or
														account is required.
													</p>
												</div>
											</div>
												<Switch
													id={`public-form-spam-protection-${formId}`}
													checked={spamProtectionEnabled}
													disabled={isSavingSpamProtection}
												onCheckedChange={(checked) =>
													void saveSpamProtection(
														checked,
													)
												}
												aria-label="Spam Protection"
											/>
										</div>

										<Collapsible
											open={restrictionsOpen}
											onOpenChange={setRestrictionsOpen}
											className="border-t pt-1"
										>
											<CollapsibleTrigger className="flex min-h-9 w-full items-center justify-between py-2 text-sm font-medium outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50">
												<span className="text-left">
													Website Restrictions
													<span className="ml-2 text-xs font-normal text-muted-foreground">
														Optional
													</span>
												</span>
												<ChevronDown
													className={`h-4 w-4 transition-transform ${restrictionsOpen ? "rotate-180" : ""}`}
												/>
											</CollapsibleTrigger>
											<CollapsibleContent className="space-y-2 pb-3 pt-2">
												<Label
													htmlFor={`public-form-origins-${formId}`}
												>
													Allowed Website Origins
												</Label>
												<Textarea
													id={`public-form-origins-${formId}`}
													value={allowedOrigins}
													onChange={(event) =>
														setAllowedOrigins(
															event.target.value,
														)
													}
													placeholder="https://www.example.com"
													rows={2}
													className="field-sizing-fixed min-w-0 max-w-full"
												/>
												<p className="text-xs text-muted-foreground">
													Limit which websites can
													frame this form. Use one
													exact origin per line; leave
													empty to allow any website.
												</p>
												{isPublished ? (
													<p
														className={
															restrictionSaveState ===
															"error"
																? "text-xs text-destructive"
																: "text-xs text-muted-foreground"
														}
														role={
															restrictionSaveState ===
															"error"
																? "alert"
																: "status"
														}
													>
														{restrictionSaveState ===
														"saving"
															? "Saving restrictions…"
															: restrictionSaveState ===
																  "saved"
																? "Restrictions saved"
																: restrictionSaveState ===
																	  "error"
																	? "Restrictions could not be saved. Check each origin and try again."
																	: "Changes save automatically."}
													</p>
												) : null}
											</CollapsibleContent>
										</Collapsible>

										{blockers.map((blocker) => (
											<Alert
												key={blocker.code}
												variant="destructive"
											>
												<AlertTriangle />
												<AlertTitle>
													Cannot publish
												</AlertTitle>
												<AlertDescription>
													{blocker.message}
												</AlertDescription>
											</Alert>
										))}

										{isPublished && publicIframeSnippet ? (
											<div className="space-y-3">
												<div className="space-y-2">
													<Label>Embed Options</Label>
													<div className="grid gap-3 rounded-xl bg-muted/30 p-3 sm:grid-cols-3">
														<div className="space-y-1.5">
															<Label
																htmlFor={`embed-theme-${formId}`}
															>
																Theme
															</Label>
															<Select
																value={
																	embedTheme
																}
																onValueChange={(
																	value,
																) =>
																	setEmbedTheme(
																		value as EmbedTheme,
																	)
																}
															>
																<SelectTrigger
																	id={`embed-theme-${formId}`}
																	className="w-full"
																>
																	<SelectValue />
																</SelectTrigger>
																<SelectContent>
																	<SelectItem value="light">
																		Light
																	</SelectItem>
																	<SelectItem value="dark">
																		Dark
																	</SelectItem>
																	<SelectItem value="system">
																		System
																	</SelectItem>
																</SelectContent>
															</Select>
														</div>
														<div className="space-y-1.5">
															<Label
																htmlFor={`embed-header-${formId}`}
																className="whitespace-nowrap"
															>
																Show Header
															</Label>
															<div className="flex h-8 items-center justify-between rounded-lg bg-background/70 px-3">
																<span className="text-xs text-muted-foreground">
																	{embedHeaderVisible
																		? "Shown"
																		: "Hidden"}
																</span>
																<Switch
																	id={`embed-header-${formId}`}
																	checked={
																		embedHeaderVisible
																	}
																	onCheckedChange={
																		setEmbedHeaderVisible
																	}
																/>
															</div>
														</div>
														<div className="space-y-1.5">
															<Label
																htmlFor={`embed-transparent-${formId}`}
																className="whitespace-nowrap"
															>
																Transparent
																Background
															</Label>
															<div className="flex h-8 items-center justify-between rounded-lg bg-background/70 px-3">
																<span className="text-xs text-muted-foreground">
																	{embedTransparent
																		? "Transparent"
																		: "Solid"}
																</span>
																<Switch
																	id={`embed-transparent-${formId}`}
																	checked={
																		embedTransparent
																	}
																	onCheckedChange={
																		setEmbedTransparent
																	}
																/>
															</div>
														</div>
													</div>
													<p className="text-xs text-muted-foreground">
														These options only
														change the code you
														copy.
													</p>
												</div>
												<Label>Embed Code</Label>
												<div
													className="relative min-w-0 max-w-full overflow-hidden rounded-xl"
													role="region"
													aria-label="Embed Code"
												>
													<SyntaxHighlighter
														language="html"
														style={oneDark}
														wrapLongLines
														codeTagProps={{
															style: {
																whiteSpace:
																	"pre-wrap",
																wordBreak:
																	"break-all",
															},
														}}
														customStyle={{
															margin: 0,
															paddingRight:
																"3rem",
															fontSize: "0.75rem",
														}}
													>
														{publicIframeSnippet}
													</SyntaxHighlighter>
													<Button
														variant="secondary"
														size="icon"
														className="absolute right-2 top-2 h-7 w-7"
														onClick={() =>
															void copy(
																"embed",
																publicIframeSnippet,
															)
														}
														title="Copy Embed Code"
													>
														{copiedTarget ===
														"embed" ? (
															<Check />
														) : (
															<Copy />
														)}
														<span className="sr-only">
															Copy Embed Code
														</span>
													</Button>
												</div>
												<div className="flex justify-end border-t pt-3">
													<Button
														variant="outline"
														onClick={() =>
															setPublicAction(
																"rotate",
															)
														}
													>
														Rotate
													</Button>
												</div>
											</div>
										) : null}

										<section className="space-y-3 border-t pt-4">
											<div>
												<h3 className="font-medium">
													Confirmation Message
												</h3>
												<p className="text-sm text-muted-foreground">
													Shown after a successful
													anonymous submission.
												</p>
											</div>
											<Tabs
												value={confirmationView}
												onValueChange={(value) =>
													setConfirmationView(
														value as
															"edit" | "preview",
													)
												}
											>
												<TabsList className="grid w-48 grid-cols-2">
													<TabsTrigger value="edit">
														Edit
													</TabsTrigger>
													<TabsTrigger value="preview">
														Preview
													</TabsTrigger>
												</TabsList>
												<TabsContent
													value="edit"
													forceMount
													className="pt-1 data-[state=inactive]:hidden"
												>
													<TiptapEditor
														content={
															confirmationMarkdown
														}
														onChange={
															setConfirmationMarkdown
														}
														ariaLabel="Confirmation Message editor"
														placeholder="Write a confirmation message…"
														className="min-h-[220px]"
													/>
												</TabsContent>
												<TabsContent
													value="preview"
													forceMount
													className="pt-1 data-[state=inactive]:hidden"
												>
													<div className="prose prose-sm min-h-[220px] max-w-none rounded-xl bg-muted/40 p-4 ring-1 ring-foreground/5 dark:prose-invert">
														<FormConfirmationMarkdown
															markdown={
																confirmationMarkdown
															}
														/>
													</div>
												</TabsContent>
											</Tabs>
											<p className="text-xs text-muted-foreground">
												Markdown and HTTPS images are
												supported. External image hosts
												receive a request when visitors
												view the confirmation.
											</p>
											<div className="flex justify-end">
												<Button
													onClick={() =>
														void saveConfirmation()
													}
														disabled={
															isSavingConfirmation ||
														confirmationMarkdown ===
															savedConfirmationMarkdown
													}
												>
													{isSavingConfirmation
														? "Updating…"
														: "Update"}
												</Button>
											</div>
										</section>
									</>
								)}
							</section>
						</TabsContent>

						<TabsContent value="hmac" className="pt-3">
							<section className="min-w-0 rounded-2xl border p-4">
								<FormEmbedSection formId={formId} />
							</section>
						</TabsContent>
					</Tabs>
				</DialogContent>
			</Dialog>

			<AlertDialog
				open={publicAction !== null}
				onOpenChange={(nextOpen) => !nextOpen && setPublicAction(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							{publicAction === "rotate"
								? "Rotate the public embed code?"
								: publicAction === "unpublish"
									? "Disable the public embed?"
									: "Allow anonymous form access?"}
						</AlertDialogTitle>
						<AlertDialogDescription asChild>
							<div className="space-y-3">
								{publicAction !== "rotate" &&
								publicAction !== "unpublish" ? (
									<>
										<p>
											Anyone on an allowed website can
											load this form without signing in.
											The public session can only use the
											capabilities below.
										</p>
										<ul className="list-disc space-y-1 pl-5 text-left">
											<li>
												Execute{" "}
												{review?.submission_workflow
													?.name ||
													"the linked submission workflow"}
												.
											</li>
											{review?.startup_workflow ? (
												<li>
													Load data from{" "}
													{
														review.startup_workflow
															.name
													}{" "}
													when the form opens.
												</li>
											) : null}
											{providerFields.length > 0 ? (
												<li>
													Query{" "}
													{providerFields.length}{" "}
													approved data provider
													{providerFields.length === 1
														? ""
														: "s"}
													:{" "}
													{providerFields
														.map(
															(field) =>
																field.provider_name,
														)
														.join(", ")}
													.
												</li>
											) : null}
											{fileFields.length > 0 ? (
												<li>
													Upload files for:{" "}
													{fileFields.join(", ")}.
												</li>
											) : null}
										</ul>
										{warnings.map((warning) => (
											<p
												key={warning}
												className="text-amber-600 dark:text-amber-400"
											>
												{warning}
											</p>
										))}
										<p className="font-medium text-foreground">
											No other workflows or Bifrost
											execution APIs are granted.
										</p>
									</>
								) : publicAction === "rotate" ? (
									<p>
										Existing embed code stops loading
										immediately. Replace it on every website
										with the newly generated code.
									</p>
								) : (
									<p>
										The public iframe and issued public
										sessions stop working immediately. The
										private link and HMAC integrations are
										unaffected.
									</p>
								)}
							</div>
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							disabled={isUpdating}
							onClick={() =>
								publicAction === "publish"
									? void updatePublication()
									: void rotateOrUnpublish()
							}
						>
							{isUpdating
								? "Updating…"
								: publicAction === "rotate"
									? "Rotate"
									: publicAction === "unpublish"
										? "Disable embed"
										: "Publish public embed"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
