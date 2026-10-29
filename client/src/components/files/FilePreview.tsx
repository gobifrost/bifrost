import { useEffect, useState } from "react";
import { Download, FileWarning, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { files } from "@/lib/app-sdk/files";

interface FilePreviewProps {
	location: string;
	scope: string | null;
	solution?: string;
	path: string | null;
}

function extOf(path: string): string {
	const leaf = path.split("/").at(-1) ?? path;
	return leaf.includes(".")
		? (leaf.split(".").at(-1)?.toLowerCase() ?? "")
		: "";
}

export function previewKind(path: string): "text" | "image" | "download" {
	const ext = extOf(path);
	if (
		[
			"txt",
			"md",
			"json",
			"yaml",
			"yml",
			"csv",
			"log",
			"ts",
			"tsx",
			"js",
			"py",
		].includes(ext)
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

async function downloadFile(
	path: string,
	location: string,
	scope: string | null,
	solution?: string,
) {
	const blob = await files.download(
		path,
		solution ? { location, scope, solution } : { location, scope },
	);
	if (typeof URL.createObjectURL !== "function")
		throw new Error("Downloads are unavailable in this browser.");
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = path.split("/").at(-1) ?? "download";
	try {
		link.click();
	} finally {
		URL.revokeObjectURL(url);
	}
}

function PreviewSession({ location, scope, solution, path }: FilePreviewProps) {
	const [text, setText] = useState<string | null>(null);
	const [imageUrl, setImageUrl] = useState<string | null>(null);
	const [loading, setLoading] = useState(
		!!path && previewKind(path) !== "download",
	);
	const [attempt, setAttempt] = useState(0);
	const [truncated, setTruncated] = useState(false);
	const [downloading, setDownloading] = useState(false);
	const [downloadError, setDownloadError] = useState(false);
	async function download() {
		if (!path || downloading) return;
		setDownloading(true);
		setDownloadError(false);
		try {
			await downloadFile(path, location, scope, solution);
		} catch {
			setDownloadError(true);
		} finally {
			setDownloading(false);
		}
	}
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
					const content = await files.read(
						path,
						solution
							? { location, scope, solution }
							: { location, scope },
					);
					if (!cancelled) {
						setText(content.slice(0, 6000));
						setTruncated(content.length > 6000);
					}
				} catch (err) {
					if (!cancelled)
						setError(
							err instanceof Error ? err.message : String(err),
						);
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
					const bytes = await files.readBytes(
						path,
						solution
							? { location, scope, solution }
							: { location, scope },
					);
					if (cancelled) return;
					const type =
						IMAGE_MIME[extOf(path)] ?? "application/octet-stream";
					// Copy into a plain ArrayBuffer-backed view so the Blob part
					// type is concrete (readBytes returns Uint8Array<ArrayBufferLike>).
					objectUrl = URL.createObjectURL(
						new Blob([new Uint8Array(bytes)], { type }),
					);
					setImageUrl(objectUrl);
				} catch (err) {
					if (!cancelled)
						setError(
							err instanceof Error ? err.message : String(err),
						);
				} finally {
					if (!cancelled) setLoading(false);
				}
			}
		})();
		return () => {
			cancelled = true;
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [path, location, scope, solution, attempt]);

	if (!path) {
		return (
			<div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
				Select a file to preview.
			</div>
		);
	}

	const kind = previewKind(path);
	return (
		<section
			aria-label="File preview"
			className="flex h-full min-h-0 min-w-0 flex-col"
		>
			<div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 pt-3">
				<span className="text-xs text-muted-foreground">
					{kind === "text"
						? "Text Preview"
						: kind === "image"
							? "Image Preview"
							: "File Download"}
				</span>
				<Button
					variant="outline"
					className="min-h-11"
					disabled={downloading}
					onClick={() => void download()}
				>
					<Download aria-hidden="true" />
					{downloading
						? "Downloading…"
						: error
							? "Download instead"
							: "Download"}
				</Button>
				{downloading && (
					<p role="status" className="text-sm text-muted-foreground">
						Preparing download…
					</p>
				)}
				{downloadError && (
					<div role="alert" className="space-y-2 text-sm">
						<p className="text-destructive">
							Couldn’t download this file. Try again.
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							onClick={() => void download()}
						>
							Retry download
						</Button>
					</div>
				)}
			</div>
			<div className="min-h-0 min-w-0 flex-1 overflow-auto p-4 text-sm">
				{loading ? (
					<div
						role="status"
						className="flex min-h-24 flex-col items-center justify-center gap-2 text-muted-foreground"
					>
						<Loader2
							aria-hidden="true"
							className="size-6 animate-spin motion-reduce:animate-none"
						/>
						<span>Loading preview…</span>
					</div>
				) : error ? (
					<div
						role="alert"
						className="flex min-h-24 flex-col items-center justify-center gap-3 text-center"
					>
						<FileWarning
							aria-hidden="true"
							className="size-6 text-muted-foreground"
						/>
						<p>Couldn’t load this file’s preview.</p>
						<Button
							variant="outline"
							className="min-h-11"
							onClick={() => setAttempt((value) => value + 1)}
						>
							Retry preview
						</Button>
					</div>
				) : kind === "text" ? (
					<>
						{truncated && (
							<p className="mb-3 text-sm text-muted-foreground">
								Showing the first 6,000 characters. Download to
								read the complete file.
							</p>
						)}
						{text === "" ? (
							<p className="text-muted-foreground">
								This file is empty.
							</p>
						) : (
							<pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed [overflow-wrap:anywhere]">
								{text}
							</pre>
						)}
					</>
				) : kind === "image" && imageUrl ? (
					<div className="flex min-h-24 items-center justify-center">
						<img
							src={imageUrl}
							alt={path}
							onError={() =>
								setError("Image could not be displayed")
							}
							className="max-h-full min-h-10 min-w-10 max-w-full rounded-[var(--bf-radius-surface)] border border-border bg-muted object-contain"
						/>
					</div>
				) : (
					<p className="py-4 text-muted-foreground">
						No inline preview for this file type.
					</p>
				)}
			</div>
		</section>
	);
}

export function FilePreview(props: FilePreviewProps) {
	return (
		<PreviewSession
			key={JSON.stringify([props.location, props.scope, props.path])}
			{...props}
		/>
	);
}
