/**
 * Theme Context Provider
 * Manages dark/light mode with localStorage persistence
 */

import {
	createContext,
	useContext,
	useState,
	useEffect,
	ReactNode,
} from "react";
import { parseFormEmbedPresentation } from "@/lib/form-embed-presentation";

type Theme = "dark" | "light";

interface ThemeContextType {
	theme: Theme;
	toggleTheme: () => void;
	setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

interface ThemeProviderProps {
	children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
	const [embedPresentation] = useState(() =>
		parseFormEmbedPresentation(
			window.location.pathname,
			window.location.search,
		),
	);
	const [systemTheme, setSystemTheme] = useState<Theme>(() =>
		window.matchMedia?.("(prefers-color-scheme: dark)").matches
			? "dark"
			: "light",
	);

	// Use lazy initializer to read from localStorage on first render
	const [theme, setThemeState] = useState<Theme>(() => {
		const storedTheme = localStorage.getItem("theme") as Theme | null;
		if (storedTheme) {
			return storedTheme;
		}
		return "dark";
	});
	const activeTheme =
		embedPresentation?.theme === "system"
			? systemTheme
			: embedPresentation?.theme || theme;

	useEffect(() => {
		if (embedPresentation?.theme !== "system" || !window.matchMedia) return;
		const media = window.matchMedia("(prefers-color-scheme: dark)");
		const updateSystemTheme = () =>
			setSystemTheme(media.matches ? "dark" : "light");
		updateSystemTheme();
		media.addEventListener("change", updateSystemTheme);
		return () => media.removeEventListener("change", updateSystemTheme);
	}, [embedPresentation]);

	useEffect(() => {
		// Apply theme to document
		const root = document.documentElement;
		if (activeTheme === "dark") {
			root.classList.add("dark");
		} else {
			root.classList.remove("dark");
		}
	}, [activeTheme]);

	const setTheme = (newTheme: Theme, skipTransition = false) => {
		if (embedPresentation) return;
		if (skipTransition || !document.startViewTransition) {
			// No animation support or explicitly skipped
			setThemeState(newTheme);
			localStorage.setItem("theme", newTheme);
			return;
		}

		// Use View Transitions API for smooth animation
		document.startViewTransition(() => {
			setThemeState(newTheme);
			localStorage.setItem("theme", newTheme);
		});
	};

	const toggleTheme = () => {
		const newTheme = activeTheme === "dark" ? "light" : "dark";
		setTheme(newTheme);
	};

	return (
		<ThemeContext.Provider
			value={{
				theme: activeTheme,
				toggleTheme,
				setTheme,
			}}
		>
			{children}
		</ThemeContext.Provider>
	);
}

export function useTheme() {
	const context = useContext(ThemeContext);
	if (context === undefined) {
		throw new Error("useTheme must be used within a ThemeProvider");
	}
	return context;
}
