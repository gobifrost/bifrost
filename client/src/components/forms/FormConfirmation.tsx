import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card, CardContent } from "@/components/ui/card";

interface FormConfirmationProps {
	formId: string;
	markdown: string;
}

function parentOrigin(): string | null {
	if (!document.referrer) return null;
	try {
		const origin = new URL(document.referrer).origin;
		return origin.startsWith("https://") || origin.startsWith("http://")
			? origin
			: null;
	} catch {
		return null;
	}
}

function safeImageSource(src: string | undefined): string | undefined {
	if (!src) return undefined;
	try {
		const url = new URL(src, window.location.origin);
		if (
			url.protocol === "https:" ||
			url.origin === window.location.origin
		) {
			return url.href;
		}
	} catch {
		return undefined;
	}
	return undefined;
}

export function FormConfirmation({ formId, markdown }: FormConfirmationProps) {
	const containerRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		window.scrollTo({ top: 0, behavior: "smooth" });
		containerRef.current?.focus();

		const targetOrigin = parentOrigin();
		if (!targetOrigin || window.parent === window) return;

		const notify = (
			type: "bifrost:form-submitted" | "bifrost:form-resize",
		) => {
			window.parent.postMessage(
				{
					type,
					formId,
					...(type === "bifrost:form-resize"
						? { height: document.documentElement.scrollHeight }
						: {}),
				},
				targetOrigin,
			);
		};

		notify("bifrost:form-submitted");
		const observer = new ResizeObserver(() =>
			notify("bifrost:form-resize"),
		);
		observer.observe(document.documentElement);
		return () => observer.disconnect();
	}, [formId]);

	return (
		<div
			ref={containerRef}
			tabIndex={-1}
			role="status"
			aria-live="polite"
			className="flex scroll-mt-4 justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
		>
			<Card className="w-full max-w-2xl">
				<CardContent className="prose prose-sm max-w-none overflow-hidden pt-6 dark:prose-invert">
					<FormConfirmationMarkdown markdown={markdown} />
				</CardContent>
			</Card>
		</div>
	);
}

export function FormConfirmationMarkdown({ markdown }: { markdown: string }) {
	return (
		<div className="markdown-content">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				components={{
					img: ({ src, alt }) => {
						const safeSrc = safeImageSource(src);
						return safeSrc ? (
							<img
								src={safeSrc}
								alt={alt || ""}
								loading="lazy"
								referrerPolicy="no-referrer"
								className="h-auto max-w-full"
							/>
						) : null;
					},
					a: ({ href, children }) => {
						const external = href?.startsWith("http");
						return (
							<a
								href={href}
								{...(external
									? {
											target: "_blank",
											rel: "noopener noreferrer",
										}
									: {})}
							>
								{children}
							</a>
						);
					},
				}}
			>
				{markdown}
			</ReactMarkdown>
		</div>
	);
}
