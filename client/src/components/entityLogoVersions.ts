/**
 * Per-entity logo version store.
 *
 * <EntityLogo> renders an <img> at a fixed URL like /api/applications/:id/logo.
 * After an upload via <LogoDropZone>, the bytes on the server change but the
 * URL doesn't — so the browser keeps serving the cached image on other
 * surfaces (grid cards, headers) until a hard refresh.
 *
 * This module is a tiny external store: bump the version for an entity after
 * a successful upload/delete, and any <EntityLogo> for that entity rerenders
 * with a new `?v=` query param.
 */

import { useSyncExternalStore } from "react";

type Key = `app:${string}` | `agent:${string}`;

const versions = new Map<Key, number>();
const listeners = new Set<() => void>();

function key(type: "app" | "agent", id: string): Key {
	return `${type}:${id}` as Key;
}

export function bumpEntityLogo(type: "app" | "agent", id: string): void {
	const k = key(type, id);
	versions.set(k, Date.now());
	for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
	listeners.add(fn);
	return () => {
		listeners.delete(fn);
	};
}

export function useEntityLogoVersion(
	type: "app" | "agent",
	id: string,
): number | undefined {
	return useSyncExternalStore(
		subscribe,
		() => versions.get(key(type, id)),
		() => undefined,
	);
}
