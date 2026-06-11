/**
 * LLM Configuration Settings
 *
 * Configure the AI provider (OpenAI, Anthropic, or custom) for chat functionality.
 * Flow: Provider → Endpoint (if custom) → API Key → Test → Model (loaded dynamically)
 */

import { useState, useEffect, useRef } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Combobox } from "@/components/ui/combobox";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
	Loader2,
	Bot,
	CheckCircle2,
	AlertCircle,
	Trash2,
	Zap,
	Database,
	DollarSign,
	Plus,
	Pencil,
	X,
	Check,
	ArrowRight,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { $api, authFetch } from "@/lib/api-client";
import { useNotificationStore } from "@/stores/notificationStore";
import {
	listPricing,
	createPricing,
	updatePricing,
	deletePricing,
	type AIModelPricingListItem,
	type AIModelPricingCreate,
} from "@/services/ai-pricing";

type Provider = "openai" | "anthropic";

// Model info with both ID and display name
interface ModelInfo {
	id: string;
	display_name: string;
}

// Default models for each provider (fallback if API doesn't return models)
const DEFAULT_MODELS: Record<Provider, string> = {
	openai: "gpt-4o",
	anthropic: "claude-sonnet-4-20250514",
};

// Default API endpoints for each provider
const DEFAULT_ENDPOINTS: Record<Provider, string> = {
	openai: "https://api.openai.com/v1",
	anthropic: "https://api.anthropic.com",
};

