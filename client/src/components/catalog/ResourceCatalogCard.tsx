import { useId, type ReactNode } from "react";

export function ResourceCatalogCard({
	icon,
	title,
	subtitle,
	description,
	action,
	footer,
	children,
	onOpen,
	href,
	disabled = false,
	compact = false,
	titleClassName = "",
	titleInteractive = true,
}: {
	icon: ReactNode;
	title: ReactNode;
	subtitle?: ReactNode;
	description?: ReactNode;
	action?: ReactNode;
	footer?: ReactNode;
	children?: ReactNode;
	onOpen: () => void;
	href?: string;
	disabled?: boolean;
	compact?: boolean;
	titleClassName?: string;
	titleInteractive?: boolean;
}) {
	const titleId = useId();

	return (
		<article
			data-slot="card"
			className="relative flex h-full min-w-0 flex-col rounded-[var(--bf-radius-surface)] border bg-card transition-colors hover:border-primary/40 focus-within:border-primary"
		>
			<div className="flex flex-wrap items-start gap-3 p-4 sm:p-5 sm:pb-3">
				{icon}
				<div
					className={
						compact ? "min-w-0 flex-1" : "min-w-0 w-full order-2"
					}
				>
					{titleInteractive && href && !disabled ? (
						<>
							<a
								href={href}
								aria-labelledby={titleId}
								className="absolute inset-0 z-[1] rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
								onClick={(event) => {
									if (
										event.button !== 0 ||
										event.metaKey ||
										event.ctrlKey ||
										event.shiftKey ||
										event.altKey
									)
										return;
									event.preventDefault();
									onOpen();
								}}
							/>
							<span
								id={titleId}
								className={`block text-left text-base leading-snug font-semibold [overflow-wrap:anywhere] ${titleClassName}`}
							>
								{title}
							</span>
						</>
					) : titleInteractive ? (
						<button
							type="button"
							className={`text-left text-base leading-snug font-semibold after:absolute after:inset-0 after:rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring disabled:cursor-not-allowed [overflow-wrap:anywhere] ${titleClassName}`}
							onClick={onOpen}
							disabled={disabled}
						>
							{title}
						</button>
					) : (
						<span
							className={`block text-left text-base leading-snug font-semibold [overflow-wrap:anywhere] ${titleClassName}`}
						>
							{title}
						</span>
					)}
					{subtitle ? (
						<p className="mt-1 text-xs text-muted-foreground">
							{subtitle}
						</p>
					) : null}
				</div>
				{action ? (
					<div className="relative z-10 ml-auto -mr-2 -mt-2 shrink-0">
						{action}
					</div>
				) : null}
			</div>
			{!compact && (
				<>
					{description ? (
						<div className="line-clamp-2 px-4 text-sm leading-relaxed text-muted-foreground [overflow-wrap:anywhere] sm:px-5">
							{description}
						</div>
					) : null}
					{children ? (
						<div className="px-4 pt-3 sm:px-5">{children}</div>
					) : null}
					{footer ? (
						<div className="mt-auto px-4 py-4 text-xs text-muted-foreground sm:px-5">
							{footer}
						</div>
					) : null}
				</>
			)}
		</article>
	);
}
