/**
 * Hook for managing auto-match state and operations
 */

import { useState, useCallback } from "react";
import {
	computeAutoMatches,
	type MatchSuggestion,
	type MatchResult,
} from "@/lib/matching";

interface UseAutoMatchProps {
	organizations: Array<{ id: string; name: string }>;
	entities: Array<{ value: string; label: string }>;
	existingMappings: Array<{ organization_id: string; entity_id: string }>;
}

export function useAutoMatch({
	organizations,
	entities,
	existingMappings,
}: UseAutoMatchProps) {
	const [suggestions, setSuggestions] = useState<
		Map<string, MatchSuggestion>
	>(new Map());
	const [matchStats, setMatchStats] = useState<MatchResult["stats"] | null>(
		null,
	);
	const [isMatching, setIsMatching] = useState(false);

	/**
	 * Run auto-match for all unmapped organizations
	 */
	const runAutoMatch = useCallback(() => {
		setIsMatching(true);

		try {
			// Get existing mapped org IDs and entity IDs
			const existingMappedOrgIds = new Set(
				existingMappings.map((m) => m.organization_id),
			);
			const existingMappedEntityIds = new Set(
				existingMappings.map((m) => m.entity_id),
			);

			const result = computeAutoMatches(
				organizations,
				entities,
				existingMappedOrgIds,
				existingMappedEntityIds,
			);

			// Convert suggestions array to Map for O(1) lookups
			const suggestionsMap = new Map<string, MatchSuggestion>();
			result.suggestions.forEach((suggestion) => {
				suggestionsMap.set(suggestion.organizationId, suggestion);
			});

			setSuggestions(suggestionsMap);
			setMatchStats(result.stats);
		} finally {
			setIsMatching(false);
		}
	}, [organizations, entities, existingMappings]);

	/**
	 * Accept a suggestion and return it
	 */
	const acceptSuggestion = useCallback(
		(orgId: string): MatchSuggestion | null => {
			const suggestion = suggestions.get(orgId);
			if (!suggestion) return null;

			// Remove from suggestions
			setSuggestions((prev) => {
				const newMap = new Map(prev);
				newMap.delete(orgId);
				return newMap;
			});

			return suggestion;
		},
		[suggestions],
	);

	/**
	 * Reject a suggestion without returning it
	 */
	const rejectSuggestion = useCallback((orgId: string) => {
		setSuggestions((prev) => {
			const newMap = new Map(prev);
			newMap.delete(orgId);
			return newMap;
		});
	}, []);

	/**
	 * Accept all suggestions and return them
	 */
	const acceptAll = useCallback((): MatchSuggestion[] => {
		const allSuggestions = Array.from(suggestions.values());
		setSuggestions(new Map());
		setMatchStats(null);
		return allSuggestions;
	}, [suggestions]);

	/**
	 * Clear all suggestions
	 */
	const clearSuggestions = useCallback(() => {
		setSuggestions(new Map());
		setMatchStats(null);
	}, []);

	return {
		suggestions,
		matchStats,
		isMatching,
		runAutoMatch,
		acceptSuggestion,
		rejectSuggestion,
		acceptAll,
		clearSuggestions,
	};
}
