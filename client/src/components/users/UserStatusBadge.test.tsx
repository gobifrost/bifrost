import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UserStatusBadge } from "./UserStatusBadge";

describe("UserStatusBadge", () => {
	it("renders Active for active status", () => {
		render(<UserStatusBadge status="active" />);
		expect(screen.getByText(/^active$/i)).toBeInTheDocument();
	});

	it("renders Pending invite for pending status", () => {
		render(<UserStatusBadge status="pending" />);
		expect(screen.getByText(/pending invite/i)).toBeInTheDocument();
	});

	it("renders Invite expired for expired status", () => {
		render(<UserStatusBadge status="expired" />);
		expect(screen.getByText(/invite expired/i)).toBeInTheDocument();
	});

	it("renders Not invited for never_invited status", () => {
		render(<UserStatusBadge status="never_invited" />);
		expect(screen.getByText(/not invited/i)).toBeInTheDocument();
	});

	it("falls back to Active for an unknown status string", () => {
		render(<UserStatusBadge status="something-new" />);
		expect(screen.getByText(/^active$/i)).toBeInTheDocument();
	});
});
