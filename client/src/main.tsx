import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/sonner";
import "./index.css";
import App from "./App.tsx";
import { queryClient } from "./lib/queryClient";
import { ThemeProvider } from "./contexts/ThemeContext";
import { OrgScopeQueryInvalidator } from "./components/OrgScopeQueryInvalidator";
import { configureMonaco } from "./lib/monaco-setup";
import { initReactShim } from "./lib/esm-react-shim";
import { handleVitePreloadError } from "@/lib/preload-error-handler";

// After a deploy, hashed JS chunks vanish and dynamic imports for old chunk
// names start 404'ing. Vite emits `vite:preloadError` for those — reload once
// to pull the fresh bundle, with a sessionStorage guard so a chronically
// broken deploy can't trap the user in a reload loop.
window.addEventListener("vite:preloadError", handleVitePreloadError);

// Expose platform React via import map so esm.sh packages use the same instance
initReactShim();

// Configure Monaco editor before React renders (sets up CDN paths for workers)
configureMonaco();

createRoot(document.getElementById("root")!).render(
	<StrictMode>
		<ThemeProvider>
			<QueryClientProvider client={queryClient}>
				<OrgScopeQueryInvalidator />
				<App />
				<Toaster />
			</QueryClientProvider>
		</ThemeProvider>
	</StrictMode>,
);
