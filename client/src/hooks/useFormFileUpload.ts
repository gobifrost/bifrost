/**
 * Hook for handling form file uploads via presigned S3 URLs.
 *
 * Flow:
 * 1. Request presigned URL from API
 * 2. Upload file directly to S3
 * 3. Return the file path (without uploads/ prefix) for form submission
 */

import { useState, useCallback } from "react";
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

type FileUploadResponse = components["schemas"]["FileUploadResponse"];

export interface UploadProgress {
	loaded: number;
	total: number;
	percentage: number;
}

export interface UploadedFile {
	/** Path for workflow (without uploads/ prefix) */
	path: string;
	/** Original filename */
	name: string;
	/** File size in bytes */
	size: number;
	/** MIME type */
	contentType: string;
}

export interface UploadState {
	status: "idle" | "getting_url" | "uploading" | "completed" | "error";
	progress: UploadProgress | null;
	error: string | null;
	file: UploadedFile | null;
}

const initialState: UploadState = {
	status: "idle",
	progress: null,
	error: null,
	file: null,
};

interface UseFormFileUploadOptions {
	orgId?: string;
	/** Called when upload starts */
	onUploadStart?: () => void;
	/** Called when upload completes or errors */
	onUploadEnd?: () => void;
	/** Maximum file size in MB (optional validation) */
	maxSizeMb?: number | null;
	/** Allowed MIME types (optional validation) */
	allowedTypes?: string[] | null;
	/** Field name for server-side validation */
	fieldName?: string;
}

interface UseFormFileUploadReturn {
	/** Upload a single file, returns the path for form submission */
	uploadFile: (
		file: File,
		onProgress?: (progress: UploadProgress) => void,
	) => Promise<string>;
	/** Current upload state */
	uploadState: UploadState;
	/** Reset state to idle */
	reset: () => void;
}

/**
 * Hook for uploading files to S3 via presigned URLs.
 *
 * @param formId - The form ID to upload files for
 * @param options - Optional callbacks and validation
 */
export function useFormFileUpload(
	formId: string,
	options: UseFormFileUploadOptions = {},
): UseFormFileUploadReturn {
	const [uploadState, setUploadState] = useState<UploadState>(initialState);
	const {
		orgId,
		onUploadStart,
		onUploadEnd,
		maxSizeMb,
		allowedTypes,
		fieldName,
	} = options;

	const reset = useCallback(() => {
		setUploadState(initialState);
	}, []);

	const uploadFile = useCallback(
		async (
			file: File,
			onProgress?: (progress: UploadProgress) => void,
		): Promise<string> => {
			// Client-side validation
			if (maxSizeMb && file.size > maxSizeMb * 1024 * 1024) {
				const error = `File size exceeds ${maxSizeMb}MB limit`;
				setUploadState({
					status: "error",
					progress: null,
					error,
					file: null,
				});
				throw new Error(error);
			}

			if (allowedTypes && allowedTypes.length > 0) {
				const isAllowed = allowedTypes.some((type) => {
					if (type.endsWith("/*")) {
						// Wildcard match (e.g., "image/*")
						const prefix = type.slice(0, -1);
						return file.type.startsWith(prefix);
					}
					if (type.startsWith(".")) {
						// Extension match (e.g., ".pdf")
						return file.name
							.toLowerCase()
							.endsWith(type.toLowerCase());
					}
					// Exact MIME type match
					return file.type === type;
				});

				if (!isAllowed) {
					const error = `File type not allowed. Allowed: ${allowedTypes.join(", ")}`;
					setUploadState({
						status: "error",
						progress: null,
						error,
						file: null,
					});
					throw new Error(error);
				}
			}

			onUploadStart?.();

			try {
				// Step 1: Get presigned URL
				setUploadState({
					status: "getting_url",
					progress: null,
					error: null,
					file: null,
				});

				const { data, error } = await apiClient.POST(
					"/api/forms/{form_id}/upload",
					{
						params: {
							path: { form_id: formId },
							...(orgId ? { query: { org_id: orgId } } : {}),
						},
						body: {
							file_name: file.name,
							content_type:
								file.type || "application/octet-stream",
							file_size: file.size,
							field_name: fieldName,
						},
					},
				);

				if (error || !data) {
					const errorMsg =
						(error as { message?: string })?.message ||
						"Failed to get upload URL";
					setUploadState({
						status: "error",
						progress: null,
						error: errorMsg,
						file: null,
					});
					onUploadEnd?.();
					throw new Error(errorMsg);
				}

				const uploadResponse = data as FileUploadResponse;

				// Step 2: Upload to S3 using XMLHttpRequest for progress
				setUploadState({
					status: "uploading",
					progress: { loaded: 0, total: file.size, percentage: 0 },
					error: null,
					file: null,
				});

				await new Promise<void>((resolve, reject) => {
					const xhr = new XMLHttpRequest();

					xhr.upload.addEventListener("progress", (event) => {
						if (event.lengthComputable) {
							const percentage = Math.round(
								(event.loaded / event.total) * 100,
							);
							const progress = {
								loaded: event.loaded,
								total: event.total,
								percentage,
							};
							setUploadState((prev) => ({
								...prev,
								progress,
							}));
							onProgress?.(progress);
						}
					});

					xhr.addEventListener("load", () => {
						if (xhr.status >= 200 && xhr.status < 300) {
							resolve();
						} else {
							reject(
								new Error(
									`Upload failed with status ${xhr.status}`,
								),
							);
						}
					});

					xhr.addEventListener("error", () => {
						reject(new Error("Network error during upload"));
					});

					xhr.addEventListener("abort", () => {
						reject(new Error("Upload aborted"));
					});

					xhr.open("PUT", uploadResponse.upload_url);
					xhr.setRequestHeader(
						"Content-Type",
						file.type || "application/octet-stream",
					);
					xhr.send(file);
				});

				// Step 3: Extract path for workflow (strip uploads/ prefix)
				const blobUri = uploadResponse.blob_uri;
				const pathForWorkflow = blobUri.startsWith("uploads/")
					? blobUri.slice("uploads/".length)
					: blobUri;

				const uploadedFile: UploadedFile = {
					path: pathForWorkflow,
					name: file.name,
					size: file.size,
					contentType: file.type || "application/octet-stream",
				};

				setUploadState({
					status: "completed",
					progress: {
						loaded: file.size,
						total: file.size,
						percentage: 100,
					},
					error: null,
					file: uploadedFile,
				});

				onUploadEnd?.();
				return pathForWorkflow;
			} catch (err) {
				const errorMsg =
					err instanceof Error ? err.message : "Upload failed";
				setUploadState((prev) => ({
					...prev,
					status: "error",
					error: errorMsg,
				}));
				onUploadEnd?.();
				throw err;
			}
		},
		[
			orgId,
			formId,
			maxSizeMb,
			allowedTypes,
			fieldName,
			onUploadStart,
			onUploadEnd,
		],
	);

	return {
		uploadFile,
		uploadState,
		reset,
	};
}
