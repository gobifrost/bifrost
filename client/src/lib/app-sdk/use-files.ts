import { useEffect, useMemo, useRef, useState } from "react";
import {
	FileAccessDeniedError,
	files,
	type FileListMetadataItem,
	type FileMode,
} from "./files";
import { subscribeToFiles, type FileChangeMessage } from "./ws-client";

export interface UseFilesOptions {
	location?: string;
	mode?: FileMode;
	scope?: string | null;
	includeMetadata?: boolean;
}

export interface UseFilesResult {
	files: string[];
	filesMetadata: FileListMetadataItem[];
	loading: boolean;
	error: Error | null;
	denied: boolean;
	empty: boolean;
	refetch: () => Promise<void>;
}

export function useFiles(
	prefix: string,
	options: UseFilesOptions = {},
): UseFilesResult {
	const {
		location = "workspace",
		mode = "cloud",
		scope = null,
		includeMetadata = false,
	} = options;
	const [fileNames, setFileNames] = useState<string[]>([]);
	const [filesMetadata, setFilesMetadata] = useState<FileListMetadataItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);
	const [denied, setDenied] = useState(false);
	const unsubscribeRef = useRef<(() => void) | null>(null);

	const optionsKey = useMemo(
		() => JSON.stringify({ location, mode, scope, includeMetadata }),
		[location, mode, scope, includeMetadata],
	);

	async function load() {
		try {
			const result = await files.list(prefix, {
				location,
				mode,
				scope,
				includeMetadata,
			});
			setFileNames(result.files);
			setFilesMetadata(result.filesMetadata);
			setDenied(false);
			setError(null);
		} catch (err) {
			const next = err instanceof Error ? err : new Error(String(err));
			setError(next);
			setFileNames([]);
			setFilesMetadata([]);
			setDenied(next instanceof FileAccessDeniedError);
		} finally {
			setLoading(false);
		}
	}

	useEffect(() => {
		let cancelled = false;

		async function loadIfCurrent() {
			if (cancelled) return;
			await load();
		}

		loadIfCurrent();

		unsubscribeRef.current = subscribeToFiles(
			location,
			prefix,
			scope,
			(evt: FileChangeMessage) => {
				if (evt.type === "subscription_revoked") {
					unsubscribeRef.current?.();
					unsubscribeRef.current = null;
					setDenied(true);
					setError(new FileAccessDeniedError("File subscription revoked"));
					return;
				}
				if (evt.type === "error") {
					setError(new Error(evt.message || "File subscription error"));
					return;
				}
				if (evt.type === "file_change") {
					void loadIfCurrent();
				}
			},
		);

		return () => {
			cancelled = true;
			unsubscribeRef.current?.();
			unsubscribeRef.current = null;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [prefix, optionsKey]);

	return {
		files: fileNames,
		filesMetadata,
		loading,
		error,
		denied,
		empty: !loading && !denied && fileNames.length === 0,
		refetch: load,
	};
}
