import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { files } from "@/lib/app-sdk/files";

interface FilePreviewProps {
	location: string;
	scope: string | null;
	path: string | null;
}

export function previewKind(path: string): "text" | "image" | "download" {
	const leaf = path.split("/").at(-1) ?? path;
	const ext = leaf.includes(".")
		? (leaf.split(".").at(-1)?.toLowerCase() ?? "")
		: "";
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

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			setText(null);
			setImageUrl(null);
			if (!path) return;
			const kind = previewKind(path);
			if (kind === "text") {
				try {
					const content = await files.read(path, { location, scope });
					if (!cancelled) setText(content.slice(0, 6000));
				} catch (err) {
					if (!cancelled)
						setText(err instanceof Error ? err.message : String(err));
				}
			} else if (kind === "image") {
				try {
					const signed = await files.signedUrl(path, {
						method: "GET",
						location,
						scope,
					});
					if (!cancelled) setImageUrl(signed.url);
				} catch {
					if (!cancelled) setImageUrl(null);
				}
			}
		})();
		return () => {
			cancelled = true;
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
				{kind === "text" && (
					<pre className="whitespace-pre-wrap break-words font-mono">
						{text ?? "Loading preview…"}
					</pre>
				)}
				{kind === "image" && (
					<div className="flex h-full items-center justify-center p-3">
						{imageUrl ? (
							<img
								src={imageUrl}
								alt={path}
								// Bordered + muted backdrop so tiny or transparent
								// images are still visibly framed (seed/placeholder
								// images can be 1×1 and would otherwise look blank).
								className="max-h-full max-w-full rounded-md bg-muted object-contain ring-1 ring-border"
								style={{ minWidth: "2.5rem", minHeight: "2.5rem" }}
							/>
						) : (
							<span className="text-muted-foreground">Loading image…</span>
						)}
					</div>
				)}
				{kind === "download" && (
					<div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
						<Download className="h-6 w-6" />
						<p>Preview unavailable. Download this file to inspect it.</p>
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
