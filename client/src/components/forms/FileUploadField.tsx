/**
 * File upload field component for forms.
 *
 * Handles single and multiple file uploads via presigned S3 URLs.
 * Shows upload progress, uploaded file list, and error states.
 */

import { useState, useCallback, useRef, useId } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Upload, X, File, Loader2, AlertCircle, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import {
	useFormFileUpload,
	type UploadProgress,
} from "@/hooks/useFormFileUpload";

interface FileUploadFieldProps {
	orgId?: string;
	formId: string;
	fieldName: string;
	label: string | null;
	required: boolean;
	helpText: string | null;
	allowedTypes: string[] | null;
	multiple: boolean | null;
	maxSizeMb: number | null;
	/** Current value - single path string or array of paths */
	value: string | string[] | null;
	/** Called when value changes */
	onChange: (value: string | string[] | null) => void;
	/** Called when any upload starts */
	onUploadStart?: () => void;
	/** Called when all uploads complete */
	onUploadEnd?: () => void;
	/** Field validation error */
	error?: { message?: string };
}

interface UploadingFile {
	id: string;
	name: string;
	progress: UploadProgress | null;
	status: "uploading" | "error";
	error?: string;
	file: File;
}

interface CompletedFile {
	path: string;
	name: string;
	size: number;
}

