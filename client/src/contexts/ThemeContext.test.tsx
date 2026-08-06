import { afterEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ThemeProvider, useTheme } from "./ThemeContext";

function ThemeProbe() {
	const { theme } = useTheme();
	return <div>{theme}</div>;
}

afterEach(() => {
	window.history.replaceState(null, "", "/");
	localStorage.clear();
	document.documentElement.classList.remove("dark");
});

describe("ThemeProvider embedded form override", () => {
	it("defaults embedded forms to light without changing the saved preference", async () => {
		localStorage.setItem("theme", "dark");
		window.history.replaceState(null, "", "/embedded/forms/public/key");
		render(
			<ThemeProvider>
				<ThemeProbe />
			</ThemeProvider>,
		);
		expect(screen.getByText("light")).toBeInTheDocument();
		await waitFor(() =>
			expect(document.documentElement).not.toHaveClass("dark"),
		);
		expect(localStorage.getItem("theme")).toBe("dark");
	});

	it("honors an explicit dark embed theme", async () => {
		localStorage.setItem("theme", "light");
		window.history.replaceState(
			null,
			"",
			"/embedded/forms/public/key?theme=dark",
		);
		render(
			<ThemeProvider>
				<ThemeProbe />
			</ThemeProvider>,
		);
		expect(screen.getByText("dark")).toBeInTheDocument();
		await waitFor(() =>
			expect(document.documentElement).toHaveClass("dark"),
		);
		expect(localStorage.getItem("theme")).toBe("light");
	});
});
