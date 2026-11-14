import { MarkdownManager } from "@tiptap/markdown";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";
import { canEditMarkdownVisually } from "./tiptap-markdown";

const manager = new MarkdownManager({ extensions: [StarterKit] });

describe("canEditMarkdownVisually", () => {
	it.each([
		"## Hello\n\n[Relative](/execute/form) `inline code`\n\n- Parent\n  - Child",
		"**Bold** and _italic_\n\n* First\n* Second\n",
		"3. Third\n4. Fourth\n\n> Quoted\n\n```sh\necho hello\n```",
		"",
	])("accepts supported structure after serialization: %s", (source) => {
		expect(
			canEditMarkdownVisually(
				manager,
				source,
				manager.serialize(manager.parse(source)),
			),
		).toBe(true);
	});

	it.each([
		"<!-- Keep this -->\n\nText",
		"| Key | Value |\n| --- | --- |\n| api | endpoint |",
		"![Diagram](/diagram.png)",
		"- [x] Done\n- [ ] To do",
		"<details><summary>Details</summary>Hidden</details>",
		"[unused]: /keep-this-definition\n\nText",
	])("protects unsupported content: %s", (source) => {
		expect(canEditMarkdownVisually(manager, source, "Text")).toBe(false);
	});

	it.each([
		["[Form](/execute/one)", "[Form](/execute/two)"],
		["## Heading", "### Heading"],
		["`code`", "code"],
		["- Parent\n  - Child", "- Parent\n- Child"],
	])("detects changed structure or metadata: %s", (source, serialized) => {
		expect(canEditMarkdownVisually(manager, source, serialized)).toBe(
			false,
		);
	});
});
