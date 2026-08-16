import type { ReactNode } from "react";

export function RouteReadyReveal({ children }: { children: ReactNode }) {
	return <div className="route-ready-reveal h-full w-full">{children}</div>;
}
