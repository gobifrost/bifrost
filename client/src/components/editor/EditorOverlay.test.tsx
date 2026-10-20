import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const state = { isOpen: true, layoutMode: "full" };
const canAt = vi.fn();
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAt }),
}));
vi.mock("@/stores/editorStore", () => ({
	useEditorStore: Object.assign(
		(select: (value: typeof state) => unknown) => select(state),
		{ getState: () => state },
	),
}));
vi.mock("./EditorLayout", () => ({
	EditorLayout: () => <div>Repository editor</div>,
}));
vi.mock("@/components/window-management", () => ({
	WindowOverlay: ({ children }: { children: React.ReactNode }) => children,
}));

import { EditorOverlay } from "./EditorOverlay";

beforeEach(() => {
	state.isOpen = true;
	state.layoutMode = "full";
	canAt.mockReset();
});

it("opens for a named repository writer", () => {
	canAt.mockReturnValue(true);
	render(<EditorOverlay />);
	expect(screen.getByText("Repository editor")).toBeInTheDocument();
	expect(canAt).toHaveBeenCalledWith("repository.readwrite", {
		kind: "global",
	});
});

it("keeps the editor closed without the Global repository grant", () => {
	canAt.mockReturnValue(false);
	render(<EditorOverlay />);
	expect(screen.queryByText("Repository editor")).not.toBeInTheDocument();
});
