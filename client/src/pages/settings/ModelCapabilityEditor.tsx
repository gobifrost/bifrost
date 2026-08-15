import { useEffect, useRef, useState } from "react";
import {
	FileText,
	Image,
	Loader2,
	RefreshCw,
	Sparkles,
	Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { cn } from "@/lib/utils";

export type ModelCapabilities = components["schemas"]["ModelCapabilities"];

const UNKNOWN: ModelCapabilities = {
	image_input: false,
	pdf_input: false,
	tool_calling: false,
	native_image_output: false,
	source: "unknown",
	fingerprint: "",
};

const CAPABILITY_ROWS = [
	{
		key: "image_input" as const,
		label: "Image input",
		description: "The model can inspect attached images.",
		icon: Image,
	},
	{
		key: "pdf_input" as const,
		label: "PDF input",
		description: "The model can inspect attached PDF files.",
		icon: FileText,
	},
	{
		key: "tool_calling" as const,
		label: "Tool calling",
		description: "Required for workflows and Bifrost-generated files.",
		icon: Wrench,
	},
	{
		key: "native_image_output" as const,
		label: "Native image generation",
		description: "The provider can return a newly generated image.",
		icon: Sparkles,
	},
];

function sourceLabel(source: ModelCapabilities["source"]): string {
	return {
		openrouter: "OpenRouter catalog",
		verified: "Verified",
		manual: "Manual",
		unknown: "Not verified",
	}[source];
}

export function ModelCapabilityEditor({
	provider,
	model,
	endpoint,
	apiKey,
	value,
	onChange,
}: {
	provider: "openai" | "anthropic" | "google";
	model: string;
	endpoint: string;
	apiKey?: string;
	value: ModelCapabilities | null;
	onChange: (value: ModelCapabilities) => void;
}) {
	const [detecting, setDetecting] = useState(false);
	const [verifying, setVerifying] = useState(false);
	const [message, setMessage] = useState<string | null>(null);
	const previousModel = useRef(model);
	const capabilities = value ?? UNKNOWN;

	const detect = async () => {
		if (!model.trim()) return;
		setDetecting(true);
		try {
			const response = await authFetch(
				"/api/admin/llm/model-capabilities",
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						provider,
						model,
						endpoint: endpoint || null,
					}),
				},
			);
			if (!response.ok) throw new Error("Capability lookup failed");
			const result = (await response.json()) as {
				capabilities: ModelCapabilities;
				message: string;
			};
			onChange(result.capabilities);
			setMessage(result.message);
		} catch {
			setMessage(
				"Capability lookup failed. Set the flags manually before saving.",
			);
		} finally {
			setDetecting(false);
		}
	};

	const verify = async () => {
		if (!model.trim()) return;
		setVerifying(true);
		try {
			const response = await authFetch(
				"/api/admin/llm/model-capabilities/verify",
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						provider,
						model,
						endpoint: endpoint || null,
						api_key: apiKey || null,
					}),
				},
			);
			const body = (await response.json().catch(() => ({}))) as {
				capabilities?: ModelCapabilities;
				message?: string;
				detail?: string;
			};
			if (!response.ok || !body.capabilities) {
				throw new Error(
					body.detail || "Capability verification failed",
				);
			}
			onChange(body.capabilities);
			setMessage(body.message || "Provider verification completed.");
		} catch (error) {
			setMessage(
				error instanceof Error
					? error.message
					: "Capability verification failed.",
			);
		} finally {
			setVerifying(false);
		}
	};

	useEffect(() => {
		if (previousModel.current === model) return;
		previousModel.current = model;
		if (!model.trim()) return;
		const timer = window.setTimeout(() => void detect(), 450);
		return () => window.clearTimeout(timer);
		// Detection deliberately follows model selection; callback identity is not an input.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [model]);

	const toggle = (
		key:
			| "image_input"
			| "pdf_input"
			| "tool_calling"
			| "native_image_output",
		checked: boolean,
	) => {
		onChange({ ...capabilities, [key]: checked, source: "manual" });
		setMessage(
			"Manually configured. Save the AI configuration to apply these flags.",
		);
	};

	return (
		<div className="rounded-lg bg-muted/35 p-3">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<span className="text-xs font-medium">Capabilities</span>
					<Badge
						variant="secondary"
						className={cn(
							"font-normal",
							capabilities.source === "unknown" &&
								"text-muted-foreground",
						)}
					>
						{sourceLabel(capabilities.source)}
					</Badge>
				</div>
				<div className="flex items-center gap-1">
					{capabilities.source === "unknown" && (
						<Button
							type="button"
							variant="secondary"
							size="sm"
							onClick={() => void verify()}
							disabled={!model.trim() || verifying}
						>
							{verifying && (
								<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
							)}
							Verify with provider
						</Button>
					)}
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={() => void detect()}
						disabled={!model.trim() || detecting}
					>
						{detecting ? (
							<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
						) : (
							<RefreshCw className="mr-1.5 h-3.5 w-3.5" />
						)}
						Catalog
					</Button>
				</div>
			</div>
			<div className="mt-3 grid gap-3 lg:grid-cols-2">
				{CAPABILITY_ROWS.map((item) => {
					const Icon = item.icon;
					return (
						<label
							key={item.key}
							className="flex cursor-pointer items-start gap-2.5"
						>
							<Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
							<span className="min-w-0 flex-1">
								<span className="block text-xs font-medium">
									{item.label}
								</span>
								<span className="block text-[11px] leading-4 text-muted-foreground">
									{item.description}
								</span>
							</span>
							<Switch
								size="sm"
								checked={capabilities[item.key]}
								onCheckedChange={(checked) =>
									toggle(item.key, checked)
								}
								aria-label={item.label}
							/>
						</label>
					);
				})}
			</div>
			{message && (
				<p className="mt-3 text-[11px] leading-4 text-muted-foreground">
					{message}
				</p>
			)}
		</div>
	);
}