export function LLMConfig() {
	// Form state
	const [provider, setProvider] = useState<Provider>("openai");
	const [model, setModel] = useState(DEFAULT_MODELS.openai);
	const [apiKey, setApiKey] = useState("");
	const [endpoint, setEndpoint] = useState(DEFAULT_ENDPOINTS.openai);
	const [maxTokens, setMaxTokens] = useState(4096);
	const [defaultSystemPrompt, setDefaultSystemPrompt] = useState("");
	const [summarizationModel, setSummarizationModel] = useState("");
	const [tuningModel, setTuningModel] = useState("");

	// Models state (loaded dynamically after test)
	const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
	const [modelsLoaded, setModelsLoaded] = useState(false);

	// UI state
	const [saving, setSaving] = useState(false);
	const [testing, setTesting] = useState(false);
	const [testResult, setTestResult] = useState<{
		success: boolean;
		message: string;
	} | null>(null);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	const [pricingRefreshKey, setPricingRefreshKey] = useState(0);

	// Load current configuration
	const {
		data: config,
		isLoading: configLoading,
		refetch,
	} = $api.useQuery("get", "/api/admin/llm/config", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	// Mutations
	const saveMutation = $api.useMutation("post", "/api/admin/llm/config");
	const deleteMutation = $api.useMutation("delete", "/api/admin/llm/config");
	const testMutation = $api.useMutation("post", "/api/admin/llm/test");
	const testSavedMutation = $api.useMutation(
		"post",
		"/api/admin/llm/test-saved",
	);

	// Update form when config loads. Adjust during render with a previous-
	// reference sentinel to avoid setState-in-effect.
	const [prevConfigRef, setPrevConfigRef] = useState<typeof config>(undefined);
	if (config && prevConfigRef !== config) {
		setPrevConfigRef(config);
		const p = config.provider as Provider;
		setProvider(p);
		setModel(config.model);
		setMaxTokens(config.max_tokens);
		setDefaultSystemPrompt(config.default_system_prompt ?? "");
		setEndpoint(config.endpoint || DEFAULT_ENDPOINTS[p] || DEFAULT_ENDPOINTS.openai);
		setSummarizationModel(config.summarization_model ?? "");
		setTuningModel(config.tuning_model ?? "");
	}

	// Track if we've already fetched models for this config
	const modelsFetchedRef = useRef(false);

	// Fetch available models when config has an API key set
	useEffect(() => {
		if (!config?.api_key_set || modelsFetchedRef.current) {
			return;
		}

		const fetchModels = async () => {
			modelsFetchedRef.current = true;
			try {
				const result = await testSavedMutation.mutateAsync({});
				if (
					result.success &&
					result.models &&
					result.models.length > 0
				) {
					// Cast to ModelInfo[] since API now returns objects
					const models = result.models as unknown as ModelInfo[];
					setAvailableModels(models);
				}
				setModelsLoaded(true);
			} catch {
				// Silently fail - user can still manually test
				setModelsLoaded(true);
			}
		};

		fetchModels();
	}, [config?.api_key_set, testSavedMutation]);

	// Handle provider change
	const handleProviderChange = (newProvider: Provider) => {
		setProvider(newProvider);
		setModel(DEFAULT_MODELS[newProvider]);
		setEndpoint(DEFAULT_ENDPOINTS[newProvider]);
		setTestResult(null);
		setAvailableModels([]);
		setModelsLoaded(false);
	};

	// Test connection with current form values
	const handleTestConnection = async () => {
		if (!apiKey && !config?.api_key_set) {
			toast.error("Please enter an API key");
			return;
		}

		setTesting(true);
		setTestResult(null);

		try {
			let result;
			// If we have a new API key, test with that
			if (apiKey) {
				const isDefaultEndpoint = endpoint === DEFAULT_ENDPOINTS[provider];
				result = await testMutation.mutateAsync({
					body: {
						provider,
						model: model || DEFAULT_MODELS[provider],
						api_key: apiKey,
						endpoint: isDefaultEndpoint ? undefined : endpoint || undefined,
					},
				});
			} else {
				// Test saved configuration
				result = await testSavedMutation.mutateAsync({});
			}

			setTestResult({ success: result.success, message: result.message });

			if (result.success) {
				toast.success("Connection successful", {
					description: result.message,
				});
				// Load models from response
				if (result.models && result.models.length > 0) {
					// Cast to ModelInfo[] since API now returns objects
					const models = result.models as unknown as ModelInfo[];
					setAvailableModels(models);
					// If current model is not in list, select first available
					const modelIds = models.map((m) => m.id);
					if (!modelIds.includes(model)) {
						setModel(models[0].id);
					}
				}
				setModelsLoaded(true);
			} else {
				toast.error("Connection failed", {
					description: result.message,
				});
				setModelsLoaded(false);
			}
		} catch (error) {
			const message = extractErrorMessage(error);
			setTestResult({ success: false, message });
			toast.error("Connection test failed", { description: message });
			setModelsLoaded(false);
		} finally {
			setTesting(false);
		}
	};

	// Save configuration
	const handleSave = async () => {
		if (!apiKey && !config?.api_key_set) {
			toast.error("Please enter an API key");
			return;
		}

		if (!model) {
			toast.error("Please select a model");
			return;
		}

		setSaving(true);
		try {
			const isDefaultEndpoint = endpoint === DEFAULT_ENDPOINTS[provider];
			await saveMutation.mutateAsync({
				body: {
					provider,
					model,
					api_key: apiKey || undefined,
					endpoint: isDefaultEndpoint ? undefined : endpoint || undefined,
					max_tokens: maxTokens,
					default_system_prompt: defaultSystemPrompt || null,
					summarization_model: summarizationModel || null,
					tuning_model: tuningModel || null,
				},
			});

			toast.success("Configuration saved", {
				description: `Using ${provider} with model ${model}`,
			});

			// Clear API key field (it's saved now)
			setApiKey("");
			setTestResult(null);

			// Refetch to get updated config and refresh pricing
			refetch();
			setPricingRefreshKey((k) => k + 1);
		} catch (error) {
			toast.error("Failed to save configuration", {
				description: extractErrorMessage(error),
			});
		} finally {
			setSaving(false);
		}
	};

	// Delete configuration
	const handleDelete = async () => {
		setSaving(true);
		setShowDeleteConfirm(false);

		try {
			await deleteMutation.mutateAsync({});

			// Reset form
			setProvider("openai");
			setModel(DEFAULT_MODELS.openai);
			setApiKey("");
			setEndpoint(DEFAULT_ENDPOINTS.openai);
			setMaxTokens(16384);
			setDefaultSystemPrompt("");
			setSummarizationModel("");
			setTuningModel("");
			setTestResult(null);
			setAvailableModels([]);
			setModelsLoaded(false);

			toast.success("Configuration deleted", {
				description: "AI chat is now disabled",
			});

			refetch();
		} catch (error) {
			toast.error("Failed to delete configuration", {
				description: extractErrorMessage(error),
			});
		} finally {
			setSaving(false);
		}
	};

	if (configLoading) {
		return (
			<div className="flex items-center justify-center py-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	// Check if we can show model selection
	const canSelectModel = modelsLoaded || config?.api_key_set;
	const hasModels = availableModels.length > 0;

	// Determine if we can save:
	// - If entering new API key: must be tested successfully
	// - If using existing config: must have modelsLoaded (via test) or unchanged config
	const isNewApiKey = apiKey.length > 0;
	const isVerified = testResult?.success === true;
	const hasValidConfig = config?.api_key_set && !isNewApiKey;
	const canSave =
		!saving &&
		model &&
		(isVerified || hasValidConfig);

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Bot className="h-5 w-5" />
						<CardTitle>AI Provider Configuration</CardTitle>
					</div>
					<CardDescription>
						Configure the LLM provider for AI chat functionality.
						This enables the Chat feature across the platform.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Status Banner */}
					{config?.is_configured ? (
						<div className="rounded-lg border bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900 p-4">
							<div className="flex items-center justify-between">
								<div className="flex items-center gap-2">
									<CheckCircle2 className="h-4 w-4 text-green-600" />
									<span className="text-sm font-medium text-green-800 dark:text-green-200">
										AI Chat Enabled
									</span>
								</div>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => setShowDeleteConfirm(true)}
									className="text-destructive hover:text-destructive"
								>
									<Trash2 className="h-4 w-4 mr-1" />
									Remove
								</Button>
							</div>
							<p className="mt-1 text-sm text-green-700 dark:text-green-300">
								Using {config.provider} with model{" "}
								{config.model}
							</p>
						</div>
					) : (
						<div className="rounded-lg border bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900 p-4">
							<div className="flex items-center gap-2">
								<AlertCircle className="h-4 w-4 text-amber-600" />
								<span className="text-sm font-medium text-amber-800 dark:text-amber-200">
									AI Chat Not Configured
								</span>
							</div>
							<p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
								Configure a provider below to enable AI chat
								functionality.
							</p>
						</div>
					)}

					{/* Provider Selection */}
					<div className="space-y-2">
						<Label htmlFor="provider">Provider</Label>
						<Select
							value={provider}
							onValueChange={handleProviderChange}
						>
							<SelectTrigger id="provider">
								<SelectValue placeholder="Select provider" />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="openai">OpenAI</SelectItem>
								<SelectItem value="anthropic">
									Anthropic
								</SelectItem>
							</SelectContent>
						</Select>
					</div>

					{/* API Endpoint */}
					<div className="space-y-2">
						<Label htmlFor="endpoint">API Endpoint</Label>
						<Input
							id="endpoint"
							placeholder={DEFAULT_ENDPOINTS[provider]}
							value={endpoint}
							onChange={(e) => setEndpoint(e.target.value)}
						/>
						<p className="text-xs text-muted-foreground">
							Change this to use a compatible provider (Azure OpenAI, Ollama, etc.)
						</p>
					</div>

					{/* API Key */}
					<div className="space-y-2">
						<Label htmlFor="api-key">API Key</Label>
						<div className="flex flex-col gap-2 sm:flex-row">
							<Input
								id="api-key"
								type="password"
								autoComplete="off"
								placeholder={
									config?.api_key_set
										? "API key saved - enter new key to change"
										: provider === "openai"
											? "sk-..."
											: "sk-ant-..."
								}
								value={apiKey}
								onChange={(e) => {
									setApiKey(e.target.value);
									setTestResult(null);
									// Reset models when key changes
									if (e.target.value !== "") {
										setModelsLoaded(false);
										setAvailableModels([]);
									}
								}}
							/>
							<Button
								variant="secondary"
								onClick={handleTestConnection}
								disabled={
									testing || (!apiKey && !config?.api_key_set)
								}
							>
								{testing ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Testing...
									</>
								) : testResult?.success ? (
									<>
										<CheckCircle2 className="h-4 w-4 mr-2 text-green-600" />
										Verified
									</>
								) : testResult?.success === false ? (
									<>
										<AlertCircle className="h-4 w-4 mr-2 text-destructive" />
										Failed
									</>
								) : (
									<>
										<Zap className="h-4 w-4 mr-2" />
										Test
									</>
								)}
							</Button>
						</div>
						{provider === "openai" && (
							<p className="text-xs text-muted-foreground">
								Get your API key from{" "}
								<a
									href="https://platform.openai.com/api-keys"
									target="_blank"
									rel="noopener noreferrer"
									className="underline hover:text-foreground"
								>
									platform.openai.com
								</a>
							</p>
						)}
						{provider === "anthropic" && (
							<p className="text-xs text-muted-foreground">
								Get your API key from{" "}
								<a
									href="https://console.anthropic.com/settings/keys"
									target="_blank"
									rel="noopener noreferrer"
									className="underline hover:text-foreground"
								>
									console.anthropic.com
								</a>
							</p>
						)}
					</div>

					{/* Model Selection (only after successful test) */}
					<div className="space-y-2">
						<Label htmlFor="model">
							Model
							{!canSelectModel && (
								<span className="text-muted-foreground font-normal ml-2">
									(test API key first)
								</span>
							)}
						</Label>
						{canSelectModel ? (
							hasModels ? (
								<Combobox
									id="model"
									value={model}
									onValueChange={setModel}
									placeholder="Select model..."
									searchPlaceholder="Search models..."
									emptyText="No models found."
									options={availableModels.map((m) => ({
										value: m.id,
										label: m.display_name,
										description: m.id !== m.display_name ? m.id : undefined,
									}))}
								/>
							) : (
								<Input
									id="model"
									placeholder="Enter model identifier"
									value={model}
									onChange={(e) => setModel(e.target.value)}
								/>
							)
						) : (
							<Input
								id="model"
								placeholder="Test API key to load available models"
								disabled
								value=""
							/>
						)}
					</div>

					{/* Advanced Settings */}
					<div className="space-y-4 pt-4 border-t">
						<h4 className="text-sm font-medium">
							Advanced Settings
						</h4>

						{/* Max Tokens */}
						<div className="space-y-3">
							<div className="flex items-center justify-between gap-3">
								<Label htmlFor="max-tokens">
									Max Output Tokens
								</Label>
								<span className="text-sm text-muted-foreground">
									{maxTokens.toLocaleString()}
								</span>
							</div>
							<Slider
								id="max-tokens"
								min={1024}
								max={32768}
								step={1024}
								value={[maxTokens]}
								onValueChange={(values: number[]) =>
									setMaxTokens(values[0])
								}
							/>
							<p className="text-xs text-muted-foreground">
								Maximum tokens per response. Most models
								default to 4,096 but can generate up to
								32K+.
							</p>
						</div>

						{/* Default System Prompt */}
						<div className="space-y-2">
							<Label htmlFor="default-system-prompt">
								Default System Prompt
							</Label>
							<Textarea
								id="default-system-prompt"
								placeholder="You are a helpful AI assistant..."
								value={defaultSystemPrompt}
								onChange={(e) =>
									setDefaultSystemPrompt(e.target.value)
								}
								rows={4}
								className="font-mono text-sm resize-none"
							/>
							<p className="text-xs text-muted-foreground">
								System prompt used when chatting without a
								specific agent. Leave empty to use the built-in
								default.
							</p>
						</div>

						{/* Summarization Model Override */}
						<div className="space-y-2">
							<Label htmlFor="summarization-model">
								Summarization model (optional)
							</Label>
							<Input
								id="summarization-model"
								placeholder="Leave blank to use primary model"
								value={summarizationModel}
								onChange={(e) =>
									setSummarizationModel(e.target.value)
								}
							/>
							<p className="text-xs text-muted-foreground">
								Override the model used for post-run summarization.
								Uses the primary provider and API key.
							</p>
						</div>

						{/* Tuning Model Override */}
						<div className="space-y-2">
							<Label htmlFor="tuning-model">
								Tuning model (optional)
							</Label>
							<Input
								id="tuning-model"
								placeholder="Leave blank to use primary model"
								value={tuningModel}
								onChange={(e) =>
									setTuningModel(e.target.value)
								}
							/>
							<p className="text-xs text-muted-foreground">
								Override the model used for agent tuning chat and
								dry-runs. Uses the primary provider and API key.
							</p>
						</div>
					</div>

					{/* Save Button */}
					<div className="flex flex-col items-end gap-2 pt-4">
						{!canSave && isNewApiKey && !isVerified && (
							<p className="text-xs text-muted-foreground">
								Test your API key before saving
							</p>
						)}
						<Button onClick={handleSave} disabled={!canSave}>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Saving...
								</>
							) : (
								"Save Configuration"
							)}
						</Button>
					</div>
				</CardContent>
			</Card>

			{/* Embedding Configuration Card */}
			<EmbeddingConfigCard
				llmProvider={config?.provider}
				llmEndpoint={config?.endpoint ?? null}
			/>

			{/* Model Pricing Card */}
			<ModelPricingCard refreshKey={pricingRefreshKey} />

			{/* Info Card */}
			<Card>
				<CardHeader>
					<CardTitle className="text-base">About AI Chat</CardTitle>
				</CardHeader>
				<CardContent className="space-y-2 text-sm text-muted-foreground">
					<p>
						Once configured, AI chat will be available to users
						through the Chat page:
					</p>
					<ul className="list-disc list-inside space-y-1 ml-2">
						<li>
							Users can have conversations with the AI assistant
						</li>
						<li>
							Conversations are saved and can be continued later
						</li>
						<li>
							Agents can be configured to provide specialized
							assistance
						</li>
						<li>Token usage is tracked per conversation</li>
					</ul>
				</CardContent>
			</Card>

			{/* Delete Confirmation Dialog */}
			<AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Remove AI Configuration</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to remove the AI provider configuration?
							This will disable AI chat functionality until reconfigured.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={saving}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							disabled={saving}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Removing...
								</>
							) : (
								"Remove Configuration"
							)}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

