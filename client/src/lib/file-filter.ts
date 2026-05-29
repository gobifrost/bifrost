/**
 * File filtering for workspace operations.
 *
 * Provides consistent filtering of system/metadata files.
 * This mirrors the backend file_filter.py - keep them in sync!
 */

// Directories to exclude from workspace operations
const EXCLUDED_DIRECTORIES = new Set([
	".git",
	"__pycache__",
	".vscode",
	".idea",
	"node_modules",
	".venv",
	"venv",
	"env",
	".pytest_cache",
	".mypy_cache",
	".ruff_cache",
	"htmlcov",
	".tox",
	".nox",
	".eggs",
	".ipynb_checkpoints",
]);

// Files to exclude from workspace operations
const EXCLUDED_FILES = new Set([
	".DS_Store",
	"Thumbs.db",
	"desktop.ini",
	"bifrost.pyi",
	".coverage",
	".python-version",
	".env",
	".env.local",
]);

// File extensions to exclude
const EXCLUDED_EXTENSIONS = new Set([".pyc", ".pyo", ".pyd", ".so", ".dylib"]);

// Prefixes that indicate hidden/metadata files
const EXCLUDED_PREFIXES = ["._"]; // AppleDouble metadata files

/**
 * Check if a path should be excluded from workspace operations.
 *
 * Works with file paths (e.g., "folder/subfolder/file.txt").
 * Checks each component of the path against exclusion rules.
 *
 * @param path - File path to check (relative)
 * @returns True if the path should be excluded, False otherwise
 */
export function isExcludedPath(path: string): boolean {
	// Split path into components
	const parts = path.split("/").filter((p) => p.length > 0);

	for (const part of parts) {
		// Check hidden prefixes (AppleDouble files)
		for (const prefix of EXCLUDED_PREFIXES) {
			if (part.startsWith(prefix)) {
				return true;
			}
		}

		// Check exact matches (files and directories)
		if (EXCLUDED_FILES.has(part) || EXCLUDED_DIRECTORIES.has(part)) {
			return true;
		}

		// Check for egg-info directories (pattern match)
		if (part.endsWith(".egg-info")) {
			return true;
		}
	}

	// Check file extension of the final component
	const lastPart = parts[parts.length - 1];
	if (lastPart) {
		const dotIndex = lastPart.lastIndexOf(".");
		if (dotIndex > 0) {
			const extension = lastPart.substring(dotIndex).toLowerCase();
			if (EXCLUDED_EXTENSIONS.has(extension)) {
				return true;
			}
		}
	}

	return false;
}

/**
 * Check if a path is allowed for workspace operations.
 *
 * Inverse of isExcludedPath() for convenience.
 *
 * @param path - File path to check
 * @returns True if the path is allowed, False if it should be excluded
 */
export function isAllowedPath(path: string): boolean {
	return !isExcludedPath(path);
}
