/**
 * Component tests for ConfigFieldInput.
 *
 * Covers each field-type branch (secret / bool / int / json / string), the
 * required-marker, and the Reset affordance for overridden values. Secrets
 * deserve special attention — they should never display the stored value and
 * must surface an override-set hint.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";
import { ConfigFieldInput } from "./ConfigFieldInput";
import type { ConfigSchemaItem } from "@/services/integrations";

function makeField(
	overrides: Partial<ConfigSchemaItem> = {},
): ConfigSchemaItem {
	return {
		key: "example_key",
		type: "string",
		required: false,
		...overrides,
	} as ConfigSchemaItem;
}

describe("ConfigFieldInput — string", () => {
	it("emits the typed string via onChange", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "string" })}
				value=""
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByRole("textbox"), {
			target: { value: "hello" },
		});

		expect(onChange).toHaveBeenLastCalledWith("hello");
	});

	it("emits undefined when the string field is cleared", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "string" })}
				value="hi"
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByRole("textbox"), {
			target: { value: "" },
		});

		expect(onChange).toHaveBeenLastCalledWith(undefined);
	});

	it("marks required fields with an asterisk next to the label", () => {
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "string", required: true })}
				value=""
				onChange={() => {}}
			/>,
		);
		// The label contains both the key and an asterisk child
		expect(screen.getByText("*")).toBeInTheDocument();
	});
});

describe("ConfigFieldInput — int", () => {
	it("parses typed numbers to integers", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "int" })}
				value={0}
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByRole("spinbutton"), {
			target: { value: "42" },
		});

		expect(onChange).toHaveBeenLastCalledWith(42);
	});
});

describe("ConfigFieldInput — bool", () => {
	it("toggles via the switch", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "bool" })}
				value={false}
				onChange={onChange}
			/>,
		);

		await user.click(screen.getByRole("switch"));

		expect(onChange).toHaveBeenLastCalledWith(true);
	});
});

describe("ConfigFieldInput — json", () => {
	it("parses valid JSON into an object", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "json" })}
				value={undefined}
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByRole("textbox"), {
			target: { value: '{"a":1}' },
		});

		expect(onChange).toHaveBeenLastCalledWith({ a: 1 });
	});

	it("keeps invalid JSON as a string instead of throwing", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "json" })}
				value={undefined}
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByRole("textbox"), {
			target: { value: "{oops" },
		});

		expect(onChange).toHaveBeenLastCalledWith("{oops");
	});
});

describe("ConfigFieldInput — secret", () => {
	it("renders secrets behind a password input and surfaces a 'Secret configured' badge when a value is stored", () => {
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "secret" })}
				value="super-secret"
				onChange={() => {}}
			/>,
		);
		// Password inputs are not exposed as role=textbox; grab by type
		const input = document.querySelector(
			"input[type='password']",
		) as HTMLInputElement;
		expect(input).toBeTruthy();
		expect(input.type).toBe("password");
		// Badge indicating a secret is configured
		expect(screen.getByText(/secret configured/i)).toBeInTheDocument();
	});

	it("emits the typed replacement value via onChange", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "secret" })}
				value=""
				onChange={onChange}
			/>,
		);
		const input = document.querySelector(
			"input[type='password']",
		) as HTMLInputElement;
		fireEvent.change(input, { target: { value: "new-secret" } });
		expect(onChange).toHaveBeenLastCalledWith("new-secret");
	});

	it("shows override-set hint + placeholder when hasOverride is true and value empty", () => {
		renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "secret" })}
				value=""
				hasOverride
				onChange={() => {}}
			/>,
		);
		expect(screen.getByText(/an override is set/i)).toBeInTheDocument();
	});
});

describe("ConfigFieldInput — reset affordance", () => {
	it("shows the Reset button only when onReset + hasOverride are both set", async () => {
		const onReset = vi.fn();
		const { user, rerender } = renderWithProviders(
			<ConfigFieldInput
				field={makeField({ type: "string" })}
				value="x"
				onChange={() => {}}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /reset/i }),
		).not.toBeInTheDocument();

		rerender(
			<ConfigFieldInput
				field={makeField({ type: "string" })}
				value="x"
				hasOverride
				onReset={onReset}
				onChange={() => {}}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /reset/i }));
		expect(onReset).toHaveBeenCalledTimes(1);
	});
});

it("uses static options for organization configuration and clears to inherited state", async () => {
	const onChange = vi.fn();
	const { user } = renderWithProviders(
		<ConfigFieldInput
			field={makeField({ options: ["us", "eu"] })}
			value="us"
			onChange={onChange}
		/>,
	);
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "eu" }));
	expect(onChange).toHaveBeenLastCalledWith("eu");
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "us" }));
	expect(onChange).toHaveBeenLastCalledWith(undefined);
});
