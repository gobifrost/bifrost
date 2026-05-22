import { useCallback, useRef, useState, type ReactNode } from "react";
import { Camera, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";

export type LogoDropZoneProps = {
	/** Endpoint to POST a multipart upload to. */
	uploadUrl: string;
	/** Endpoint to DELETE when the user removes the image. */
	deleteUrl: string;
	/** GET URL used to render the preview <img>. */
	previewUrl: string;
	/** Rendered inside the preview when no image is set (or it 404s). */
	fallback: ReactNode;
	/** Square vs circle preview. Defaults to square. */
	shape?: "circle" | "square";
	/** Pixel size of the preview. Defaults to 96. */
	size?: number;
	/** Accepted MIME types. Defaults to PNG/JPEG/SVG. */
	accept?: string;
	/** Max size in bytes. Defaults to 5MB. */
	maxBytes?: number;
	/** ARIA label for the drop target. */
	ariaLabel?: string;
	/** Called after a successful upload or delete so the caller can invalidate caches. */
	onChange?: () => void;
};

const DEFAULT_ACCEPT = "image/png,image/jpeg,image/svg+xml";
const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;

export function LogoDropZone({
	uploadUrl,
	deleteUrl,
	previewUrl,
	fallback,
	shape = "square",
	size = 96,
	accept = DEFAULT_ACCEPT,
	maxBytes = DEFAULT_MAX_BYTES,
	ariaLabel = "Upload image (click or drag)",
	onChange,
}: LogoDropZoneProps) {
	const fileInputRef = useRef<HTMLInputElement>(null);
	const [cacheKey, setCacheKey] = useState(() => String(Date.now()));
	const [isDragging, setIsDragging] = useState(false);
	const [imageLoaded, setImageLoaded] = useState(false);
	const [isErrored, setIsErrored] = useState(false);
	const [uploading, setUploading] = useState(false);
	const [removing, setRemoving] = useState(false);

	async function handleUpload(file: File) {
		const acceptedTypes = accept.split(",").map((t) => t.trim());
		if (!acceptedTypes.includes(file.type)) {
			toast.error(
				`Please upload a ${acceptedTypes
					.map((t) => t.split("/")[1].toUpperCase())
					.join("/")} image`,
			);
			return;
		}
		if (file.size > maxBytes) {
			toast.error(
				`Image must be less than ${Math.round(maxBytes / 1024 / 1024)}MB`,
			);
			return;
		}

		setUploading(true);
		try {
			const fd = new FormData();
			fd.append("file", file);
			const resp = await authFetch(uploadUrl, { method: "POST", body: fd });
			if (!resp.ok) {
				const err = await resp.json().catch(() => ({}));
				throw new Error(err.detail || `Upload failed (${resp.status})`);
			}
			setCacheKey(String(Date.now()));
			setIsErrored(false);
			setImageLoaded(false);
			onChange?.();
			toast.success("Image updated");
		} catch (err) {
			toast.error((err as Error).message);
		} finally {
			setUploading(false);
		}
	}

	async function handleDelete() {
		setRemoving(true);
		try {
			const resp = await authFetch(deleteUrl, { method: "DELETE" });
			if (!resp.ok && resp.status !== 204) {
				throw new Error(`Remove failed (${resp.status})`);
			}
			setCacheKey(String(Date.now()));
			setIsErrored(true);
			setImageLoaded(false);
			onChange?.();
			toast.success("Image removed");
		} catch (err) {
			toast.error((err as Error).message);
		} finally {
			setRemoving(false);
		}
	}

	const handleDragOver = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		setIsDragging(true);
	}, []);
	const handleDragLeave = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		setIsDragging(false);
	}, []);
	const handleDrop = useCallback(
		(e: React.DragEvent) => {
			e.preventDefault();
			setIsDragging(false);
			const file = e.dataTransfer.files[0];
			if (file) void handleUpload(file);
		},
		// eslint-disable-next-line react-hooks/exhaustive-deps
		[uploadUrl, accept, maxBytes],
	);

	const rounded = shape === "circle" ? "rounded-full" : "rounded-md";
	const src = `${previewUrl}?v=${encodeURIComponent(cacheKey)}`;

	return (
		<div
			data-testid="logo-drop-zone"
			role="button"
			tabIndex={0}
			aria-label={ariaLabel}
			title={ariaLabel}
			onClick={() => fileInputRef.current?.click()}
			onKeyDown={(e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					fileInputRef.current?.click();
				}
			}}
			onDragOver={handleDragOver}
			onDragLeave={handleDragLeave}
			onDrop={handleDrop}
			style={{ width: size, height: size }}
			className={`relative group cursor-pointer overflow-hidden ${rounded} border bg-muted/40 flex items-center justify-center shrink-0 ${
				isDragging ? "ring-2 ring-primary ring-offset-2" : ""
			}`}
		>
			{!isErrored && (
				<img
					data-testid="logo-drop-zone-img"
					src={src}
					alt=""
					width={size}
					height={size}
					className="h-full w-full object-cover"
					onLoad={() => setImageLoaded(true)}
					onError={() => {
						setIsErrored(true);
						setImageLoaded(false);
					}}
				/>
			)}
			{(isErrored || !imageLoaded) && (
				<div className="absolute inset-0 flex items-center justify-center text-muted-foreground">
					{fallback}
				</div>
			)}
			<div
				className={`absolute inset-0 flex items-center justify-center bg-black/50 ${rounded} transition-opacity ${
					uploading
						? "opacity-100"
						: "opacity-0 group-hover:opacity-100"
				}`}
			>
				{uploading ? (
					<Loader2 className="h-6 w-6 text-white animate-spin" />
				) : (
					<Camera className="h-6 w-6 text-white" />
				)}
			</div>
			{imageLoaded && !isErrored && (
				<button
					type="button"
					aria-label="Remove image"
					title="Remove image"
					disabled={removing}
					onClick={(e) => {
						e.stopPropagation();
						void handleDelete();
					}}
					className="absolute top-1 right-1 rounded-full bg-black/70 p-1 text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/90 disabled:opacity-50"
				>
					{removing ? (
						<Loader2 className="h-3 w-3 animate-spin" />
					) : (
						<Trash2 className="h-3 w-3" />
					)}
				</button>
			)}
			<input
				ref={fileInputRef}
				type="file"
				accept={accept}
				className="hidden"
				onChange={(e) => {
					const f = e.target.files?.[0];
					if (f) void handleUpload(f);
					e.target.value = "";
				}}
			/>
		</div>
	);
}
