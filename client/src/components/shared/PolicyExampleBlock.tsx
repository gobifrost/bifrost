/**
 * A single worked-example policy in a reference panel: heading + description +
 * a read-only code view that defaults to YAML with a JSON/YAML toggle, plus a
 * Copy button that copies whatever format is currently shown. Shared by the
 * Tables and Files policy reference panels so examples match the editors
 * (which default to YAML).
 */

import { useState } from "react";
import * as yaml from "js-yaml";
import { Button } from "@/components/ui/button";
import { CodeEditor } from "@/components/tables/CodeEditor";

type Format = "yaml" | "json";

export interface PolicyExampleBlockProps {
	heading: string;
	description: string;
	/** The policy document to render (any JSON-serializable shape). */
	policy: unknown;
	/** Stable index for the Monaco model path (must be unique on the page). */
	index: number;
}

function serialize(policy: unknown, format: Format): string {
	return format === "yaml"
		? yaml.dump(policy)
		: JSON.stringify(policy, null, 2);
}

export function PolicyExampleBlock({
	heading,
	description,
	policy,
	index,
}: PolicyExampleBlockProps) {
	const [format, setFormat] = useState<Format>("yaml");
	const [copied, setCopied] = useState(false);
	const text = serialize(policy, format);

	function handleCopy() {
		// Guard the clipboard call so jsdom (no navigator.clipboard) doesn't
		// throw; the button still flips to "Copied!" for user feedback.
		try {
			void navigator.clipboard?.writeText(text);
		} catch {
			// no-op; visual state still updates
		}
		setCopied(true);
		setTimeout(() => setCopied(false), 1500);
	}

	return (
		<div className="space-y-1">
			<div className="flex items-center justify-between gap-2">
				<h5 className="font-mono text-sm font-semibold">{heading}</h5>
				<div className="flex items-center gap-1">
					<div className="flex overflow-hidden rounded-md border text-[11px]">
						<button
							type="button"
							onClick={() => setFormat("yaml")}
							className={
								"px-2 py-0.5 " +
								(format === "yaml"
									? "bg-muted font-medium text-foreground"
									: "text-muted-foreground")
							}
						>
							YAML
						</button>
						<button
							type="button"
							onClick={() => setFormat("json")}
							className={
								"px-2 py-0.5 " +
								(format === "json"
									? "bg-muted font-medium text-foreground"
									: "text-muted-foreground")
							}
						>
							JSON
						</button>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="xs"
						onClick={handleCopy}
					>
						{copied ? "Copied!" : "Copy"}
					</Button>
				</div>
			</div>
			<p className="text-xs text-muted-foreground">{description}</p>
			<CodeEditor
				mode={format}
				text={text}
				onChange={() => {}}
				path={`example-${index}.${format}`}
				height="170px"
				readOnly
			/>
		</div>
	);
}
