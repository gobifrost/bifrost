/**
 * Hook for managing app dependencies via the API.
 *
 * Reads/writes to GET/PUT /api/applications/{appId}/dependencies.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";

interface UseAppDependenciesResult {
	/** Current dependencies {name: version} */
	dependencies: Record<string, string>;
	/** Whether initial load is in progress */
	isLoading: boolean;
	/** Whether a save is in progress */
	isSaving: boolean;
	loadError: string | null;
	reload: () => void;
	/** Add a package */
	addDependency: (name: string, version: string) => Promise<void>;
	/** Remove a package */
	removeDependency: (name: string) => Promise<void>;
	/** Update a package version */
	updateVersion: (name: string, version: string) => Promise<void>;
}

export function useAppDependencies(appId: string): UseAppDependenciesResult {
	const [dependencies, setDependencies] = useState<Record<string, string>>(
		{},
	);
	const [isLoading, setIsLoading] = useState(true);
	const [isSaving, setIsSaving] = useState(false);
	const [loadError, setLoadError] = useState<string | null>(null);
	const [reloadVersion, setReloadVersion] = useState(0);
	const reload = useCallback(
		() => setReloadVersion((value) => value + 1),
		[],
	);
	const savingRef = useRef(false);

	// Fetch dependencies on mount
	useEffect(() => {
		let cancelled = false;

		async function load() {
			setIsLoading(true);
			setLoadError(null);
			try {
				const response = await authFetch(
					`/api/applications/${appId}/dependencies`,
				);
				if (!response.ok)
					throw new Error("Failed to load dependencies");
				const data = await response.json();
				if (!cancelled) setDependencies(data);
			} catch (err) {
				if (!cancelled) {
					setLoadError(
						"Could not load installed packages. Retry before changing dependencies.",
					);
					console.error("Failed to load dependencies:", err);
				}
			} finally {
				if (!cancelled) setIsLoading(false);
			}
		}

		void load();
		return () => {
			cancelled = true;
		};
	}, [appId, reloadVersion]);

	// Save dependencies to API
	const saveDeps = useCallback(
		async (newDeps: Record<string, string>) => {
			if (savingRef.current)
				throw new Error("Package changes are already being saved");
			savingRef.current = true;
			setIsSaving(true);
			try {
				const response = await authFetch(
					`/api/applications/${appId}/dependencies`,
					{
						method: "PUT",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(newDeps),
					},
				);
				if (!response.ok)
					throw new Error("Failed to save dependencies");
				const validated = await response.json();
				setDependencies(validated);
			} finally {
				savingRef.current = false;
				setIsSaving(false);
			}
		},
		[appId],
	);

	const addDependency = useCallback(
		async (name: string, version: string) => {
			const newDeps = { ...dependencies, [name]: version };
			await saveDeps(newDeps);
			toast.success(`Added ${name}@${version}`);
		},
		[dependencies, saveDeps],
	);

	const removeDependency = useCallback(
		async (name: string) => {
			const newDeps = { ...dependencies };
			delete newDeps[name];
			await saveDeps(newDeps);
			toast.success(`Removed ${name}`);
		},
		[dependencies, saveDeps],
	);

	const updateVersion = useCallback(
		async (name: string, version: string) => {
			const newDeps = { ...dependencies, [name]: version };
			await saveDeps(newDeps);
		},
		[dependencies, saveDeps],
	);

	return {
		dependencies,
		isLoading,
		isSaving,
		loadError,
		reload,
		addDependency,
		removeDependency,
		updateVersion,
	};
}
