import { describe, expect, it } from "vitest";
import { isAllowedPath, isExcludedPath } from "./file-filter";

describe("file-filter", () => {
	it("allows .bifrost paths", () => {
		expect(isExcludedPath(".bifrost")).toBe(false);
		expect(isExcludedPath(".bifrost/workflows.yaml")).toBe(false);
		expect(isAllowedPath(".bifrost/workflows.yaml")).toBe(true);
	});

	it("still excludes tool and cache paths", () => {
		expect(isExcludedPath("__pycache__/module.pyc")).toBe(true);
		expect(isExcludedPath(".venv/bin/python")).toBe(true);
		expect(isExcludedPath("workflows/job.py")).toBe(false);
	});
});
