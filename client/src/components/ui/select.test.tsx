import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "./select";

describe("SelectTrigger", () => {
	it("fills form width by default and permits an explicit compact width", () => {
		const { rerender } = renderWithProviders(
			<Select defaultValue="private">
				<SelectTrigger aria-label="Access level">
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="private">Private</SelectItem>
				</SelectContent>
			</Select>,
		);

		expect(screen.getByLabelText("Access level")).toHaveClass("w-full");

		rerender(
			<Select defaultValue="private">
				<SelectTrigger
					aria-label="Compact access level"
					className="w-fit"
				>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="private">Private</SelectItem>
				</SelectContent>
			</Select>,
		);

		expect(screen.getByLabelText("Compact access level")).toHaveClass(
			"w-fit",
		);
		expect(screen.getByLabelText("Compact access level")).not.toHaveClass(
			"w-full",
		);
	});
});
