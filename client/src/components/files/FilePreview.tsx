import { useEffect, useState } from "react";
import { Download, FileWarning, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { files } from "@/lib/app-sdk/files";

interface FilePreviewProps {
	location: string;
	scope: string | null;
	path: string | null;
}

function extOf(path: string): string {
	const leaf = path.split("/").at(-1) ?? path;
	return leaf.includes(".") ? (leaf.split(".").at(-1)?.toLowerCase() ?? "") : "";
}

export function previewKind(path: string): "text" | "image" | "download" {
	const ext = extOf(path);
	if (
		["txt", "md", "json", "yaml", "yml", "csv", "log", "ts", "tsx", "js", "py"].includes(
			ext,
		)
	) {
		return "text";
	}
	if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) {
		return "image";
	}
	return "download";
}

const IMAGE_MIME: Record<string, string> = {
	png: "image/png",
	jpg: "image/jpeg",
	jpeg: "image/jpeg",
	gif: "image/gif",
	webp: "image/webp",
	svg: "image/svg+xml",
};

async function downloadFile(path: string, location: string, scope: string | null) {
	const blob = await files.download(path, { location, scope });
	if (typeof URL.createObjectURL !== "function") return;
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = path.split("/").at(-1) ?? "download";
	link.click();
	URL.revokeObjectURL(url);
}

export function FilePreview({ location, scope, path }: FilePreviewProps) {
	const [text, setText] = useState<string | null>(null);
	const [imageUrl, setImageUrl] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		let objectUrl: string | null = null;
		void (async () => {
			setText(null);
			setImageUrl(null);
			setError(null);
			if (!path) return;
			const kind = previewKind(path);
			if (kind === "text") {
				setLoading(true);
				try {
					const content = await files.read(path, { location, scope });
					if (!cancelled) setText(content.slice(0, 6000));
				} catch (err) {
					if (!cancelled)
						setError(err instanceof Error ? err.message : String(err));
				} finally {
					if (!cancelled) setLoading(false);
				}
			} else if (kind === "image") {
				setLoading(true);
				try {
					// Read the bytes through the authenticated API (not a
					// presigned S3 URL), so previews work regardless of whether
					// the S3 origin is browser-reachable. Render via a blob
					// object URL with the right MIME type.
					const bytes = await files.readBytes(path, { location, scope });
					if (cancelled) return;
					const type = IMAGE_MIME[extOf(path)] ?? "application/octet-stream";
					objectUrl = URL.createObjectURL(new Blob([bytes], { type }));
					setImageUrl(objectUrl);
				} catch (err) {
					if (!cancelled)
						setError(err instanceof Error ? err.message : String(err));
				} finally {
					if (!cancelled) setLoading(false);
				}
			}
		})();
		return () => {
			cancelled = true;
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [path, location, scope]);

	if (!path) {
		return (
			<div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
				Select a file to preview.
			</div>
		);
	}

	const kind = previewKind(path);
	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-b px-3 py-2">
				<p className="truncate text-sm font-medium" title={path}>
					{path.split("/").at(-1)}
				</p>
			</div>
			<div className="min-h-0 flex-1 overflow-auto p-3 text-xs">
				{loading ? (
					<div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
						<Loader2 className="h-6 w-6 animate-spin" />
						<span>Loading preview…</span>
					</div>
				) : error ? (
					<div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
						<FileWarning className="h-6 w-6" />
						<p className="max-w-[80%] text-center">
							Couldn’t load this file’s preview.
						</p>
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={() => void downloadFile(path, location, scope)}
						>
							<Download className="h-4 w-4" /> Download instead
						</Button>
					</div>
				) : kind === "text" ? (
					<pre className="whitespace-pre-wrap break-words font-mono">{text}</pre>
				) : kind === "image" && imageUrl ? (
					<div className="flex h-full items-center justify-center p-3">
						<img
							src={imageUrl}
							alt={path}
							// Bordered + muted backdrop so tiny or transparent
							// images are still visibly framed (seed/placeholder
							// images can be 1×1 and would otherwise look blank).
							className="max-h-full max-w-full rounded-md bg-muted object-contain ring-1 ring-border"
							style={{ minWidth: "2.5rem", minHeight: "2.5rem" }}
						/>
					</div>
				) : (
					<div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
						<Download className="h-6 w-6" />
						<p>No inline preview for this file type.</p>
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={() => void downloadFile(path, location, scope)}
						>
							<Download className="h-4 w-4" /> Download
						</Button>
					</div>
				)}
			</div>
		</div>
	);
}
