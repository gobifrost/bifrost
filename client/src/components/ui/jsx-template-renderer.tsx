import React from "react";
import { transform } from "@babel/standalone";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

interface JsxTemplateRendererProps {
	template: string;
	context: {
		workflow: Record<string, unknown>;
		query: Record<string, string>;
		field: Record<string, unknown>;
	};
	className?: string;
}

/**
 * Renders JSX template strings with access to form context
 *
 * SECURITY ACKNOWLEDGMENT (Per User Request):
 * - This component uses dynamic code evaluation (Babel transform + Function constructor)
 * - User explicitly approved this approach for trusted platform admin use
 * - Only form builders (platform hosts/admins) write these templates
 * - Templates execute client-side only with restricted context scope
 * - No server-side execution or untrusted user input
 *
 * Example template:
 * ```jsx
 * <div>
 *   <h1>Welcome {context.workflow.user_email}</h1>
 *   {context.workflow.users && context.workflow.users.map(function(user, i) {
 *     return <div key={i}>{user.name}</div>
 *   })}
 * </div>
 * ```
 */
/**
 * Compile + evaluate the template synchronously. Returns the produced React
 * element on success, or an error string on failure. Pulled out so the JSX
 * render (`<div>{element}</div>`) never sits inside a try/catch — render
 * errors of the produced element won't be caught synchronously and need an
 * error boundary at the call site.
 */
function evaluateTemplate(
	template: string,
	context: JsxTemplateRendererProps["context"],
): { ok: true; element: React.ReactNode } | { ok: false; error: string } {
	try {
		// Wrap template in an IIFE to capture the JSX expression
		const wrappedTemplate = `(function() { return (${template}); })()`;

		// Transform JSX to JavaScript using Babel
		const result = transform(wrappedTemplate, {
			// The evaluator supplies React directly; automatic runtime imports cannot
			// execute inside its Function body.
			presets: [["react", { runtime: "classic" }]],
			filename: "template.jsx",
		});

		if (!result || !result.code) {
			return {
				ok: false,
				error: "Babel transformation produced no code",
			};
		}

		// Evaluate the transformed code with React and context in scope
		// User approved: Only admins write templates, client-side execution only
		const evaluator = Function(
			"React",
			"context",
			`"use strict"; return ${result.code};`,
		);
		return { ok: true, element: evaluator(React, context) };
	} catch (error) {
		return {
			ok: false,
			error: error instanceof Error ? error.message : "Invalid template",
		};
	}
}

export function JsxTemplateRenderer({
	template,
	context,
	className,
}: JsxTemplateRendererProps) {
	const result = evaluateTemplate(template, context);

	if (!result.ok) {
		return (
			<Alert variant="destructive" className={className}>
				<AlertTitle>Template error</AlertTitle>
				<AlertDescription>
					<pre
						tabIndex={0}
						aria-label="Template error details"
						className="max-h-60 overflow-auto whitespace-pre-wrap font-mono text-sm leading-6 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					>
						{result.error}
					</pre>
				</AlertDescription>
			</Alert>
		);
	}

	return (
		<div
			className={cn(
				"min-w-0 max-w-full overflow-x-auto [overflow-wrap:anywhere]",
				className,
			)}
		>
			{result.element}
		</div>
	);
}
