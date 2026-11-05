import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";

import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { TONE_MUTED, TYPE_MUTED } from "./design-tokens";

export interface PromptDiffViewerProps {
	before: string;
	after: string;
	className?: string;
}

/**
 * Side-by-side diff of a current prompt vs a proposed prompt.
 *
 * Thin wrapper around react-diff-viewer-continued that applies our dark-theme
 * surface tokens and renders a friendly empty state when the two sides match.
 */
export function PromptDiffViewer({
	before,
	after,
	className,
}: PromptDiffViewerProps) {
	const split = useMediaQuery("(min-width: 768px)");
	if (before === after) {
		return (
			<div
				data-testid="prompt-diff-empty"
				className={cn(
					"rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-4 text-center",
					TYPE_MUTED,
					TONE_MUTED,
					className,
				)}
			>
				No changes — the proposed prompt matches the current one.
			</div>
		);
	}

	return (
		<div
			data-testid="prompt-diff-viewer"
			className={cn(
				"min-w-0 overflow-hidden rounded-[var(--bf-radius-surface)] border [&_pre]:whitespace-pre-wrap [&_pre]:[overflow-wrap:anywhere]",
				className,
			)}
		>
			<ReactDiffViewer
				oldValue={before}
				newValue={after}
				splitView={split}
				compareMethod={DiffMethod.WORDS}
				useDarkTheme={false}
				styles={{
					variables: {
						light: {
							addedBackground: "var(--bf-success-soft)",
							addedColor: "var(--foreground)",
							removedBackground: "var(--bf-danger-soft)",
							removedColor: "var(--foreground)",
							wordAddedBackground: "var(--bf-success-soft)",
							wordRemovedBackground: "var(--bf-danger-soft)",
							addedGutterBackground: "var(--bf-success-soft)",
							removedGutterBackground: "var(--bf-danger-soft)",
							addedGutterColor: "var(--bf-success)",
							removedGutterColor: "var(--bf-danger)",
							codeFoldBackground: "var(--muted)",
							codeFoldGutterBackground: "var(--muted)",
							codeFoldContentColor: "var(--muted-foreground)",
							emptyLineBackground: "var(--card)",
							diffViewerBackground: "var(--card)",
							diffViewerColor: "var(--foreground)",
							gutterBackground: "var(--muted)",
							gutterBackgroundDark: "var(--muted)",
							diffViewerTitleBackground: "var(--muted)",
							diffViewerTitleColor: "var(--foreground)",
							diffViewerTitleBorderColor: "var(--border)",
							changedBackground: "var(--bf-warning-soft)",
							highlightBackground: "var(--accent)",
							highlightGutterBackground: "var(--accent)",
							gutterColor: "var(--muted-foreground)",
						},
					},
					contentText: {
						fontFamily: "var(--font-mono)",
						fontSize: "14px",
						lineHeight: "1.5",
					},
				}}
			/>
		</div>
	);
}
