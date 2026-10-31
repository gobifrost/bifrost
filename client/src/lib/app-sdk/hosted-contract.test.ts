import { describe, expect, it } from "vitest";

import * as binding from "./hosted-binding";
import * as source from "./index.v2";

describe("hosted SDK binding contract", () => {
	it("forwards every installable SDK value export without adding a second public runtime", () => {
		expect(Object.keys(binding).sort()).toEqual(Object.keys(source).sort());
	});

	it("keeps the imperative tables and files method names", () => {
		expect(Object.keys(binding.tables).sort()).toEqual(Object.keys(source.tables).sort());
		expect(Object.keys(binding.files).sort()).toEqual(Object.keys(source.files).sort());
	});
});
