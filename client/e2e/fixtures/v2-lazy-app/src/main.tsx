import { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";

import "./style.css";

export const sharedLabel = "Lazy chunk rendered";

interface Bootstrap {
	basename: string;
	baseUrl: string;
	token: string;
	orgScope: string | null;
	appId: string;
	theme: "light" | "dark";
	onLogout: () => void;
}

interface FixtureModule {
	mount: (mountEl: HTMLElement, bootstrap: Bootstrap) => () => void;
}

declare global {
	interface Window {
		__BIFROST_APP_MODULES__?: Map<string, FixtureModule>;
		__v2LazyFixture?: {
			entryExecutions: number;
			mounts: number;
			unmounts: number;
		};
	}
}

const counters = (window.__v2LazyFixture ??= {
	entryExecutions: 0,
	mounts: 0,
	unmounts: 0,
});
counters.entryExecutions += 1;

const LazyPage = lazy(() => import("./LazyPage"));

function FixtureApp({ bootstrap }: { bootstrap: Bootstrap }) {
	return (
		<main>
			<p data-testid="fixture-basename">{bootstrap.basename}</p>
			<Suspense fallback={<p>Loading lazy chunk…</p>}>
				<LazyPage />
			</Suspense>
		</main>
	);
}

export function mount(mountEl: HTMLElement, bootstrap: Bootstrap) {
	counters.mounts += 1;
	const root = createRoot(mountEl);
	root.render(<FixtureApp bootstrap={bootstrap} />);
	return () => {
		counters.unmounts += 1;
		root.unmount();
	};
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });

if (import.meta.env.DEV) {
	const mountEl = document.getElementById("root");
	if (!mountEl) throw new Error("Missing #root");
	mount(mountEl, {
		basename: "/",
		baseUrl: window.location.origin,
		token: "dev",
		orgScope: null,
		appId: "dev",
		theme: "light",
		onLogout: () => undefined,
	});
}