function formatFileSize(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileUploadField({
	orgId,
	formId,
	fieldName,
	label,
	required,
	helpText,
	allowedTypes,
	multiple,
	maxSizeMb,
	value,
	onChange,
	onUploadStart,
	onUploadEnd,
	error,
}: FileUploadFieldProps) {
	const inputRef = useRef<HTMLInputElement>(null);
	const id = useId();
	const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
	const [isDragOver, setIsDragOver] = useState(false);
	const activeUploadsRef = useRef(0);

	const { uploadFile } = useFormFileUpload(formId, {
		orgId,
		maxSizeMb,
		allowedTypes,
		fieldName,
	});

	// Parse current value into completed files list
	const completedFiles: CompletedFile[] = (() => {
		if (!value) return [];
		const paths = Array.isArray(value) ? value : [value];
		return paths.map((path) => ({
			path,
			name: path.split("/").pop() || path,
			size: 0, // Size not stored, would need metadata
		}));
	})();

	const handleUploadStart = useCallback(() => {
		if (activeUploadsRef.current === 0) {
			onUploadStart?.();
		}
		activeUploadsRef.current++;
	}, [onUploadStart]);

	const handleUploadEnd = useCallback(() => {
		activeUploadsRef.current--;
		if (activeUploadsRef.current === 0) {
			onUploadEnd?.();
		}
	}, [onUploadEnd]);

	const processFiles = useCallback(
		async (files: FileList | File[]) => {
			if (activeUploadsRef.current > 0) return;
			const fileArray = Array.from(files);

			// For single file mode, only take the first file
			const filesToUpload = multiple ? fileArray : fileArray.slice(0, 1);

			if (!filesToUpload.length) return;
			let currentPaths = Array.isArray(value)
				? [...value]
				: value
					? [value]
					: [];
			handleUploadStart();
			try {
				for (const file of filesToUpload) {
					const uploadId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;

					// Add to uploading list
					setUploadingFiles((prev) => [
						...prev,
						{
							id: uploadId,
							name: file.name,
							progress: null,
							status: "uploading",
							file,
						},
					]);

					try {
						const path = await uploadFile(file, (progress) => {
							setUploadingFiles((prev) =>
								prev.map((f) =>
									f.id === uploadId ? { ...f, progress } : f,
								),
							);
						});

						// Remove from uploading, add to completed
						setUploadingFiles((prev) =>
							prev.filter((f) => f.id !== uploadId),
						);

						// Update form value
						if (multiple) {
							currentPaths = [...currentPaths, path];
							onChange(currentPaths);
						} else {
							onChange(path);
						}
					} catch (err) {
						const errorMsg =
							err instanceof Error
								? err.message
								: "Upload failed";

						// Mark as error in uploading list
						setUploadingFiles((prev) =>
							prev.map((f) =>
								f.id === uploadId
									? { ...f, status: "error", error: errorMsg }
									: f,
							),
						);

						toast.error(`Failed to upload ${file.name}`, {
							description: errorMsg,
						});
					}
				}
			} finally {
				handleUploadEnd();
			}
		},
		[
			multiple,
			value,
			onChange,
			uploadFile,
			handleUploadStart,
			handleUploadEnd,
		],
	);

	const handleFileChange = useCallback(
		(e: React.ChangeEvent<HTMLInputElement>) => {
			const files = e.target.files;
			if (files && files.length > 0) {
				processFiles(files);
			}
			// Reset input so same file can be selected again
			if (inputRef.current) {
				inputRef.current.value = "";
			}
		},
		[processFiles],
	);

	const handleDrop = useCallback(
		(e: React.DragEvent) => {
			e.preventDefault();
			setIsDragOver(false);

			const files = e.dataTransfer.files;
			if (files && files.length > 0) {
				processFiles(files);
			}
		},
		[processFiles],
	);

	const handleDragOver = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		setIsDragOver(true);
	}, []);

	const handleDragLeave = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		setIsDragOver(false);
	}, []);

	const removeCompletedFile = useCallback(
		(pathToRemove: string) => {
			if (multiple && Array.isArray(value)) {
				const newPaths = value.filter((p) => p !== pathToRemove);
				onChange(newPaths.length > 0 ? newPaths : null);
			} else {
				onChange(null);
			}
		},
		[multiple, value, onChange],
	);

	const removeUploadingFile = useCallback((uploadId: string) => {
		setUploadingFiles((prev) => prev.filter((f) => f.id !== uploadId));
	}, []);

	const retryUpload = useCallback(
		(uploadingFile: UploadingFile) => {
			// Remove from uploading list and reprocess
			setUploadingFiles((prev) =>
				prev.filter((f) => f.id !== uploadingFile.id),
			);
			processFiles([uploadingFile.file]);
		},
		[processFiles],
	);

	const hasFiles = completedFiles.length > 0 || uploadingFiles.length > 0;
	const isUploading = uploadingFiles.some((f) => f.status === "uploading");

	return (
		<div
			role="group"
			aria-labelledby={`${id}-label`}
			className="min-w-0 space-y-3"
		>
			<Label id={`${id}-label`}>
				{label}
				{required && <span className="text-destructive ml-1">*</span>}
			</Label>

			{/* Drop zone - show when no files or in multiple mode */}
			{(!hasFiles || multiple) && (
				<div
					className={`min-w-0 border border-dashed rounded-[var(--bf-radius-surface)] p-4 motion-safe:transition-colors ${
						isDragOver
							? "border-primary bg-primary/5"
							: "hover:border-primary/50"
					} ${isUploading ? "opacity-50 pointer-events-none" : ""}`}
					onDrop={handleDrop}
					onDragOver={handleDragOver}
					onDragLeave={handleDragLeave}
				>
					<div className="flex flex-col items-center gap-2">
						<Upload className="h-8 w-8 text-muted-foreground" />
						<div className="text-center">
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={isUploading}
								onClick={() => inputRef.current?.click()}
							>
								Choose file{multiple ? "s" : ""}
							</Button>
							<span className="text-sm text-muted-foreground">
								{" "}
								or drag and drop
							</span>
							<Input
								ref={inputRef}
								id={`${id}-input`}
								aria-label={label || fieldName}
								aria-required={required}
								aria-invalid={!!error}
								aria-describedby={
									[
										helpText ? `${id}-help` : "",
										error ? `${id}-error` : "",
									]
										.filter(Boolean)
										.join(" ") || undefined
								}
								disabled={isUploading}
								type="file"
								className="hidden"
								onChange={handleFileChange}
								accept={allowedTypes?.join(",") ?? undefined}
								multiple={multiple ?? undefined}
							/>
							<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground mt-1">
								{allowedTypes && allowedTypes.length > 0
									? `Allowed: ${allowedTypes.join(", ")}`
									: "All file types allowed"}
								{maxSizeMb && ` • Max ${maxSizeMb}MB`}
							</p>
						</div>
					</div>
				</div>
			)}

			{/* Uploading files */}
			{uploadingFiles.length > 0 && (
				<div className="space-y-2">
					{uploadingFiles.map((uploadingFile) => (
						<div
							key={uploadingFile.id}
							className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-3 rounded-[var(--bf-radius-surface)] border bg-muted/30 p-3"
						>
							{uploadingFile.status === "uploading" ? (
								<Loader2 className="h-4 w-4 motion-safe:animate-spin text-primary shrink-0" />
							) : (
								<AlertCircle className="h-4 w-4 text-destructive shrink-0" />
							)}
							<div className="flex-1 min-w-0">
								<p className="text-sm font-medium [overflow-wrap:anywhere]">
									{uploadingFile.name}
								</p>
								{uploadingFile.status === "uploading" &&
								uploadingFile.progress ? (
									<div className="mt-1">
										<Progress
											aria-label={`Uploading ${uploadingFile.name}`}
											value={
												uploadingFile.progress
													.percentage
											}
											className="h-1"
										/>
										<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground mt-1">
											{uploadingFile.progress.percentage}%
										</p>
									</div>
								) : uploadingFile.status === "error" ? (
									<p
										role="alert"
										className="text-sm leading-6 [overflow-wrap:anywhere] text-destructive mt-1"
									>
										{uploadingFile.error}
									</p>
								) : null}
							</div>
							{uploadingFile.status === "error" && (
								<div className="col-start-2 flex flex-wrap gap-1">
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="h-11 w-11"
										disabled={isUploading}
										aria-label={`Retry ${uploadingFile.name}`}
										onClick={() =>
											retryUpload(uploadingFile)
										}
									>
										<RotateCcw className="h-3 w-3" />
									</Button>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="h-11 w-11"
										disabled={isUploading}
										aria-label={`Dismiss failed upload ${uploadingFile.name}`}
										onClick={() =>
											removeUploadingFile(
												uploadingFile.id,
											)
										}
									>
										<X className="h-3 w-3" />
									</Button>
								</div>
							)}
						</div>
					))}
				</div>
			)}

			{/* Completed files */}
			{completedFiles.length > 0 && (
				<div className="space-y-2">
					{completedFiles.map((completedFile) => (
						<CompletedFileRow
							key={completedFile.path}
							completedFile={completedFile}
							disabled={isUploading}
							onRemove={() =>
								removeCompletedFile(completedFile.path)
							}
						/>
					))}
				</div>
			)}

			{helpText && (
				<p
					id={`${id}-help`}
					className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
				>
					{helpText}
				</p>
			)}
			{error && (
				<p
					id={`${id}-error`}
					role="alert"
					className="text-sm text-destructive [overflow-wrap:anywhere]"
				>
					{error.message}
				</p>
			)}
		</div>
	);
}

function CompletedFileRow({
	completedFile,
	disabled,
	onRemove,
}: {
	completedFile: CompletedFile;
	disabled: boolean;
	onRemove: () => void;
}) {
	return (
		<div
			key={completedFile.path}
			className="min-w-0 flex flex-wrap items-start gap-3 rounded-[var(--bf-radius-surface)] border bg-muted/30 p-3"
		>
			<File className="h-4 w-4 text-muted-foreground shrink-0" />
			<div className="flex-1 min-w-0">
				<p className="text-sm font-medium [overflow-wrap:anywhere]">
					{completedFile.name}
				</p>
				{completedFile.size > 0 && (
					<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
						{formatFileSize(completedFile.size)}
					</p>
				)}
			</div>
			<Button
				type="button"
				variant="ghost"
				size="icon"
				className="h-11 w-11 shrink-0"
				disabled={disabled}
				aria-label={`Remove ${completedFile.name}`}
				onClick={() => onRemove()}
			>
				<X className="h-3 w-3" />
			</Button>
		</div>
	);
}
