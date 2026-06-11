import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Eye, Copy, Check, TreeDeciduous } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { toast } from "sonner";

interface PrettyInputDisplayProps {
	inputData: Record<string, unknown>;
	showToggle?: boolean;
	defaultView?: "pretty" | "tree";
}

/**
 * Convert snake_case to Title Case
 * Examples:
 * - user_name → User Name
 * - api_key → API Key
 * - first_name_last_name → First Name Last Name
 */
function snakeCaseToTitleCase(str: string): string {
	return str
		.split("_")
		.map((word) => {
			// Handle common acronyms
			const acronyms = [
				"api",
				"id",
				"url",
				"uri",
				"http",
				"https",
				"ip",
				"sql",
				"db",
				"ui",
				"ux",
			];
			if (acronyms.includes(word.toLowerCase())) {
				return word.toUpperCase();
			}
			// Capitalize first letter
			return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
		})
		.join(" ");
}

/**
 * Format value for display
 */
function formatValue(value: unknown): { display: string; badge?: string } {
	if (value === null) {
		return { display: "null", badge: "null" };
	}

	if (value === undefined) {
		return { display: "undefined", badge: "undefined" };
	}

	if (typeof value === "boolean") {
		return {
			display: value ? "Yes" : "No",
			badge: value ? "true" : "false",
		};
	}

	if (typeof value === "number") {
		return { display: value.toLocaleString(), badge: "number" };
	}

	if (typeof value === "string") {
		// Check if it's a URL
		try {
			new URL(value);
			return { display: value, badge: "url" };
		} catch {
			// Check if it's a date
			const dateRegex = /^\d{4}-\d{2}-\d{2}/;
			if (dateRegex.test(value)) {
				try {
					const date = new Date(value);
					if (!isNaN(date.getTime())) {
						return {
							display: date.toLocaleString(),
							badge: "date",
						};
					}
				} catch {
					// Not a valid date
				}
			}

			// Regular string
			return { display: value };
		}
	}

	if (Array.isArray(value)) {
		return { display: value.join(", "), badge: `array (${value.length})` };
	}

	if (typeof value === "object") {
		return { display: JSON.stringify(value, null, 2), badge: "object" };
	}

	return { display: String(value) };
}

export function PrettyInputDisplay({
	inputData,
	showToggle = false,
	defaultView = "pretty",
}: PrettyInputDisplayProps) {
	const [view, setView] = useState<"pretty" | "tree">(defaultView);
	const [copied, setCopied] = useState(false);

	const handleCopy = async () => {
		try {
			await navigator.clipboard.writeText(
				JSON.stringify(inputData, null, 2),
			);
			setCopied(true);
			toast.success("Copied to clipboard");
			setTimeout(() => setCopied(false), 2000);
		} catch {
			toast.error("Failed to copy to clipboard");
		}
	};

	// Tree view
	if (view === "tree") {
		return (
			<div className="space-y-4">
				<div className="flex items-center justify-between">
					{showToggle ? (
						<p className="text-sm text-muted-foreground">
							Viewing tree structure
						</p>
					) : (
						<div />
					)}
					<div className="flex gap-2">
						<Button
							variant="outline"
							size="sm"
							onClick={handleCopy}
						>
							{copied ? (
								<Check className="mr-2 h-4 w-4" />
							) : (
								<Copy className="mr-2 h-4 w-4" />
							)}
							{copied ? "Copied!" : "Copy"}
						</Button>
						{showToggle && (
							<Button
								variant="outline"
								size="sm"
								onClick={() => setView("pretty")}
							>
								<Eye className="mr-2 h-4 w-4" />
								Pretty View
							</Button>
						)}
					</div>
				</div>
				<div className="rounded-lg ring-1 ring-foreground/5 p-4 bg-muted/50">
					<VariablesTreeView data={inputData} />
				</div>
			</div>
		);
	}

	// Pretty view
	const entries = Object.entries(inputData);

	if (entries.length === 0) {
		return (
			<div className="text-center text-muted-foreground py-8">
				No input parameters
			</div>
		);
	}

	return (
		<div className="space-y-4">
			{showToggle && (
				<div className="flex items-center justify-between">
					<p className="text-sm text-muted-foreground">
						Viewing {entries.length} parameter
						{entries.length !== 1 ? "s" : ""}
					</p>
					<Button
						variant="outline"
						size="sm"
						onClick={() => setView("tree")}
					>
						<TreeDeciduous className="mr-2 h-4 w-4" />
						Tree View
					</Button>
				</div>
			)}

			<div className="divide-y divide-border/60 overflow-hidden rounded-lg ring-1 ring-foreground/5 bg-muted/50">
				{entries.map(([key, value]) => {
					const friendlyLabel = snakeCaseToTitleCase(key);
					const { display, badge } = formatValue(value);

					return (
						<div
							key={key}
							className="flex items-start gap-4 p-4 hover:bg-muted/50 transition-colors"
						>
							<div className="flex-1 min-w-0">
								<div className="flex items-center gap-2 mb-1">
									<label className="text-sm font-medium">
										{friendlyLabel}
									</label>
									{badge && (
										<Badge
											variant="secondary"
											className="text-xs"
										>
											{badge}
										</Badge>
									)}
								</div>
								<div className="text-sm text-muted-foreground break-words">
									{typeof value === "object" &&
									!Array.isArray(value) ? (
										<SyntaxHighlighter
											language="json"
											style={oneDark}
											customStyle={{
												margin: 0,
												borderRadius: "0.25rem",
												fontSize: "0.75rem",
												maxHeight: "16rem",
												maxWidth: "100%",
												overflow: "auto",
											}}
										>
											{display}
										</SyntaxHighlighter>
									) : (
										<p className="whitespace-pre-wrap break-all">
											{display}
										</p>
									)}
								</div>
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}
