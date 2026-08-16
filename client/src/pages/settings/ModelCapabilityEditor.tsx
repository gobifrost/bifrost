import { useEffect, useRef, useState } from "react";
import {
	CircleHelp,
	CheckCircle2,
	FileText,
	Image,
	Loader2,
	ShieldCheck,
	Wrench,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { cn } from "@/lib/utils";

export type ModelCapabilities = components["schemas"]["ModelCapabilities"];

type CapabilityKey = "image_input" | "pdf_input" | "tool_calling";

const UNKNOWN: ModelCapabilities = {
	image_input: false,
	pdf_input: false,
	tool_calling: false,
	source: "unknown",
	fingerprint: "",
};

const CAPABILITIES = [
	{
		key: "image_input" as const,
		label: "Image Input",
		icon: Image,
	},
	{
		key: "pdf_input" as const,
		label: "PDF Input",
		icon: FileText,
	},
	{
		key: "tool_calling" as const,
		label: "Tool Calling",
		icon: Wrench,
	},
];

function sourceLabel(source: ModelCapabilities["source"]): string {
	return {
		openrouter: "OpenRouter",
		verified: "Verified",
		manual: "Manual",
		unknown: "Not Verified",
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
	const previousModel = useRef(model);
	const capabilities = value ?? UNKNOWN;
	const verified = capabilities.source !== "unknown";
	const SourceIcon = verified ? CheckCircle2 : CircleHelp;

	const detect = async (announce = false) => {
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
			if (announce) {
				toast.success("Capabilities Updated", {
					description: result.message,
				});
			}
		} catch {
			toast.error("Capability Lookup Failed", {
				description:
					"Verify with the provider or set each capability manually.",
			});
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
			toast.success("Capabilities Verified", {
				description: body.message || "Provider verification completed.",
			});
		} catch (error) {
			toast.error("Capability Verification Failed", {
				description:
					error instanceof Error
						? error.message
						: "Confirm the endpoint, key, and model, then retry.",
			});
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

	const toggle = (key: CapabilityKey) => {
		onChange({
			...capabilities,
			[key]: !capabilities[key],
			source: "manual",
		});
	};

	return (
		<TooltipProvider delayDuration={150}>
			<div className="flex min-h-5 flex-wrap items-center gap-0.5 text-xs">
				{CAPABILITIES.map((item) => {
					const Icon = item.icon;
					const unknown = capabilities.source === "unknown";
					const supported = capabilities[item.key];
					const state = unknown
						? "Not Verified"
						: supported
							? "Supported"
							: "Not Supported";
					return (
						<Tooltip key={item.key}>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => toggle(item.key)}
									aria-label={`${item.label}: ${state}`}
									className={cn(
										"grid h-6 w-6 place-items-center rounded-md outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
										unknown &&
											"text-amber-600 dark:text-amber-400",
										supported &&
											!unknown &&
											"text-green-600 dark:text-green-400",
										!supported &&
											!unknown &&
											"text-red-600 dark:text-red-400",
									)}
								>
									<Icon className="h-3.5 w-3.5" />
								</button>
							</TooltipTrigger>
							<TooltipContent>
								<p className="font-medium">{item.label}</p>
								<p>
									{state} · {sourceLabel(capabilities.source)}
								</p>
								<p>Click to change manually.</p>
							</TooltipContent>
						</Tooltip>
					);
				})}

				<div className="ml-auto flex items-center gap-0.5">
					{capabilities.source === "unknown" && (
						<Tooltip>
							<TooltipTrigger asChild>
								<Button
									type="button"
									variant="ghost"
									size="icon"
									className="h-6 w-6 text-blue-600 dark:text-blue-400"
									onClick={() => void verify()}
									disabled={!model.trim() || verifying}
									aria-label="Verify With Provider"
								>
									{verifying ? (
										<Loader2 className="h-3 w-3 animate-spin" />
									) : (
										<ShieldCheck className="h-3 w-3" />
									)}
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								Verify With Provider
							</TooltipContent>
						</Tooltip>
					)}
					<Tooltip>
						<TooltipTrigger asChild>
							<button
								type="button"
								className="flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-normal text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
								onClick={() => void detect(true)}
								disabled={!model.trim() || detecting}
								aria-label="Refresh Model Capabilities"
							>
								<span>{sourceLabel(capabilities.source)}</span>
								{detecting ? (
									<Loader2 className="h-3 w-3 animate-spin" />
								) : (
									<SourceIcon
										className={cn(
											"h-3 w-3",
											verified
												? "text-green-600 dark:text-green-400"
												: "text-amber-600 dark:text-amber-400",
										)}
										aria-hidden="true"
									/>
								)}
							</button>
						</TooltipTrigger>
						<TooltipContent>
							Refresh Model Capabilities
						</TooltipContent>
					</Tooltip>
				</div>
			</div>
		</TooltipProvider>
	);
}
