import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FormConfirmation, FormConfirmationMarkdown } from "./FormConfirmation";

describe("FormConfirmation", () => {
	beforeEach(() => {
		vi.stubGlobal(
			"ResizeObserver",
			class {
				observe() {}
				disconnect() {}
			},
		);
		vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
	});

	it("focuses and renders safe Markdown without raw HTML", () => {
		render(
			<FormConfirmation
				formId="form-1"
				markdown={
					'## Thank you\n\n<script>alert("x")</script>\n\n![Receipt](https://images.example/ok.png)'
				}
			/>,
		);

		const status = screen.getByRole("status");
		expect(status).toHaveFocus();
		expect(
			screen.getByRole("heading", { name: "Thank you" }),
		).toBeVisible();
		expect(status.querySelector("script")).toBeNull();
		expect(screen.getByRole("img", { name: "Receipt" })).toHaveAttribute(
			"referrerpolicy",
			"no-referrer",
		);
	});

	it("drops non-HTTPS cross-origin images", () => {
		render(
			<FormConfirmation
				formId="form-1"
				markdown="![Unsafe](http://images.example/unsafe.png)"
			/>,
		);

		expect(screen.queryByRole("img", { name: "Unsafe" })).toBeNull();
	});

	it("uses the shared rendered-Markdown typography surface", () => {
		render(
			<FormConfirmationMarkdown
				markdown={"## Heading\n\n- First\n- Second"}
			/>,
		);

		const heading = screen.getByRole("heading", { name: "Heading" });
		expect(heading.closest(".markdown-content")).toBeInTheDocument();
		expect(screen.getByRole("list")).toHaveTextContent("First Second");
	});
});
