import { FileSpreadsheet, FileText, Image, Presentation } from "lucide-react";

import { cn } from "@/lib/utils";
import {
	attachmentContentUrl,
	formatBytes,
	isImageAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";
import { FilePreviewSheet } from "./FilePreviewSheet";
import { useState } from "react";

function FileIcon({ attachment }: { attachment: AttachmentPublic }) {
	if (isImageAttachment(attachment.content_type)) {
		return <Image className="h-5 w-5" />;
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
					"mb-2 flex flex-wrap gap-2",
					variant === "attachment"
						? "justify-end"
						: "justify-start px-4",
				)}
			>
				{attachments.map((attachment) => {
					const previewUrl = attachmentContentUrl(
						conversationId,
						attachment.id,
					);
					return (
						<button
							type="button"
							key={attachment.id}
							onClick={() => setPreview(attachment)}
							className={cn(
								"group/file flex max-w-72 items-center gap-2.5 rounded-xl border p-2.5 text-left outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 motion-reduce:transition-none",
								variant === "attachment"
									? "border-primary-foreground/20 bg-primary-foreground/10 hover:bg-primary-foreground/15"
									: "animate-in fade-in-0 slide-in-from-bottom-1 border-border bg-card text-card-foreground shadow-sm hover:bg-accent/60 motion-reduce:animate-none",
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
							<span className="min-w-0">
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
					);
				})}
			</div>

			<FilePreviewSheet
				conversationId={conversationId}
				attachment={preview}
				onOpenChange={(open) => !open && setPreview(null)}
			/>
		</>
	);
}
