import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ListPagination } from "./ListPagination";

describe("ListPagination", () => {
	it("reports the visible range and navigates by offset", async () => {
		const user = userEvent.setup();
		const onPageChange = vi.fn();
		render(
			<ListPagination
				offset={25}
				limit={25}
				total={72}
				onPageChange={onPageChange}
			/>,
		);

		expect(screen.getByText("26–50 of 72")).toBeInTheDocument();
		expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
		await user.click(screen.getByRole("link", { name: /previous/i }));
		await user.click(screen.getByRole("link", { name: /next/i }));
		expect(onPageChange).toHaveBeenNthCalledWith(1, 0);
		expect(onPageChange).toHaveBeenNthCalledWith(2, 50);
	});

	it("disables navigation while fetching", () => {
		render(
			<ListPagination
				offset={0}
				limit={25}
				total={50}
				isFetching
				onPageChange={vi.fn()}
			/>,
		);

		expect(screen.getByLabelText("Loading page")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /previous/i })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
		expect(screen.getByRole("link", { name: /next/i })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
	});
});
