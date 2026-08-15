import { useEffect, useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
	formatBytes,
	isImageAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";

const DOCX =
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const XLSX =
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function LoadingPreview({ label = "Preparing preview" }: { label?: string }) {
	return (
		<div className="flex min-h-64 flex-1 items-center justify-center bg-muted/20">
			<div className="flex items-center gap-2 text-sm text-muted-foreground">
				<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
				{label}
			</div>
		</div>
	);
}

function PreviewError({ onRetry }: { onRetry: () => void }) {
	return (
		<div className="flex min-h-64 flex-1 items-center justify-center p-8 text-center">
			<div>
				<FileText className="mx-auto mb-3 h-7 w-7 text-muted-foreground" />
				<p className="text-sm font-medium">Preview unavailable</p>
				<p className="mt-1 text-sm text-muted-foreground">
					Try again, or download the file to open it locally.
				</p>
				<Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
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
				<div className="absolute inset-0 z-10 flex items-center justify-center bg-background/90 transition-opacity">
					<LoadingPreview label="Loading preview" />
				</div>
			)}
			{isImageAttachment(attachment.content_type) ? (
				<div className="flex flex-1 items-center justify-center overflow-auto p-5">
					<img
						src={blobUrl}
						alt={attachment.filename}
						onLoad={() => setLoaded(true)}
						className="max-h-full max-w-full rounded-lg object-contain shadow-sm"
					/>
				</div>
			) : (
				<iframe
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
			<iframe
				title={attachment.filename}
				sandbox=""
				srcDoc={content}
				className="h-full min-h-[70vh] w-full bg-white"
			/>
		);
	}
	return (
		<pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap p-5 font-mono text-xs leading-5">
			{content}
		</pre>
	);
}

export function FilePreviewSheet({
	conversationId,
	attachment,
	onOpenChange,
}: {
	conversationId: string;
	attachment: AttachmentPublic | null;
	onOpenChange: (open: boolean) => void;
}) {
	const [downloading, setDownloading] = useState(false);
	const isOfficePreview =
		attachment?.content_type === DOCX || attachment?.content_type === XLSX;
	const previewUrl = attachment
		? attachmentContentUrl(conversationId, attachment.id, {
				preview: isOfficePreview,
			})
		: "";

	const download = async () => {
		if (!attachment) return;
		setDownloading(true);
		try {
			const response = await authFetch(
				attachmentContentUrl(conversationId, attachment.id, {
					download: true,
				}),
			);
			if (!response.ok) throw new Error("Download failed");
			const blobUrl = URL.createObjectURL(await response.blob());
			const anchor = document.createElement("a");
			anchor.href = blobUrl;
			anchor.download = attachment.filename;
			anchor.click();
			URL.revokeObjectURL(blobUrl);
		} catch {
			toast.error("Download failed");
		} finally {
			setDownloading(false);
		}
	};

	return (
		<Sheet open={attachment !== null} onOpenChange={onOpenChange}>
			<SheetContent className="w-full p-0 sm:max-w-2xl lg:max-w-3xl">
				{attachment && (
					<>
						<SheetHeader className="shrink-0 border-b px-5 py-4 pr-14">
							<div className="flex items-center justify-between gap-4">
								<div className="min-w-0">
									<SheetTitle className="truncate">
										{attachment.filename}
									</SheetTitle>
									<SheetDescription>
										{formatBytes(attachment.size_bytes)}
									</SheetDescription>
								</div>
								<Button
									variant="outline"
									size="sm"
									className="shrink-0"
									onClick={download}
									disabled={downloading}
								>
									{downloading ? (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									) : (
										<Download className="mr-2 h-4 w-4" />
									)}
									Download
								</Button>
							</div>
						</SheetHeader>
						<div className="flex min-h-0 flex-1 overflow-auto">
							{isImageAttachment(attachment.content_type) ||
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
