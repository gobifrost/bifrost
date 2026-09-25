import { forwardRef, type ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** Desktop pages keep their controls above a bounded content region.
 * Mobile uses normal document flow; editors can own their own pane layout.
 */
export function PageWorkspace({ className, ...props }: ComponentProps<"div">) {
	return (
		<div
			{...props}
			data-page-workspace
			className={cn(
				"flex min-w-0 flex-col gap-6 lg:h-full lg:min-h-0",
				className,
			)}
		/>
	);
}

/** Content-sized until the desktop workspace runs out of room. Tables retain
 * their own sticky header/footer inside this region. No nested mobile scroll.
 */
export const PageScrollArea = forwardRef<HTMLDivElement, ComponentProps<"div">>(
	function PageScrollArea({ className, ...props }, ref) {
		return (
			<div
				ref={ref}
				role="region"
				aria-label="Page content"
				tabIndex={0}
				{...props}
				data-page-scroll
				className={cn(
					"min-w-0 lg:min-h-0 lg:flex-1 lg:overflow-auto",
					className,
				)}
			/>
		);
	},
);
