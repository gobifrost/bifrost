/**
 * Auto-matching utilities for integration entity mapping
 */

import Fuse from "fuse.js";

export interface MatchSuggestion {
	organizationId: string;
	organizationName: string;
	entityId: string;
	entityName: string;
	score: number; // 0-100
}

export interface MatchResult {
	suggestions: MatchSuggestion[];
	unmatchedOrgIds: string[];
	stats: {
		total: number;
		matched: number;
		highConfidence: number;
		lowConfidence: number;
	};
}

/**
 * Perform exact string match (case-insensitive)
 * @returns Matching entity or null
 */
export function exactMatch(
	orgName: string,
	entities: Array<{ value: string; label: string }>,
): { entityId: string; entityName: string } | null {
	const normalizedOrgName = orgName.trim().toLowerCase();

	for (const entity of entities) {
		const normalizedLabel = entity.label.trim().toLowerCase();
		if (normalizedLabel === normalizedOrgName) {
			return {
				entityId: entity.value,
				entityName: entity.label,
			};
		}
	}

	return null;
}

/**
 * Perform fuzzy string match using Fuse.js
 * @param threshold - Match threshold (0-1, lower is more strict). Default 0.3
 * @returns Matching entity with score or null
 */
export function fuzzyMatch(
	orgName: string,
	entities: Array<{ value: string; label: string }>,
	threshold: number = 0.3,
): { entityId: string; entityName: string; score: number } | null {
	if (entities.length === 0) {
		return null;
	}

	const fuse = new Fuse(entities, {
		keys: ["label"],
		threshold,
		includeScore: true,
	});

	const results = fuse.search(orgName);

	if (results.length === 0) {
		return null;
	}

	const bestMatch = results[0];
	if (!bestMatch.item || bestMatch.score === undefined) {
		return null;
	}

	// Convert Fuse score (0=perfect, 1=worst) to percentage (100=perfect, 0=worst)
	const scorePercentage = Math.round((1 - bestMatch.score) * 100);

	return {
		entityId: bestMatch.item.value,
		entityName: bestMatch.item.label,
		score: scorePercentage,
	};
}

/**
 * Compute auto-match suggestions for all unmapped organizations.
 * An exact (case-insensitive) name match scores 100; otherwise the closest
 * fuzzy match is suggested with its confidence, for the user to confirm.
 * @param orgs - List of organizations to match
 * @param entities - List of available entities from data provider
 * @param existingMappedOrgIds - Set of organization IDs that already have mappings
 * @param existingMappedEntityIds - Set of entity IDs that are already mapped (to avoid duplicates)
 * @returns Match results with suggestions and stats
 */
export function computeAutoMatches(
	orgs: Array<{ id: string; name: string }>,
	entities: Array<{ value: string; label: string }>,
	existingMappedOrgIds: Set<string>,
	existingMappedEntityIds: Set<string>,
): MatchResult {
	const suggestions: MatchSuggestion[] = [];
	const unmatchedOrgIds: string[] = [];
	const usedEntityIds = new Set(existingMappedEntityIds);

	// Only process organizations that don't have mappings yet
	const unmappedOrgs = orgs.filter(
		(org) => !existingMappedOrgIds.has(org.id),
	);

	for (const org of unmappedOrgs) {
		const matchResult: {
			entityId: string;
			entityName: string;
			score?: number;
		} | null =
			exactMatch(org.name, entities) ?? fuzzyMatch(org.name, entities);

		// If we found a match and the entity isn't already used
		if (matchResult && !usedEntityIds.has(matchResult.entityId)) {
			const score =
				matchResult.score !== undefined ? matchResult.score : 100; // Exact match = 100%

			suggestions.push({
				organizationId: org.id,
				organizationName: org.name,
				entityId: matchResult.entityId,
				entityName: matchResult.entityName,
				score,
			});

			// Mark this entity as used for subsequent iterations
			usedEntityIds.add(matchResult.entityId);
		} else {
			unmatchedOrgIds.push(org.id);
		}
	}

	// Calculate stats
	const highConfidence = suggestions.filter((s) => s.score >= 80).length;
	const lowConfidence = suggestions.filter((s) => s.score < 80).length;

	return {
		suggestions,
		unmatchedOrgIds,
		stats: {
			total: unmappedOrgs.length,
			matched: suggestions.length,
			highConfidence,
			lowConfidence,
		},
	};
}
