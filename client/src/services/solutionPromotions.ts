import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PromotionReview = components["schemas"]["PromotionReviewDTO"];
export type PromotionResult = components["schemas"]["PromotionResultDTO"];
export type PromotionTargetRequest =
	components["schemas"]["PromotionTargetRequest"];

async function requestJson<T>(
	url: string,
	fallback: string,
	init: RequestInit = {},
): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		const detail =
			body && typeof body === "object" && typeof body.detail === "string"
				? body.detail
				: fallback;
		throw new Error(detail);
	}
	return (await response.json()) as T;
}

export async function listPromotionReviews(
	options: { signal?: AbortSignal } = {},
): Promise<PromotionReview[]> {
	const result = await requestJson<{
		promotions: PromotionReview[];
		total: number;
	}>("/api/solution-promotions", "Failed to load promotion requests", {
		signal: options.signal,
	});
	return result.promotions;
}

export async function promoteSolution(
	solutionId: string,
	request: PromotionTargetRequest,
	options: { signal?: AbortSignal } = {},
): Promise<PromotionResult> {
	return requestJson<PromotionResult>(
		`/api/solution-promotions/${solutionId}/promote`,
		"Failed to promote Solution",
		{
			method: "POST",
			body: JSON.stringify(request),
			signal: options.signal,
		},
	);
}
