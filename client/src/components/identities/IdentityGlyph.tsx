import { Workflow } from "lucide-react";

import { cn } from "@/lib/utils";

/** An identity's mark where a person has an initials avatar: a hexagon. */
export function IdentityGlyph({ className }: { className?: string }) {
	return (
		<span
			role="img"
			aria-label="Identity"
			className={cn(
				"relative inline-flex h-12 w-12 shrink-0 items-center justify-center text-foreground sm:h-14 sm:w-14",
				className,
			)}
		>
			<svg
				viewBox="0 0 48 48"
				aria-hidden="true"
				className="absolute inset-0 size-full"
			>
				<path
					d="M24 2 43.05 13v22L24 46 4.95 35V13Z"
					fill="var(--muted)"
					stroke="var(--border)"
					strokeWidth="1.5"
					strokeLinejoin="round"
				/>
			</svg>
			<Workflow
				aria-hidden="true"
				className="relative size-5 sm:size-6"
			/>
		</span>
	);
}