/**
 * Embedding Configuration Component
 *
 * Configures embeddings (used by the Knowledge Store / RAG). Supports any
 * OpenAI-compatible endpoint via an `endpoint` override (e.g. OpenRouter,
 * Azure, Ollama). When the LLM provider is OpenAI-compatible, this card can
 * inherit its key + endpoint without an explicit dedicated config.
 */
const DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1";

function isDefaultOpenAIEndpoint(value: string | null | undefined): boolean {
	if (!value) return true;
	return value.replace(/\/+$/, "") === DEFAULT_OPENAI_ENDPOINT.replace(/\/+$/, "");
}

/**
 * Pull a human-friendly message off whatever the API client throws.
 * openapi-react-query throws the parsed body for HTTP errors, which is the
 * `{ detail: "..." }` shape FastAPI returns. Native Error instances (network
 * failures, etc.) carry .message. Fall back to JSON stringify so we never
 * say "unknown error" when the server told us something specific.
 */
function extractErrorMessage(error: unknown): string {
	if (typeof error === "object" && error !== null) {
		const body = error as { detail?: unknown; message?: unknown };
		if (typeof body.detail === "string") return body.detail;
		if (typeof body.message === "string") return body.message;
	}
	if (error instanceof Error) return error.message;
	if (typeof error === "string") return error;
	try {
		return JSON.stringify(error);
	} catch {
		return "Unknown error";
	}
}

