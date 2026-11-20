import { usePresentationStore } from "@/stores/presentationStore";
import { useEffect, useState, useRef } from "react";
import {
	ChevronLeft,
	ChevronRight,
	Download,
	FileText,
	Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { authFetch } from "@/lib/api-client";
import {
	attachmentContentUrl,
	downloadChatAttachment,
	formatBytes,
	isImageAttachment,
	isVideoAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";

const DOCX =
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const XLSX =
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function LoadingPreview({ label = "Preparing preview" }: { label?: string }) {
	return (
		<div role="status" className="flex min-h-64 flex-1 items-center justify-center bg-muted/20">
			<div className="flex items-center gap-2 text-sm text-muted-foreground">
				<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
				{label}
			</div>
		</div>
	);
}

function PreviewError({ onRetry }: { onRetry: () => void }) {
	return (
		<div role="alert" className="flex min-h-64 flex-1 items-center justify-center p-6 text-center">
			<div>
				<FileText className="mx-auto mb-3 h-7 w-7 text-muted-foreground" />
				<p className="text-sm font-medium">Preview unavailable</p>
				<p className="mt-1 text-sm text-muted-foreground">
					Try again, or download the file to open it locally.
				</p>
				<Button variant="outline" size="sm" className="mt-4 min-h-11" onClick={onRetry}>
					Retry preview
				</Button>
			</div>
		</div>
	);
}

function AuthenticatedBinaryPreview({
	url,
	attachment,
}: {
	url: string;
	attachment: AttachmentPublic;
}) {
	const [blobUrl, setBlobUrl] = useState<string | null>(null);
	const [loaded, setLoaded] = useState(false);
	const [error, setError] = useState(false);
	const [attempt, setAttempt] = useState(0);

	useEffect(() => {
		let objectUrl: string | null = null;
		let cancelled = false;
		authFetch(url)
			.then((response) => {
				if (!response.ok) throw new Error("Preview failed");
				return response.blob();
			})
			.then((blob) => {
				if (cancelled) return;
				objectUrl = URL.createObjectURL(blob);
				setBlobUrl(objectUrl);
			})
			.catch(() => !cancelled && setError(true));
		return () => {
			cancelled = true;
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [url, attempt]);

	if (error) {
		return (
			<PreviewError
				onRetry={() => {
					setBlobUrl(null);
					setLoaded(false);
					setError(false);
					setAttempt((value) => value + 1);
				}}
			/>
		);
	}
	if (!blobUrl) return <LoadingPreview />;

	return (
		<div className="relative flex min-h-0 flex-1 bg-muted/20">
			{!loaded && (
				<div className="absolute inset-0 z-10 flex items-center justify-center bg-background/90 transition-opacity motion-reduce:transition-none">
					<LoadingPreview label="Loading preview" />
				</div>
			)}
			{isImageAttachment(attachment.content_type) ? (
				<div className="flex flex-1 items-center justify-center overflow-auto p-3 sm:p-5">
					<img
						src={blobUrl}
						alt={attachment.filename}
						onLoad={() => setLoaded(true)}
						onError={() => setError(true)}
						className="max-h-full max-w-full rounded-lg object-contain shadow-sm"
					/>
				</div>
			) : isVideoAttachment(attachment.content_type) ? (
				<div className="flex flex-1 items-center justify-center overflow-auto bg-black p-3 sm:p-5">
					<video
						src={blobUrl}
						controls
						onLoadedData={() => setLoaded(true)}
						onError={() => setError(true)}
						className="max-h-full max-w-full rounded-lg"
					>
						<track kind="captions" />
					</video>
				</div>
			) : (
				<iframe
					data-pii-surface
					title={attachment.filename}
					src={blobUrl}
					onLoad={() => setLoaded(true)}
					className="h-full min-h-[70vh] w-full bg-white"
				/>
			)}
		</div>
	);
}

function AuthenticatedTextPreview({
	url,
	attachment,
}: {
	url: string;
	attachment: AttachmentPublic;
}) {
	const [content, setContent] = useState<string | null>(null);
	const [error, setError] = useState(false);
	const [attempt, setAttempt] = useState(0);
	const richPreview =
		attachment.content_type === "text/html" ||
		attachment.content_type === DOCX ||
		attachment.content_type === XLSX;

	useEffect(() => {
		let cancelled = false;
		authFetch(url)
			.then((response) => {
				if (!response.ok) throw new Error("Preview failed");
				return response.text();
			})
			.then((value) => !cancelled && setContent(value))
			.catch(() => !cancelled && setError(true));
		return () => {
			cancelled = true;
		};
	}, [url, attempt]);

	if (error) {
		return (
			<PreviewError
				onRetry={() => {
					setContent(null);
					setError(false);
					setAttempt((value) => value + 1);
				}}
			/>
		);
	}
	if (content === null) return <LoadingPreview />;
	if (richPreview) {
		return (
			<div data-pii-surface className="min-w-0 flex-1">
				<iframe
					title={attachment.filename}
					sandbox=""
					srcDoc={content}
					className="h-full min-h-[70vh] w-full bg-white"
				/>
			</div>
		);
	}
	return (
		<pre
			data-pii-surface
			tabIndex={0}
			aria-label="File contents"
			className="min-h-0 min-w-0 flex-1 overflow-auto whitespace-pre-wrap p-5 font-mono text-sm leading-6 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
		>
			{content}
		</pre>
	);
}

export function FilePreviewSheet({
	conversationId,
	attachment,
	attachments = [],
	onAttachmentChange,
	onOpenChange,
}: {
	conversationId: string;
	attachment: AttachmentPublic | null;
	attachments?: AttachmentPublic[];
	onAttachmentChange?: (attachment: AttachmentPublic) => void;
	onOpenChange: (open: boolean) => void;
}) {
	const downloadGeneration = useRef(0);
	const [downloading, setDownloading] = useState(false);
	const [downloadError, setDownloadError] = useState<{ id: string; message: string } | null>(null);
	const gallery = attachments.filter(
		(item) =>
			isImageAttachment(item.content_type) ||
			isVideoAttachment(item.content_type),
	);
	const galleryIndex = attachment
		? gallery.findIndex((item) => item.id === attachment.id)
		: -1;
	const canNavigate = galleryIndex >= 0 && gallery.length > 1;
	const isOfficePreview =
		attachment?.content_type === DOCX || attachment?.content_type === XLSX;
	const previewUrl = attachment
		? attachmentContentUrl(conversationId, attachment.id, {
				preview: isOfficePreview,
			})
		: "";
	const isMedia = Boolean(
		attachment &&
			(isImageAttachment(attachment.content_type) ||
				isVideoAttachment(attachment.content_type)),
	);

	const presentationMode = usePresentationStore((state) => state.enabled);
	const download = async () => {
		if (!attachment || downloading) return;
		const generation = ++downloadGeneration.current;
		setDownloadError(null);
		setDownloading(true);
		try {
			await downloadChatAttachment(conversationId, attachment);
		} catch {
			if (generation === downloadGeneration.current) setDownloadError({ id: attachment.id, message: "Could not download this file. Try Download again." });
		} finally {
			if (generation === downloadGeneration.current) setDownloading(false);
		}
	};
	const changeOpen = (open: boolean) => {
		if (!open) {
			downloadGeneration.current += 1;
			setDownloading(false);
			setDownloadError(null);
		}
		onOpenChange(open);
	};

	const navigateGallery = (direction: -1 | 1) => {
		if (!canNavigate || !onAttachmentChange) return;
		const nextIndex = (galleryIndex + direction + gallery.length) % gallery.length;
		onAttachmentChange(gallery[nextIndex]);
	};

	if (isMedia) {
		return (
			<Dialog open={attachment !== null} onOpenChange={changeOpen}>
				<DialogContent className="flex h-dvh w-full max-w-none flex-col gap-0 overflow-hidden rounded-none border-border/70 bg-background p-0 [&_[data-slot=dialog-close]]:right-2 [&_[data-slot=dialog-close]]:top-[max(0.5rem,env(safe-area-inset-top))] [&_[data-slot=dialog-close]]:size-11 sm:h-[min(88vh,900px)] sm:w-[min(94vw,1100px)] sm:rounded-[var(--bf-radius-feature)] sm:[&_[data-slot=dialog-close]]:right-4 sm:[&_[data-slot=dialog-close]]:top-4 sm:[&_[data-slot=dialog-close]]:size-11">
					{attachment && (
						<>
							<DialogHeader className="shrink-0 border-b px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] pr-14 text-left sm:px-5 sm:py-4 sm:pr-14">
								<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
									<div className="min-w-0 flex-1">
										<DialogTitle className="[overflow-wrap:anywhere]">
											{presentationMode
												? "File Preview"
												: attachment.filename}
										</DialogTitle>
										<DialogDescription>
											{formatBytes(attachment.size_bytes)}
										</DialogDescription>
									</div>
									<div className="flex flex-wrap min-h-11 w-full shrink-0 items-center justify-between gap-1 sm:min-h-0 sm:w-auto sm:justify-start">
										{canNavigate && (
											<>
											<Button variant="ghost" size="icon-sm" className="size-11 shrink-0 sm:size-11" onClick={() => navigateGallery(-1)} aria-label="Previous media">
													<ChevronLeft className="h-4 w-4" />
												</Button>
												<span className="px-1 text-xs text-muted-foreground">{galleryIndex + 1} / {gallery.length}</span>
											<Button variant="ghost" size="icon-sm" className="size-11 shrink-0 sm:size-11" onClick={() => navigateGallery(1)} aria-label="Next media">
													<ChevronRight className="h-4 w-4" />
												</Button>
											</>
										)}
										<Button variant="outline" size="sm" className="h-11 shrink-0 sm:h-11" onClick={download} disabled={downloading}>
											{downloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" /> : <Download className="mr-2 h-4 w-4" />}
											Download
										</Button>
									</div>
								</div>
							{downloadError?.id === attachment.id && <p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">{downloadError.message}</p>}
							</DialogHeader>
							<div
								data-pii-surface
								className="flex min-h-0 flex-1 bg-black/90"
							>
								<AuthenticatedBinaryPreview
									key={attachment.id}
									url={previewUrl}
									attachment={attachment}
								/>
							</div>
						</>
					)}
				</DialogContent>
			</Dialog>
		);
	}

	return (
		<Sheet open={attachment !== null} onOpenChange={changeOpen}>
			<SheetContent className="w-full p-0 [&_[data-slot=sheet-close]]:right-2 [&_[data-slot=sheet-close]]:top-[max(0.5rem,env(safe-area-inset-top))] [&_[data-slot=sheet-close]]:size-11 sm:max-w-2xl sm:[&_[data-slot=sheet-close]]:right-4 sm:[&_[data-slot=sheet-close]]:top-4 sm:[&_[data-slot=sheet-close]]:size-11 lg:max-w-3xl">
				{attachment && (
					<>
						<SheetHeader className="shrink-0 border-b px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] pr-14 sm:px-5 sm:py-4 sm:pr-14">
							<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
								<div className="min-w-0 flex-1">
									<SheetTitle className="[overflow-wrap:anywhere]">
										{presentationMode
											? "File Preview"
											: attachment.filename}
									</SheetTitle>
									<SheetDescription>
										{formatBytes(attachment.size_bytes)}
									</SheetDescription>
								</div>
								<div className="flex flex-wrap shrink-0 items-center gap-1">
									{canNavigate && (
										<>
											<Button
												variant="ghost"
												size="icon-sm"
												className="size-11 shrink-0 sm:size-11"
												onClick={() => navigateGallery(-1)}
												aria-label="Previous media"
											>
												<ChevronLeft className="h-4 w-4" />
											</Button>
											<span className="px-1 text-xs text-muted-foreground">
												{galleryIndex + 1} / {gallery.length}
											</span>
											<Button
												variant="ghost"
												size="icon-sm"
												className="size-11 shrink-0 sm:size-11"
												onClick={() => navigateGallery(1)}
												aria-label="Next media"
											>
												<ChevronRight className="h-4 w-4" />
											</Button>
										</>
									)}
									<Button
										variant="outline"
										size="sm"
										className="h-11 shrink-0 sm:h-11"
										onClick={download}
										disabled={downloading}
									>
										{downloading ? (
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
										) : (
											<Download className="mr-2 h-4 w-4" />
										)}
										Download
									</Button>
								</div>
							</div>
						{downloadError?.id === attachment.id && <p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">{downloadError.message}</p>}
						</SheetHeader>
						<div className="flex min-h-0 flex-1 overflow-auto">
							{isImageAttachment(attachment.content_type) ||
							isVideoAttachment(attachment.content_type) ||
							attachment.content_type === "application/pdf" ? (
								<AuthenticatedBinaryPreview
									key={attachment.id}
									url={previewUrl}
									attachment={attachment}
								/>
							) : (
								<AuthenticatedTextPreview
									key={attachment.id}
									url={previewUrl}
									attachment={attachment}
								/>
							)}
						</div>
					</>
				)}
			</SheetContent>
		</Sheet>
	);
}
