import { describe, expect, it } from "vitest";

import {
	formRuntimeQueryParams,
	parseFormEmbedPresentation,
} from "./form-embed-presentation";

describe("parseFormEmbedPresentation", () => {
	it("only applies to embedded form documents", () => {
		expect(
			parseFormEmbedPresentation("/forms/one", "?theme=dark"),
		).toBeNull();
	});

	it("uses a predictable light canvas by default", () => {
		expect(
			parseFormEmbedPresentation("/embedded/forms/public/key", ""),
		).toEqual({
			theme: "light",
			showHeader: true,
			transparentBackground: false,
		});
	});

	it("parses supported appearance options and ignores invalid theme values", () => {
		expect(
			parseFormEmbedPresentation(
				"/embedded/forms/public/key",
				"?theme=system&header=false&background=transparent",
			),
		).toEqual({
			theme: "system",
			showHeader: false,
			transparentBackground: true,
		});
		expect(
			parseFormEmbedPresentation(
				"/embedded/forms/public/key",
				"?theme=purple",
			)?.theme,
		).toBe("light");
	});
});

describe("formRuntimeQueryParams", () => {
	it("keeps presentation controls out of embedded form context", () => {
		const search = new URLSearchParams(
			"theme=dark&header=false&background=transparent&ticket_id=42",
		);
		expect(formRuntimeQueryParams(search, true)).toEqual({
			ticket_id: "42",
		});
		expect(formRuntimeQueryParams(search, false)).toEqual({
			theme: "dark",
			header: "false",
			background: "transparent",
			ticket_id: "42",
		});
	});
});