function EmbeddingConfigCard({
	llmProvider,
	llmEndpoint,
}: {
	llmProvider?: string;
	llmEndpoint?: string | null;
}) {
	const navigate = useNavigate();
	const [apiKey, setApiKey] = useState("");
	const [model, setModel] = useState("");
	const [endpoint, setEndpoint] = useState("");
	// `override` flips on when the user explicitly wants their own endpoint+key
	// (not inheriting from the LLM card). Whole-config: both endpoint and key
	// inputs become editable, key clears, Test button appears.
	const [override, setOverride] = useState(false);
	const [availableModels, setAvailableModels] = useState<string[]>([]);
	const [modelsLoaded, setModelsLoaded] = useState(false);
	const [saving, setSaving] = useState(false);
	const [testing, setTesting] = useState(false);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	// Reindex confirmation dialog: server returns this payload when the new
	// model's dim differs from the saved one and there are existing rows.
	const [reindexConfirm, setReindexConfirm] = useState<{
		row_count: number;
		old_dim: number | null;
		new_dim: number | null;
		old_model: string | null;
		new_model: string | null;
	} | null>(null);
	const [reindexing, setReindexing] = useState(false);

	// Load current embedding configuration
	const {
		data: config,
		isLoading,
		refetch,
	} = $api.useQuery("get", "/api/admin/llm/embedding-config", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	// Mutations
	const saveMutation = $api.useMutation(
		"post",
		"/api/admin/llm/embedding-config",
	);
	const deleteMutation = $api.useMutation(
		"delete",
		"/api/admin/llm/embedding-config",
	);
	const testMutation = $api.useMutation(
		"post",
		"/api/admin/llm/embedding-test",
	);

	const needsDedicatedKey = llmProvider === "anthropic";
	const hasSavedConfig = config?.is_configured === true && !config?.uses_llm_key;
	// Inheritance is available iff the LLM provider is OpenAI-compatible
	// (i.e. provider === "openai"). Anthropic has no embeddings endpoint.
	const inheritAvailable = llmProvider === "openai";
	// Inherit when: inheritance is available, no dedicated config exists, and
	// the user hasn't clicked Override.
	const inheritActive = inheritAvailable && !hasSavedConfig && !override;
	// Endpoint+key inputs are editable when the user is actively configuring
	// (no saved config and no inheritance, OR explicit Override). Otherwise
	// they're disabled and show the resolved current value.
	const editingCredentials = override || (!hasSavedConfig && !inheritActive);

	// Sync local state from loaded config — runs whenever the config object
	// identity changes (so refetch() after save resets the form).
	const [prevConfigRef, setPrevConfigRef] = useState<typeof config>(undefined);
	if (config && prevConfigRef !== config) {
		setPrevConfigRef(config);
		setModel(config.model ?? "");
		setEndpoint(config.endpoint ?? "");
		setApiKey("");
		setAvailableModels([]);
		setModelsLoaded(false);
	}

	// Auto-load model list when we have credentials we can use without the
	// user typing anything: inherit-mode (LLM provider is openai) or there's
	// a saved dedicated config. Fires on mount and whenever the credential
	// surface changes.
	const canAutoLoadModels =
		(inheritActive || (hasSavedConfig && !override)) && !modelsLoaded && !testing;
	if (canAutoLoadModels) {
		// Trigger via the test mutation; backend resolves to inherited or saved key.
		void (async () => {
			try {
				setTesting(true);
				const result = await testMutation.mutateAsync({
					body: {
						api_key: undefined,
						model: "",
						endpoint: undefined,
					},
				});
				if (result.success) {
					setAvailableModels(result.models ?? []);
				}
			} catch {
				// Silent — user can still type a model id manually.
			} finally {
				setModelsLoaded(true);
				setTesting(false);
			}
		})();
	}

	// Compute the endpoint value to send: empty/default → null.
	const endpointToSend = (() => {
		const trimmed = endpoint.trim();
		if (!trimmed || isDefaultOpenAIEndpoint(trimmed)) return null;
		return trimmed;
	})();

	// Manual Test (override mode only — in inherit mode we trust the LLM
	// card's own validation and just auto-list models).
	const handleTest = async () => {
		if (!apiKey) {
			toast.error("Please enter an API key");
			return;
		}

		setTesting(true);

		try {
			const result = await testMutation.mutateAsync({
				body: {
					api_key: apiKey,
					model: "",
					endpoint: endpointToSend,
				},
			});

			if (result.success) {
				toast.success("Endpoint reachable", {
					description: result.models
						? `Found ${result.models.length} embedding model(s).`
						: result.message,
				});
				setAvailableModels(result.models ?? []);
				setModelsLoaded(true);
			} else {
				toast.error("Test failed", {
					description: result.message,
				});
				setModelsLoaded(false);
			}
		} catch (error) {
			toast.error("Test failed", { description: extractErrorMessage(error) });
			setModelsLoaded(false);
		} finally {
			setTesting(false);
		}
	};

	// Save runs the live embedding test on the backend; 400 surfaces here as
	// a thrown error.
	// Save runs the live embedding test on the backend; 400 surfaces here as
	// a thrown error. When the new model's dim differs from the saved one and
	// knowledge_store has existing rows, the response carries
	// needs_reindex_confirmation=true and we open a dialog instead of toasting.
	const performSave = async (confirmReindex: boolean) => {
		setSaving(true);
		try {
			const result = (await saveMutation.mutateAsync({
				body: {
					api_key: apiKey || undefined,
					model,
					endpoint: endpointToSend,
					confirm_reindex: confirmReindex,
				},
			})) as unknown as {
				saved: boolean;
				notification_id?: string | null;
				needs_reindex_confirmation?: boolean;
				row_count?: number | null;
				old_dim?: number | null;
				new_dim?: number | null;
				old_model?: string | null;
				new_model?: string | null;
			};

			if (result.needs_reindex_confirmation) {
				setReindexConfirm({
					row_count: result.row_count ?? 0,
					old_dim: result.old_dim ?? null,
					new_dim: result.new_dim ?? null,
					old_model: result.old_model ?? null,
					new_model: result.new_model ?? null,
				});
				return;
			}

			if (result.saved) {
				if (result.notification_id) {
					toast.success("Embedding configuration saved", {
						description:
							"Re-indexing the knowledge store — track progress in the notification center.",
					});
				} else {
					toast.success("Embedding configuration saved");
				}
				await refetch();
				setOverride(false);
				setReindexConfirm(null);
			}
		} catch (error) {
			toast.error("Failed to save embedding configuration", {
				description: extractErrorMessage(error),
			});
		} finally {
			setSaving(false);
		}
	};

	const handleSave = async () => {
		if (!model) {
			toast.error("Please select a model");
			return;
		}
		await performSave(false);
	};

	// Trigger an on-demand reindex against the saved config (no save).
	const handleReindex = async () => {
		setReindexing(true);
		try {
			const response = await authFetch("/api/admin/llm/embedding-reindex", {
				method: "POST",
			});
			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				throw new Error(errorData.detail ?? "Reindex failed");
			}
			const data = (await response.json()) as {
				notification_id: string;
				row_count: number;
			};
			if (data.row_count === 0) {
				toast.info("Knowledge store is empty — nothing to reindex.");
			} else {
				toast.success("Reindex started", {
					description: `Re-embedding ${data.row_count} row${
						data.row_count === 1 ? "" : "s"
					} — progress in the notification center.`,
				});
			}
		} catch (error) {
			toast.error("Failed to start reindex", {
				description: extractErrorMessage(error),
			});
		} finally {
			setReindexing(false);
		}
	};

	// Delete configuration
	const handleDelete = async () => {
		setSaving(true);
		setShowDeleteConfirm(false);

		try {
			await deleteMutation.mutateAsync({});
			setApiKey("");
			setModel("");
			setEndpoint("");
			setOverride(false);
			setAvailableModels([]);
			setModelsLoaded(false);
			toast.success("Embedding configuration removed");
			await refetch();
		} catch (error) {
			toast.error("Failed to remove embedding configuration", {
				description: extractErrorMessage(error),
			});
		} finally {
			setSaving(false);
		}
	};

	// Reindex button is disabled while a running reindex notification exists
	// for this user — prevents double-trigger. Hook must be called before
	// any conditional returns. Returning a boolean (not a derived array) keeps
	// Zustand's referential-equality check stable across renders — selectors
	// that build new arrays each call cause an infinite re-render loop.
	const hasActiveReindexNotification = useNotificationStore((state) =>
		state.notifications.some(
			(n) =>
				n.category === "embedding_reindex" &&
				(n.status === "running" || n.status === "pending"),
		),
	);

	if (isLoading) {
		return (
			<Card>
				<CardContent className="flex items-center justify-center py-8">
					<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
				</CardContent>
			</Card>
		);
	}

	// Save is allowed when:
	// - A model has been picked, AND
	// - We have credentials available (inherited via LLM, or typed/saved).
	// The backend's POST runs the real embed test before persisting, so we
	// don't need to gate Save on a separate Test click here.
	const haveCredentials = inheritActive || apiKey.length > 0 || (hasSavedConfig && !override);
	const canSave = !saving && model.length > 0 && haveCredentials;

	const reindexInFlight = hasActiveReindexNotification || reindexing;

	return (
		<Card>
			<CardHeader>
				<div className="flex items-center gap-2">
					<Database className="h-5 w-5" />
					<CardTitle>Embedding Configuration</CardTitle>
				</div>
				<CardDescription>
					Configure embeddings for the Knowledge Store (RAG). Works with
					any OpenAI-compatible endpoint.
					{needsDedicatedKey && (
						<span className="block mt-1 text-amber-600 dark:text-amber-400">
							Anthropic doesn't provide embeddings — a dedicated
							OpenAI-compatible key is required.
						</span>
					)}
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{/* Status Banner */}
				{config?.is_configured ? (
					<div className="rounded-lg border bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900 p-4">
						<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
							<div className="flex items-center gap-2">
								<CheckCircle2 className="h-4 w-4 text-green-600" />
								<span className="text-sm font-medium text-green-800 dark:text-green-200">
									Embeddings Configured
								</span>
							</div>
							<div className="flex flex-col gap-2 sm:flex-row sm:items-center">
								<Button
									variant="outline"
									size="sm"
									className="w-full justify-center sm:w-auto"
									onClick={() =>
										navigate("/settings/maintenance")
									}
								>
									Index Docs
									<ArrowRight className="h-4 w-4 ml-1" />
								</Button>
								{!override && (
									<Button
										variant="outline"
										size="sm"
										className="w-full justify-center sm:w-auto"
										onClick={() => {
											setOverride(true);
											setEndpoint(config?.endpoint ?? "");
											setAvailableModels([]);
											setModelsLoaded(false);
										}}
									>
										Override
									</Button>
								)}
								<Button
									variant="ghost"
									size="sm"
									onClick={() =>
										setShowDeleteConfirm(true)
									}
									className="w-full justify-center text-destructive hover:text-destructive sm:w-auto"
								>
									<Trash2 className="h-4 w-4 mr-1" />
									Remove
								</Button>
							</div>
						</div>
						<p className="mt-1 text-sm text-green-700 dark:text-green-300">
							{`Dedicated key configured (${config.model}${
								isDefaultOpenAIEndpoint(config.endpoint)
									? ", default OpenAI endpoint"
									: `, endpoint: ${config.endpoint}`
							})`}
						</p>
					</div>
				) : (
					<div className="rounded-lg border bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900 p-4">
						<div className="flex items-center gap-2">
							<AlertCircle className="h-4 w-4 text-amber-600" />
							<span className="text-sm font-medium text-amber-800 dark:text-amber-200">
								Embeddings Not Configured
							</span>
						</div>
						<p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
							Knowledge Store features require embedding
							configuration.
						</p>
					</div>
				)}

				{/* Inherit-mode informational row with Override action */}
				{inheritActive && (
					<div className="flex flex-col gap-3 rounded-md bg-muted/50 px-3 py-2 ring-1 ring-foreground/5 sm:flex-row sm:items-start sm:justify-between">
						<p className="text-sm text-muted-foreground">
							Inheriting endpoint and key from your LLM
							provider. Pick an embedding model below — Save
							runs a real embedding to confirm it works.
						</p>
						<Button
							variant="outline"
							size="sm"
							className="w-full justify-center sm:w-auto"
							onClick={() => {
								setOverride(true);
								// Prefill endpoint with what we'd otherwise inherit
								// so the user can edit instead of starting blank.
								setEndpoint(llmEndpoint ?? "");
								setApiKey("");
								setAvailableModels([]);
								setModelsLoaded(false);
							}}
						>
							Override
						</Button>
					</div>
				)}

				{/* Endpoint */}
				<div className="space-y-2">
					<Label htmlFor="embedding-endpoint">API Endpoint</Label>
					<Input
						id="embedding-endpoint"
						placeholder={DEFAULT_OPENAI_ENDPOINT}
						value={
							editingCredentials
								? endpoint
								: inheritActive
									? (llmEndpoint ?? DEFAULT_OPENAI_ENDPOINT)
									: (config?.endpoint ?? DEFAULT_OPENAI_ENDPOINT)
						}
						onChange={(e) => {
							setEndpoint(e.target.value);
							setModelsLoaded(false);
							setAvailableModels([]);
						}}
						disabled={!editingCredentials}
					/>
					{editingCredentials && (
						<p className="text-xs text-muted-foreground">
							Leave blank or set to {DEFAULT_OPENAI_ENDPOINT} for the default OpenAI endpoint.
						</p>
					)}
				</div>

				{/* API Key (+ Test button only when editing credentials) */}
				<div className="space-y-2">
					<Label htmlFor="embedding-api-key">API Key</Label>
					<div className="flex gap-2">
						<Input
							id="embedding-api-key"
							type="password"
							autoComplete="off"
							placeholder={
								inheritActive
									? "(inherited from LLM provider)"
									: hasSavedConfig && !override
										? "(saved — click Override to change)"
										: config?.api_key_set
											? "API key saved - enter new key to change"
											: "sk-..."
							}
							value={apiKey}
							onChange={(e) => {
								setApiKey(e.target.value);
								setModelsLoaded(false);
								setAvailableModels([]);
							}}
							disabled={!editingCredentials}
						/>
						{editingCredentials && (
							<Button
								variant="secondary"
								onClick={handleTest}
								disabled={testing || !apiKey}
							>
								{testing ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Testing...
									</>
								) : (
									<>
										<Zap className="h-4 w-4 mr-2" />
										Test
									</>
								)}
							</Button>
						)}
					</div>
				</div>

				{/* Model Selection */}
				<div className="space-y-2">
					<Label htmlFor="embedding-model">
						Model
						{!modelsLoaded && !inheritActive && !hasSavedConfig && (
							<span className="text-muted-foreground font-normal ml-2">
								(test endpoint first)
							</span>
						)}
					</Label>
					{availableModels.length > 0 ? (
						<Combobox
							id="embedding-model"
							value={model}
							onValueChange={setModel}
							placeholder="Select model..."
							searchPlaceholder="Search models..."
							emptyText="No models found."
							options={availableModels.map((m) => ({
								value: m,
								label: m,
							}))}
						/>
					) : (
						<Input
							id="embedding-model"
							placeholder="e.g. text-embedding-3-small"
							value={model}
							onChange={(e) => setModel(e.target.value)}
						/>
					)}
					{modelsLoaded && availableModels.length === 0 && (
						<p className="text-xs text-muted-foreground">
							Endpoint didn't return a model list. Enter the model id manually.
						</p>
					)}
				</div>

				{/* Save / Reindex / Cancel */}
				<div className="flex flex-col gap-2 pt-2 sm:flex-row sm:items-center sm:justify-between">
					<div className="flex flex-col gap-2 sm:flex-row sm:items-center">
						{override && hasSavedConfig && (
							<Button
								variant="ghost"
								size="sm"
								className="w-full justify-center sm:w-auto"
								onClick={() => {
									setOverride(false);
									setApiKey("");
									setEndpoint(config?.endpoint ?? "");
									setModel(config?.model ?? "");
									setAvailableModels([]);
									setModelsLoaded(false);
								}}
							>
								Cancel
							</Button>
						)}
						{hasSavedConfig && (
							<Button
								variant="outline"
								size="sm"
								className="w-full justify-center whitespace-normal sm:w-auto sm:whitespace-nowrap"
								onClick={handleReindex}
								disabled={reindexInFlight}
								title="Re-embed every knowledge store row against the saved embedding config."
							>
								{reindexInFlight ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Reindexing...
									</>
								) : (
									<>
										<Database className="h-4 w-4 mr-2" />
										Reindex knowledge store
									</>
								)}
							</Button>
						)}
					</div>
					<Button
						onClick={handleSave}
						disabled={!canSave}
						className="w-full whitespace-normal sm:w-auto sm:whitespace-nowrap"
					>
						{saving ? (
							<>
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
								Saving...
							</>
						) : (
							"Save Embedding Config"
						)}
					</Button>
				</div>

				{/* Reindex Confirmation Dialog (dim-changed save) */}
				<Dialog
					open={reindexConfirm !== null}
					onOpenChange={(open) => {
						if (!open) setReindexConfirm(null);
					}}
				>
					<DialogContent>
						<DialogHeader>
							<DialogTitle>Re-embed knowledge store?</DialogTitle>
							<DialogDescription>
								{reindexConfirm?.row_count ?? 0} existing row
								{reindexConfirm?.row_count === 1 ? "" : "s"}{" "}
								{reindexConfirm?.row_count === 1 ? "is" : "are"}{" "}
								embedded with vectors that don't match the new
								model's{" "}
								<strong>{reindexConfirm?.new_dim ?? "?"}-dim</strong>{" "}
								output
								{reindexConfirm?.old_model
									? ` (previously ${reindexConfirm.old_model})`
									: ""}
								. Search won't work against those rows until
								they're re-embedded. Re-embedding runs in the
								background and you can cancel from the
								notification center.
							</DialogDescription>
						</DialogHeader>
						<DialogFooter>
							<Button
								variant="outline"
								onClick={() => setReindexConfirm(null)}
								disabled={saving}
							>
								Cancel
							</Button>
							<Button
								onClick={() => performSave(true)}
								disabled={saving}
							>
								{saving ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Saving...
									</>
								) : (
									"Save and re-embed"
								)}
							</Button>
						</DialogFooter>
					</DialogContent>
				</Dialog>

				{/* Delete Confirmation */}
				<Dialog
					open={showDeleteConfirm}
					onOpenChange={setShowDeleteConfirm}
				>
					<DialogContent>
						<DialogHeader>
							<DialogTitle>
								Remove Embedding Configuration
							</DialogTitle>
							<DialogDescription>
								Remove the dedicated embedding API key? If your
								LLM provider supports embeddings, the card will
								fall back to inheriting its key.
							</DialogDescription>
						</DialogHeader>
						<DialogFooter>
							<Button
								variant="outline"
								onClick={() => setShowDeleteConfirm(false)}
								disabled={saving}
							>
								Cancel
							</Button>
							<Button
								variant="destructive"
								onClick={handleDelete}
								disabled={saving}
							>
								{saving ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Removing...
									</>
								) : (
									"Remove"
								)}
							</Button>
						</DialogFooter>
					</DialogContent>
				</Dialog>
			</CardContent>
		</Card>
	);
}

