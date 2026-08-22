import {
	FileSpreadsheet,
	FileText,
	Download,
	Image,
	Presentation,
	Video,
} from "lucide-react";

import { cn } from "@/lib/utils";
import {
	attachmentContentUrl,
	downloadChatAttachment,
	formatBytes,
	isImageAttachment,
	isVideoAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";
import { FilePreviewSheet } from "./FilePreviewSheet";
import { useState } from "react";
import { toast } from "sonner";

function FileIcon({ attachment }: { attachment: AttachmentPublic }) {
	if (isImageAttachment(attachment.content_type)) {
		return <Image className="h-5 w-5" />;
	}
	if (isVideoAttachment(attachment.content_type)) {
		return <Video className="h-5 w-5" />;
	}
	if (attachment.content_type.includes("spreadsheet")) {
		return <FileSpreadsheet className="h-5 w-5" />;
	}
	if (attachment.content_type.includes("presentation")) {
		return <Presentation className="h-5 w-5" />;
	}
	return <FileText className="h-5 w-5" />;
}

export function ChatAttachmentList({
	conversationId,
	attachments,
	variant = "attachment",
}: {
	conversationId: string;
	attachments: AttachmentPublic[];
	variant?: "attachment" | "artifact";
}) {
	const [preview, setPreview] = useState<AttachmentPublic | null>(null);
	if (attachments.length === 0) return null;

	return (
		<>
			<div
				className={cn(
					"mb-2 flex gap-2",
					variant === "attachment"
						? "flex-wrap justify-end"
						: "w-full flex-col items-stretch px-4",
				)}
			>
				{attachments.map((attachment) => {
					const previewUrl = attachmentContentUrl(
						conversationId,
						attachment.id,
					);
					return (
						<div
							key={attachment.id}
							className={cn(
								"group/file flex items-center rounded-xl border p-1.5 text-left transition-colors duration-150 motion-reduce:transition-none",
								variant === "attachment"
									? "max-w-72 border-primary-foreground/20 bg-primary-foreground/10 hover:bg-primary-foreground/15"
									: "w-full max-w-none animate-in fade-in-0 slide-in-from-bottom-1 border-border bg-card text-card-foreground shadow-sm hover:bg-accent/60 motion-reduce:animate-none",
							)}
						>
							<button
								type="button"
								onClick={() => setPreview(attachment)}
								aria-label={`Preview ${attachment.filename}`}
								className={cn(
									"flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
									variant === "artifact" && "w-full max-w-none",
								)}
							>
							{isImageAttachment(attachment.content_type) ? (
								<img
									src={previewUrl}
									alt=""
									className="h-11 w-11 rounded-lg object-cover"
								/>
							) : (
								<span
									className={cn(
										"flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
										variant === "attachment"
											? "bg-primary-foreground/10"
											: "bg-primary/10 text-primary",
									)}
								>
									<FileIcon attachment={attachment} />
								</span>
							)}
							<span className="min-w-0 flex-1">
								<span className="block truncate text-xs font-medium">
									{attachment.filename}
								</span>
								<span className="block text-[11px] opacity-65">
									{variant === "artifact"
										? "Generated file · "
										: ""}
									{formatBytes(attachment.size_bytes)}
								</span>
							</span>
							</button>
							<button
								type="button"
								className="flex size-11 shrink-0 items-center justify-center rounded-lg opacity-60 hover:bg-background/60 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:size-8"
								onClick={() => {
									void downloadChatAttachment(conversationId, attachment).catch(() =>
										toast.error("Download failed"),
									);
								}}
								aria-label={`Download ${attachment.filename}`}
							>
								<Download className="h-4 w-4" />
							</button>
						</div>
					);
				})}
			</div>

			<FilePreviewSheet
				conversationId={conversationId}
				attachment={preview}
				attachments={attachments}
				onAttachmentChange={setPreview}
				onOpenChange={(open) => !open && setPreview(null)}
			/>
		</>
	);
}
