/**
 * Tests for UserEmailCell — truncating email cell with a hover copy button.
 *
 * Following the codebase pattern (see PolicyReferencePanel.test.tsx): jsdom
 * omits navigator.clipboard, so we don't assert the actual writeText call —
 * we verify the button exists, has the right label, and that clicks don't
 * bubble (which would otherwise open the row's edit dialog).
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent } from "@testing-library/react";

import { renderWithProviders, screen } from "@/test-utils";
import { UserEmailCell } from "./UserEmailCell";

describe("UserEmailCell", () => {
	it("renders the email text", () => {
		renderWithProviders(<UserEmailCell email="alice@example.com" />);
		expect(screen.getByText("alice@example.com")).toBeInTheDocument();
	});

	it("exposes a copy button with an accessible name including the email", () => {
		renderWithProviders(<UserEmailCell email="alice@example.com" />);
		expect(
			screen.getByRole("button", { name: /copy alice@example\.com/i }),
		).toBeInTheDocument();
	});

	it("does not bubble copy clicks up to the row (would otherwise open edit dialog)", () => {
		const rowClick = vi.fn();
		renderWithProviders(
			<div onClick={rowClick}>
				<UserEmailCell email="alice@example.com" />
			</div>,
		);

		fireEvent.click(
			screen.getByRole("button", { name: /copy alice@example\.com/i }),
		);

		expect(rowClick).not.toHaveBeenCalled();
	});
});