/**
 * Model Pricing Configuration Component
 *
 * Manage AI model pricing for cost tracking.
 * Shows models that have been used with their pricing, and allows adding/editing.
 */
function ModelPricingCard({ refreshKey = 0 }: { refreshKey?: number }) {
	const [pricingData, setPricingData] = useState<AIModelPricingListItem[]>(
		[],
	);
	const [modelsWithoutPricing, setModelsWithoutPricing] = useState<string[]>(
		[],
	);
	const [isLoading, setIsLoading] = useState(true);
	const [editingId, setEditingId] = useState<number | null>(null);
	const [editValues, setEditValues] = useState<{
		input: string;
		output: string;
	}>({ input: "", output: "" });
	const [saving, setSaving] = useState(false);
	const [showAddDialog, setShowAddDialog] = useState(false);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	const [deletingPricing, setDeletingPricing] =
		useState<AIModelPricingListItem | null>(null);
	const [newPricing, setNewPricing] = useState<AIModelPricingCreate>({
		provider: "openai",
		model: "",
		input_price_per_million: 0,
		output_price_per_million: 0,
	});

	// Load pricing data (with loading spinner for initial load)
	const loadPricing = async () => {
		try {
			setIsLoading(true);
			const data = await listPricing();
			setPricingData(data.pricing || []);
			setModelsWithoutPricing(data.models_without_pricing || []);
		} catch (error) {
			toast.error("Failed to load pricing data", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setIsLoading(false);
		}
	};

	// Refresh pricing data without showing loading spinner
	const refreshPricing = async () => {
		try {
			const data = await listPricing();
			setPricingData(data.pricing || []);
			setModelsWithoutPricing(data.models_without_pricing || []);
		} catch {
			// Silent refresh — don't toast on background refresh
		}
	};

	useEffect(() => {
		void (async () => {
			await loadPricing();
		})();
	}, []);

	// Refresh when parent signals (e.g., after config save)
	useEffect(() => {
		if (refreshKey > 0) {
			void (async () => {
				await refreshPricing();
			})();
		}
	}, [refreshKey]);

	// Format price for display
	const formatPrice = (price: number | string | null | undefined): string => {
		if (price === null || price === undefined) return "-";
		const numPrice = typeof price === "string" ? parseFloat(price) : price;
		if (isNaN(numPrice)) return "-";
		return `$${numPrice.toFixed(2)}`;
	};

	// Format date for display
	const formatDate = (dateStr: string | null | undefined): string => {
		if (!dateStr) return "-";
		return new Date(dateStr).toLocaleDateString();
	};

	// Start editing a row
	const startEdit = (item: AIModelPricingListItem) => {
		if (item.id === null) return;
		setEditingId(item.id);
		setEditValues({
			input: item.input_price_per_million?.toString() || "0",
			output: item.output_price_per_million?.toString() || "0",
		});
	};

	// Cancel editing
	const cancelEdit = () => {
		setEditingId(null);
		setEditValues({ input: "", output: "" });
	};

	// Save edited values
	const saveEdit = async () => {
		if (editingId === null) return;

		setSaving(true);
		try {
			await updatePricing(editingId, {
				input_price_per_million: parseFloat(editValues.input) || 0,
				output_price_per_million: parseFloat(editValues.output) || 0,
			});
			toast.success("Pricing updated");
			await loadPricing();
			cancelEdit();
		} catch (error) {
			toast.error("Failed to update pricing", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	// Add new pricing
	const handleAddPricing = async () => {
		if (!newPricing.model) {
			toast.error("Please enter a model name");
			return;
		}

		setSaving(true);
		try {
			await createPricing(newPricing);
			toast.success("Pricing added", {
				description: `Added pricing for ${newPricing.model}`,
			});
			await loadPricing();
			setShowAddDialog(false);
			setNewPricing({
				provider: "openai",
				model: "",
				input_price_per_million: 0,
				output_price_per_million: 0,
			});
		} catch (error) {
			toast.error("Failed to add pricing", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	// Show delete confirmation dialog
	const handleDeletePricing = (item: AIModelPricingListItem) => {
		setDeletingPricing(item);
		setShowDeleteConfirm(true);
	};

	// Confirm delete
	const confirmDeletePricing = async () => {
		if (!deletingPricing?.id) return;

		const deletedId = deletingPricing.id;
		const deletedModel = `${deletingPricing.provider}/${deletingPricing.model}`;

		// Optimistically remove from state
		setPricingData((prev) => prev.filter((p) => p.id !== deletedId));
		setShowDeleteConfirm(false);
		setDeletingPricing(null);

		try {
			await deletePricing(deletedId);
			toast.success("Pricing deleted", {
				description: `Removed pricing for ${deletedModel}`,
			});
			// Background refresh to sync any server-side changes
			refreshPricing();
		} catch (error) {
			toast.error("Failed to delete pricing", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
			// Revert on failure
			refreshPricing();
		}
	};

	if (isLoading) {
		return (
			<Card>
				<CardContent className="flex items-center justify-center py-8">
					<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
				</CardContent>
			</Card>
		);
	}

	const hasWarnings = modelsWithoutPricing.length > 0;

	// Parse "provider/model" strings into objects for display
	const unpricedModels = modelsWithoutPricing.map((pm) => {
		const [provider, ...modelParts] = pm.split("/");
		return { provider, model: modelParts.join("/") };
	});

	return (
		<Card>
			<CardHeader>
				<div className="flex items-center justify-between">
					<div className="flex items-center gap-2">
						<DollarSign className="h-5 w-5" />
						<CardTitle>Model Pricing</CardTitle>
						{hasWarnings && (
							<Badge variant="warning">
								{modelsWithoutPricing.length} unpriced
							</Badge>
						)}
					</div>
					<Button
						variant="outline"
						size="sm"
						onClick={() => setShowAddDialog(true)}
					>
						<Plus className="h-4 w-4 mr-1" />
						Add Model
					</Button>
				</div>
				<CardDescription>
					Configure pricing per million tokens for cost tracking.
					{hasWarnings && (
						<span className="block mt-1 text-amber-600 dark:text-amber-400">
							{modelsWithoutPricing.length} model(s) have been
							used but don't have pricing configured.
						</span>
					)}
				</CardDescription>
			</CardHeader>
			<CardContent>
				{pricingData.length === 0 &&
				modelsWithoutPricing.length === 0 ? (
					<div className="text-center py-8 text-muted-foreground">
						<DollarSign className="h-8 w-8 mx-auto mb-2 opacity-50" />
						<p>No model pricing configured yet.</p>
						<p className="text-sm mt-1">
							Add pricing to track AI usage costs.
						</p>
					</div>
				) : (
					<div className="overflow-hidden rounded-xl ring-1 ring-foreground/5 dark:ring-foreground/10">
						<Table>
							<TableHeader className="bg-muted/50">
								<TableRow>
									<TableHead>Provider</TableHead>
									<TableHead>Model</TableHead>
									<TableHead className="text-right">
										Input ($/1M)
									</TableHead>
									<TableHead className="text-right">
										Output ($/1M)
									</TableHead>
									<TableHead>Last Updated</TableHead>
									<TableHead className="w-[100px]"></TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{/* Models without pricing (need attention) */}
								{unpricedModels.map((item) => (
									<TableRow
										key={`unpriced-${item.provider}-${item.model}`}
										className="bg-amber-50/50 dark:bg-amber-950/10"
									>
										<TableCell className="font-medium capitalize">
											{item.provider}
										</TableCell>
										<TableCell>
											<div className="flex items-center gap-2">
												{item.model}
												<Badge variant="warning">
													No pricing
												</Badge>
											</div>
										</TableCell>
										<TableCell className="text-right text-muted-foreground">
											-
										</TableCell>
										<TableCell className="text-right text-muted-foreground">
											-
										</TableCell>
										<TableCell className="text-muted-foreground">
											-
										</TableCell>
										<TableCell>
											<div className="flex items-center justify-end">
												<Button
													variant="outline"
													size="sm"
													onClick={() => {
														setNewPricing({
															provider:
																item.provider,
															model: item.model,
															input_price_per_million: 0,
															output_price_per_million: 0,
														});
														setShowAddDialog(true);
													}}
												>
													<Plus className="h-4 w-4 mr-1" />
													Add
												</Button>
											</div>
										</TableCell>
									</TableRow>
								))}
								{/* Models with pricing configured */}
								{pricingData.map((item) => (
									<TableRow key={item.id}>
										<TableCell className="font-medium capitalize">
											{item.provider}
										</TableCell>
										<TableCell>
											<div className="flex items-center gap-2">
												{item.model}
												{item.is_used && (
													<Badge variant="secondary">
														In use
													</Badge>
												)}
											</div>
										</TableCell>
										<TableCell className="text-right">
											{editingId === item.id ? (
												<Input
													type="number"
													step="0.01"
													min="0"
													value={editValues.input}
													onChange={(e) =>
														setEditValues({
															...editValues,
															input: e.target
																.value,
														})
													}
													className="w-24 text-right"
												/>
											) : (
												formatPrice(
													item.input_price_per_million,
												)
											)}
										</TableCell>
										<TableCell className="text-right">
											{editingId === item.id ? (
												<Input
													type="number"
													step="0.01"
													min="0"
													value={editValues.output}
													onChange={(e) =>
														setEditValues({
															...editValues,
															output: e.target
																.value,
														})
													}
													className="w-24 text-right"
												/>
											) : (
												formatPrice(
													item.output_price_per_million,
												)
											)}
										</TableCell>
										<TableCell>
											{formatDate(item.updated_at)}
										</TableCell>
										<TableCell>
											<div className="flex items-center gap-1 justify-end">
												{editingId === item.id ? (
													<>
														<Button
															variant="ghost"
															size="sm"
															onClick={saveEdit}
															disabled={saving}
														>
															{saving ? (
																<Loader2 className="h-4 w-4 animate-spin" />
															) : (
																<Check className="h-4 w-4 text-green-600" />
															)}
														</Button>
														<Button
															variant="ghost"
															size="sm"
															onClick={cancelEdit}
															disabled={saving}
														>
															<X className="h-4 w-4" />
														</Button>
													</>
												) : (
													<>
														<Button
															variant="ghost"
															size="sm"
															onClick={() =>
																startEdit(item)
															}
														>
															<Pencil className="h-4 w-4" />
														</Button>
														<Button
															variant="ghost"
															size="sm"
															onClick={() =>
																handleDeletePricing(
																	item,
																)
															}
															className="text-destructive hover:text-destructive"
														>
															<Trash2 className="h-4 w-4" />
														</Button>
													</>
												)}
											</div>
										</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
					</div>
				)}
			</CardContent>

			{/* Add Pricing Dialog */}
			<Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Add Model Pricing</DialogTitle>
						<DialogDescription>
							Configure pricing for a new AI model.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-4 py-4">
						<div className="space-y-2">
							<Label htmlFor="new-provider">Provider</Label>
							<Select
								value={newPricing.provider}
								onValueChange={(value) =>
									setNewPricing({
										...newPricing,
										provider: value,
									})
								}
							>
								<SelectTrigger id="new-provider">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="openai">
										OpenAI
									</SelectItem>
									<SelectItem value="anthropic">
										Anthropic
									</SelectItem>
									<SelectItem value="custom">
										Custom
									</SelectItem>
								</SelectContent>
							</Select>
						</div>
						<div className="space-y-2">
							<Label htmlFor="new-model">Model Name</Label>
							<Input
								id="new-model"
								placeholder="e.g., gpt-4o, claude-3-opus"
								value={newPricing.model}
								onChange={(e) =>
									setNewPricing({
										...newPricing,
										model: e.target.value,
									})
								}
							/>
						</div>
						<div className="grid grid-cols-2 gap-4">
							<div className="space-y-2">
								<Label htmlFor="new-input-price">
									Input Price ($/1M tokens)
								</Label>
								<Input
									id="new-input-price"
									type="number"
									step="0.01"
									min="0"
									placeholder="0.00"
									value={
										newPricing.input_price_per_million || ""
									}
									onChange={(e) =>
										setNewPricing({
											...newPricing,
											input_price_per_million:
												parseFloat(e.target.value) || 0,
										})
									}
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="new-output-price">
									Output Price ($/1M tokens)
								</Label>
								<Input
									id="new-output-price"
									type="number"
									step="0.01"
									min="0"
									placeholder="0.00"
									value={
										newPricing.output_price_per_million ||
										""
									}
									onChange={(e) =>
										setNewPricing({
											...newPricing,
											output_price_per_million:
												parseFloat(e.target.value) || 0,
										})
									}
								/>
							</div>
						</div>
					</div>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setShowAddDialog(false)}
							disabled={saving}
						>
							Cancel
						</Button>
						<Button onClick={handleAddPricing} disabled={saving}>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Adding...
								</>
							) : (
								"Add Pricing"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Delete Confirmation Dialog */}
			<Dialog
				open={showDeleteConfirm}
				onOpenChange={setShowDeleteConfirm}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Delete Pricing Configuration</DialogTitle>
						<DialogDescription>
							Are you sure you want to delete pricing for{" "}
							<span className="font-medium">
								{deletingPricing?.provider}/
								{deletingPricing?.model}
							</span>
							? This action cannot be undone.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setShowDeleteConfirm(false);
								setDeletingPricing(null);
							}}
							disabled={saving}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={confirmDeletePricing}
							disabled={saving}
						>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Deleting...
								</>
							) : (
								"Delete"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</Card>
	);
}
