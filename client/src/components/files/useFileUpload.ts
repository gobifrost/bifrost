import { useState } from "react";
import { toast } from "sonner";
import { files } from "@/lib/app-sdk/files";

/**
 * Shared upload logic for the explorer: signed-PUT each file to
 * `{location}/{scope}/{prefix}/{name}` via the SDK, then fire `onUploaded` so
 * the listing refetches. Used by both the header Upload button and the
 * folder dropzone/drop handler so the behavior is identical.
 */
export function useFileUpload(
	location: string | null,
	scope: string | null,
	prefix: string,
	onUploaded: () => void,
) {
	const [uploading, setUploading] = useState(false);

	async function uploadFiles(fileList: FileList | File[]) {
		if (location === null) return;
		const list = Array.from(fileList);
		if (list.length === 0) return;
		setUploading(true);
		try {
			for (const file of list) {
				const targetPath = prefix
					? `${prefix.replace(/\/$/, "")}/${file.name}`
					: file.name;
				await files.upload(targetPath, file, { location, scope });
			}
			toast.success("Upload complete");
			onUploaded();
		} catch (err) {
			toast.error("Upload failed", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setUploading(false);
		}
	}

	return { uploading, uploadFiles };
}
