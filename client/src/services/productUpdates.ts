import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type ProductUpdatesFeed =
	components["schemas"]["ProductUpdatesFeedResponse"];
export type ProductUpdatesReceiptRequest =
	components["schemas"]["ProductUpdatesReceiptRequest"];
export type ProductUpdatesReceipt =
	components["schemas"]["ProductUpdatesReceiptResponse"];
export type ProductUpdatesBundle =
	components["schemas"]["ProductUpdatesBundle"];
export type ProductUpdateEntry = components["schemas"]["ProductUpdateEntry"];
export type ProductUpdateOtherChange =
	components["schemas"]["ProductUpdateOtherChange"];
export type ProductUpdateAsset = components["schemas"]["ProductUpdateAsset"];

export interface ProductUpdatesAdapter {
	getFeed(): Promise<ProductUpdatesFeed>;
	acknowledge(entryIds: readonly string[]): Promise<ProductUpdatesReceipt>;
}

function throwApiError(action: string, error: unknown): never {
	const detail =
		error && typeof error === "object" && "detail" in error
			? String(error.detail)
			: JSON.stringify(error);
	throw new Error(`Failed to ${action}: ${detail}`);
}

export async function getProductUpdates(): Promise<ProductUpdatesFeed> {
	const { data, error } = await apiClient.GET("/api/product-updates");
	if (error) throwApiError("load product updates", error);
	return data as ProductUpdatesFeed;
}

export async function acknowledgeProductUpdates(
	entryIds: readonly string[],
): Promise<ProductUpdatesReceipt> {
	const { data, error } = await apiClient.POST(
		"/api/product-updates/receipts",
		{
			body: {
				entry_ids: [...entryIds],
			} satisfies ProductUpdatesReceiptRequest,
		},
	);
	if (error) throwApiError("save product update acknowledgement", error);
	return data as ProductUpdatesReceipt;
}

export const productUpdatesAdapter: ProductUpdatesAdapter = {
	getFeed: getProductUpdates,
	acknowledge: acknowledgeProductUpdates,
};

export function visibleProductUpdates(
	entries: readonly ProductUpdateEntry[],
): ProductUpdateEntry[] {
	return entries.filter((entry) => entry.in_app !== false);
}
