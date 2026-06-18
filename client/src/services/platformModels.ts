/**
 * Platform model registry + admin model-migration API.
 */

import { useMemo } from "react";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformModel = components["schemas"]["PlatformModelPublic"];
export type PlatformModelListResponse =
	components["schemas"]["PlatformModelListResponse"];

// Platform-wide allowlist migration types
export type OrgAllowlistImpactRow =
	components["schemas"]["OrgAllowlistImpactRow"];
export type PlatformAllowlistPreviewRequest =
	components["schemas"]["PlatformAllowlistPreviewRequest"];
export type PlatformAllowlistPreviewResponse =
	components["schemas"]["PlatformAllowlistPreviewResponse"];
export type PlatformAllowlistApplyRequest =
	components["schemas"]["PlatformAllowlistApplyRequest"];
export type PlatformAllowlistApplyResponse =
	components["schemas"]["PlatformAllowlistApplyResponse"];

export type CostTier = "fast" | "balanced" | "premium";

export const COST_TIER_LABEL: Record<CostTier, string> = {
	fast: "Fast",
	balanced: "Balanced",
	premium: "Premium",
};

/**
 * Mirror of the backend's RESELLER_BY_HOST table. Returns null when the
 * endpoint is the model maker's own API (no prefix needed for lookup) or
 * unrecognized.
 */
const RESELLER_BY_HOST: Record<string, string> = {
	"openrouter.ai": "openrouter",
	"api.together.xyz": "together_ai",
	"api.fireworks.ai": "fireworks_ai",
	"api.deepinfra.com": "deepinfra",
	"api.groq.com": "groq",
	"integrate.api.nvidia.com": "nvidia_nim",
	"api.cerebras.ai": "cerebras",
	"inference.baseten.co": "baseten",
	"api.sambanova.ai": "sambanova",
	"api.ai21.com": "ai21_chat",
	"api.perplexity.ai": "perplexity",
	"api.mistral.ai": "mistral",
	"api.deepseek.com": "deepseek",
	"api.friendli.ai": "friendliai",
	"api.lambdalabs.com": "lambda_ai",
	"api.novita.ai": "novita",
	"api.hyperbolic.xyz": "hyperbolic",
	"api.replicate.com": "replicate",
	"ollama.com": "ollama",
	"api.z.ai": "z_ai",
};

export function resellerForEndpoint(endpoint: string | null | undefined): string | null {
	if (!endpoint) return null;
	try {
		const host = new URL(endpoint).hostname;
		return RESELLER_BY_HOST[host] ?? null;
	} catch {
		return null;
	}
}

/**
 * Capability lookup with multiple fallbacks. Provider-returned IDs and
 * LiteLLM keys disagree often enough that we need a chain:
 *   1. `<reseller>/<model_id>` exact (OpenRouter/Together/etc.)
 *   2. `model_id` exact (direct provider calls)
 *   3. `~`-stripped variants of both above (OpenRouter "redirect" aliases)
 *   4. Suffix-after-last-slash exact (odd routing forms)
 *   5. Endswith-match: any key whose suffix equals our suffix. Handles
 *      OpenRouter `moonshotai/kimi-k2.6` ↔ LiteLLM `moonshot/kimi-k2.6`.
 */
export function lookupModel(
	modelId: string,
	reseller: string | null,
	byId: Record<string, PlatformModel>,
): PlatformModel | null {
	const tryKey = (k: string) => byId[k] ?? null;
	const stripTilde = (s: string) => s.replace(/(^|\/)~/g, "$1");

	const cleanId = stripTilde(modelId);
	const candidates: string[] = [];
	if (reseller) {
		candidates.push(`${reseller}/${modelId}`);
		if (cleanId !== modelId) candidates.push(`${reseller}/${cleanId}`);
	}
	candidates.push(modelId);
	if (cleanId !== modelId) candidates.push(cleanId);

	for (const c of candidates) {
		const hit = tryKey(c);
		if (hit) return hit;
	}

	// Suffix exact (handles `~author/model` → `model`)
	const suffix = cleanId.includes("/")
		? cleanId.slice(cleanId.lastIndexOf("/") + 1)
		: null;
	if (suffix) {
		const hit = tryKey(suffix);
		if (hit) return hit;
		// Endswith-match: any catalog key whose own suffix is the same model.
		// Picks the first match (Object.keys order is insertion-stable in v8).
		const target = "/" + suffix;
		for (const k of Object.keys(byId)) {
			if (k === suffix || k.endsWith(target)) return byId[k];
		}
	}
	return null;
}

/**
 * Resolve a model_id to its context-window size (tokens) via the catalog,
 * using the same multi-fallback lookup chain as capability/price data.
 * Returns null when the model isn't in the catalog or has no window recorded.
 */
export function contextWindowForModel(
	modelId: string | null | undefined,
	reseller: string | null,
	byId: Record<string, PlatformModel>,
): number | null {
	if (!modelId) return null;
	const match = lookupModel(modelId, reseller, byId);
	return match?.context_window ?? null;
}

export async function listPlatformModels(): Promise<PlatformModelListResponse> {
	const { data, error } = await apiClient.GET("/api/platform-models");
	if (error) throw new Error(`Failed to list platform models: ${JSON.stringify(error)}`);
	return data;
}

/**
 * React-query hook returning the platform-model catalog indexed by model_id.
 * Cached for 5 minutes — the registry changes rarely. Returns an empty map
 * while loading or on error (callers degrade gracefully without enrichment).
 */
export function usePlatformModelsById(): Record<string, PlatformModel> {
	const { data } = useQuery({
		queryKey: ["platform-models"],
		queryFn: listPlatformModels,
		staleTime: 5 * 60 * 1000,
	});
	const models = data?.models;
	return useMemoizedById(models);
}

function useMemoizedById(
	models: PlatformModel[] | undefined,
): Record<string, PlatformModel> {
	return useMemo(() => {
		const idx: Record<string, PlatformModel> = {};
		for (const m of models ?? []) idx[m.model_id] = m;
		return idx;
	}, [models]);
}

export async function previewAllowlistMigration(
	request: PlatformAllowlistPreviewRequest,
): Promise<PlatformAllowlistPreviewResponse> {
	const { data, error } = await apiClient.POST(
		"/api/admin/models/preview-allowlist-migration",
		{ body: request },
	);
	if (error) throw new Error(`Preview failed: ${JSON.stringify(error)}`);
	return data;
}

export async function applyAllowlistMigration(
	request: PlatformAllowlistApplyRequest,
): Promise<PlatformAllowlistApplyResponse> {
	const { data, error } = await apiClient.POST(
		"/api/admin/models/apply-allowlist-migration",
		{ body: request },
	);
	if (error) throw new Error(`Apply failed: ${JSON.stringify(error)}`);
	return data;
}
