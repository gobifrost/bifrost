import { Loader2 } from "lucide-react";

interface PageLoaderProps {
	message?: string;
	size?: "sm" | "md" | "lg";
	fullScreen?: boolean;
}

export function PageLoader({
	message = "Loading...",
	size = "md",
	fullScreen = false,
}: PageLoaderProps) {
	const sizeClasses = {
		sm: "h-8 w-8",
		md: "h-12 w-12",
		lg: "h-16 w-16",
	};

	const containerClasses = fullScreen
		? "flex h-screen w-screen items-center justify-center bg-background"
		: "flex min-h-[400px] h-full w-full items-center justify-center";

	return (
		<div
			className={containerClasses}
			role="status"
			aria-live="polite"
			aria-label={message}
		>
			<div className="flex flex-col items-center gap-4">
				<Loader2
					aria-hidden="true"
					className={`${sizeClasses[size]} animate-spin text-primary motion-reduce:animate-none`}
				/>
				<p className="text-sm text-muted-foreground">{message}</p>
			</div>
		</div>
	);
}
