import bundle from "@/generated/product-updates.bundle.json";
import { productUpdateAssetUrls } from "@/generated/product-updates-assets";
import type {
	ProductUpdateEntry,
	ProductUpdatesBundle,
} from "@/generated/product-updates";

const RECEIPT_KEY_PREFIX = "bifrost.product-updates.receipts";

export type ProductUpdatesPreviewState =
	| "normal"
	| "empty"
	| "loading"
	| "failure"
	| "missingimage"
	| "rollback"
	| "future";
export type ProductUpdatesPreviewChannel = "dev" | "stable";

export const PRODUCT_UPDATES_RECEIPTS_EVENT =
	"bifrost:product-updates-receipts";

/**
 * Boundary for the eventual authenticated API. The preview deliberately keeps
 * receipt storage separate from bundle loading so server persistence can
 * replace it without changing page behavior.
 */
export interface ProductUpdatesAdapter {
	getBundle(
		channel: ProductUpdatesPreviewChannel,
	): Promise<ProductUpdatesBundle>;
	getReadEntryIds(adminId: string): Promise<ReadonlySet<string>>;
	markRead(adminId: string, entryIds: readonly string[]): Promise<void>;
}

export function createProductUpdatesPreviewAdapter(
	previewBundle: ProductUpdatesBundle,
	storage:
		Pick<Storage, "getItem" | "setItem"> | undefined = typeof window ===
	"undefined"
		? undefined
		: window.localStorage,
): ProductUpdatesAdapter {
	return {
		async getBundle(channel) {
			// The stable fixture is an older eligible bundle. Its retained entry
			// UUIDs demonstrate that receipts survive dev → stable promotion.
			return channel === "stable"
				? { ...previewBundle, entries: previewBundle.entries.slice(1) }
				: previewBundle;
		},
		async getReadEntryIds(adminId) {
			if (!storage)
				throw new Error("Preview receipt storage is unavailable.");
			let raw: string | null;
			try {
				raw = storage.getItem(`${RECEIPT_KEY_PREFIX}:${adminId}`);
			} catch {
				throw new Error("Preview receipt storage could not be read.");
			}
			try {
				const stored = JSON.parse(raw ?? "[]");
				return new Set(
					Array.isArray(stored)
						? stored.filter(
								(id): id is string => typeof id === "string",
							)
						: [],
				);
			} catch {
				return new Set<string>();
			}
		},
		async markRead(adminId, entryIds) {
			if (!storage)
				throw new Error("Preview receipt storage is unavailable.");
			const existing = new Set(await this.getReadEntryIds(adminId));
			for (const entryId of entryIds) existing.add(entryId);
			storage.setItem(
				`${RECEIPT_KEY_PREFIX}:${adminId}`,
				JSON.stringify([...existing].sort()),
			);
			if (typeof window !== "undefined") {
				window.dispatchEvent(new Event(PRODUCT_UPDATES_RECEIPTS_EVENT));
			}
		},
	};
}

const resolvedPreviewBundle: ProductUpdatesBundle = {
	...(bundle as ProductUpdatesBundle),
	entries: (bundle as ProductUpdatesBundle).entries.map((entry) => ({
		...entry,
		assets: entry.assets.map((asset) => ({
			...asset,
			url: productUpdateAssetUrls[asset.path] ?? asset.url,
		})),
	})),
};

export const productUpdatesPreviewAdapter = createProductUpdatesPreviewAdapter(
	resolvedPreviewBundle,
);

export function visibleProductUpdates(
	entries: readonly ProductUpdateEntry[],
	state: ProductUpdatesPreviewState,
): ProductUpdateEntry[] {
	entries = entries.filter((entry) => entry.in_app !== false);
	if (state === "empty") return [];
	if (state === "rollback") return entries.slice(1);
	if (state === "future")
		return entries.filter(
			(entry) => entry.id !== "preview-future-unmet-prerequisites",
		);
	return [...entries];
}

export function withFuturePreviewCandidate(
	entries: readonly ProductUpdateEntry[],
): ProductUpdateEntry[] {
	const source = entries[0];
	if (!source) return [];
	return [
		{
			...source,
			id: "preview-future-unmet-prerequisites",
			title: "Future Update Candidate",
			markdown:
				"This development fixture has unmet release prerequisites and is withheld from the running bundle.",
		},
		...entries,
	];
}
