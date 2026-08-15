import { useEffect, useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { authFetch } from "@/lib/api-client";
import {
	attachmentContentUrl,
	isImageAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";

function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function TextPreview({ url }: { url: string }) {
	const [text, setText] = useState<string | null>(null);
	const [error, setError] = useState(false);

	useEffect(() => {
		let cancelled = false;
		authFetch(url)
			.then((response) => {
				if (!response.ok) throw new Error("Preview failed");
				return response.text();
			})
			.then((content) => {
				if (!cancelled) setText(content);
			})
			.catch(() => {
				if (!cancelled) setError(true);
			});
		return () => {
			cancelled = true;
		};
	}, [url]);

	if (error) {
		return <p className="p-6 text-sm text-muted-foreground">Preview unavailable.</p>;
	}
	if (text === null) {
		return (
			<div className="flex min-h-48 items-center justify-center">
				<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
			</div>
		);
	}
	return (
		<pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap p-4 text-xs">
			{text}
		</pre>
	);
}

function AttachmentPreview({
	conversationId,
	attachment,
}: {
	conversationId: string;
	attachment: AttachmentPublic;
}) {
	const url = attachmentContentUrl(conversationId, attachment.id);
	if (isImageAttachment(attachment.content_type)) {
		return (
			<div className="flex max-h-[70vh] items-center justify-center overflow-auto bg-muted/30 p-3">
				<img
					src={url}
					alt={attachment.filename}
					className="max-h-[65vh] max-w-full rounded-lg object-contain"
				/>
			</div>
		);
	}
	if (attachment.content_type === "application/pdf") {
		return <iframe title={attachment.filename} src={url} className="h-[70vh] w-full" />;
	}
	return <TextPreview url={url} />;
}

export function ChatAttachmentList({
	conversationId,
	attachments,
}: {
	conversationId: string;
	attachments: AttachmentPublic[];
}) {
	const [preview, setPreview] = useState<AttachmentPublic | null>(null);
	if (attachments.length === 0) return null;

	return (
		<>
			<div className="mb-2 flex flex-wrap justify-end gap-2">
				{attachments.map((attachment) => {
					const previewUrl = attachmentContentUrl(conversationId, attachment.id);
					return (
						<button
							type="button"
							key={attachment.id}
							onClick={() => setPreview(attachment)}
							className="flex max-w-64 items-center gap-2 rounded-xl border border-primary-foreground/20 bg-primary-foreground/10 p-2 text-left transition-colors hover:bg-primary-foreground/15"
						>
							{isImageAttachment(attachment.content_type) ? (
								<img
									src={previewUrl}
									alt=""
									className="h-12 w-12 rounded-lg object-cover"
								/>
							) : (
								<span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-foreground/10">
									<FileText className="h-5 w-5" />
								</span>
							)}
							<span className="min-w-0">
								<span className="block truncate text-xs font-medium">
									{attachment.filename}
								</span>
								<span className="block text-[10px] opacity-70">
									{formatBytes(attachment.size_bytes)}
								</span>
							</span>
						</button>
					);
				})}
			</div>

			<Dialog open={preview !== null} onOpenChange={(open) => !open && setPreview(null)}>
				<DialogContent className="max-w-4xl overflow-hidden p-0">
					{preview && (
						<>
							<DialogHeader className="flex-row items-center justify-between gap-4 border-b px-5 py-4 text-left">
								<div className="min-w-0">
									<DialogTitle className="truncate">{preview.filename}</DialogTitle>
									<DialogDescription>{formatBytes(preview.size_bytes)}</DialogDescription>
								</div>
								<Button asChild variant="outline" size="sm" className="mr-7 shrink-0">
									<a
										href={attachmentContentUrl(conversationId, preview.id, {
											download: true,
										})}
									>
										<Download className="mr-2 h-4 w-4" />
										Download
									</a>
								</Button>
							</DialogHeader>
							<AttachmentPreview conversationId={conversationId} attachment={preview} />
						</>
					)}
				</DialogContent>
			</Dialog>
		</>
	);
}
