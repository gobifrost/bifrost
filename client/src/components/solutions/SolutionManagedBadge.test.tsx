import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { SolutionManagedBadge } from "./SolutionManagedBadge";

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

function renderBadge(solutionId: string | null | undefined) {
	return render(
		<MemoryRouter>
			<SolutionManagedBadge solutionId={solutionId} />
		</MemoryRouter>,
	);
}

describe("SolutionManagedBadge", () => {
	it("links to the owning solution for admins", () => {
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		renderBadge("abc-123");
		const link = screen.getByRole("link", { name: /managed/i });
		expect(link).toHaveAttribute("href", "/solutions/abc-123");
		expect(link).toHaveTextContent(/^$/);
		expect(link).toHaveAccessibleName("Managed by a Solution");
	});

	it("renders nothing for non-admins", () => {
		mockUseAuth.mockReturnValue({ isPlatformAdmin: false });
		const { container } = renderBadge("abc-123");
		expect(container.firstChild).toBeNull();
	});

	it("renders nothing when solutionId is missing", () => {
		mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
		const { container } = renderBadge(null);
		expect(container.firstChild).toBeNull();
	});
});
