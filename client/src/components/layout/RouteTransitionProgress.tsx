import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

type ProgressState = "idle" | "loading" | "finishing";

const FINISH_DELAY_MS = 220;

function shouldTrackClick(event: MouseEvent): boolean {
	if (
		event.defaultPrevented ||
		event.button !== 0 ||
		event.metaKey ||
		event.altKey ||
		event.ctrlKey ||
		event.shiftKey
	) {
		return false;
	}

	const target = event.target;
	if (!(target instanceof Element)) {
		return false;
	}

	const anchor = target.closest<HTMLAnchorElement>("a[href]");
	if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) {
		return false;
	}

	const targetUrl = new URL(anchor.href, window.location.href);
	if (targetUrl.origin !== window.location.origin) {
		return false;
	}

	const currentUrl = new URL(window.location.href);
	return (
		targetUrl.pathname !== currentUrl.pathname ||
		targetUrl.search !== currentUrl.search
	);
}

export function RouteTransitionProgress() {
	const location = useLocation();
	const routeKey = `${location.pathname}${location.search}${location.hash}`;
	const lastRouteKey = useRef(routeKey);
	const finishTimer = useRef<number | null>(null);
	const isTrackingNavigation = useRef(false);
	const [progressState, setProgressState] = useState<ProgressState>("idle");

	const clearFinishTimer = useCallback(() => {
		if (finishTimer.current === null) {
			return;
		}
		window.clearTimeout(finishTimer.current);
		finishTimer.current = null;
	}, []);

	const finish = useCallback(() => {
		if (!isTrackingNavigation.current) {
			return;
		}

		clearFinishTimer();
		setProgressState("finishing");
		finishTimer.current = window.setTimeout(() => {
			isTrackingNavigation.current = false;
			setProgressState("idle");
			finishTimer.current = null;
		}, FINISH_DELAY_MS);
	}, [clearFinishTimer]);

	const start = useCallback(() => {
		clearFinishTimer();
		isTrackingNavigation.current = true;
		setProgressState("loading");
	}, [clearFinishTimer]);

	useEffect(() => {
		const handleClick = (event: MouseEvent) => {
			if (shouldTrackClick(event)) {
				start();
			}
		};

		document.addEventListener("click", handleClick, true);
		return () => {
			document.removeEventListener("click", handleClick, true);
		};
	}, [start]);

	useEffect(() => {
		if (lastRouteKey.current === routeKey) {
			return;
		}

		lastRouteKey.current = routeKey;
		finish();
	}, [finish, routeKey]);

	useEffect(() => clearFinishTimer, [clearFinishTimer]);

	if (progressState === "idle") {
		return null;
	}

	return (
		<div
			role="progressbar"
			aria-label="Loading page"
			className="route-transition-progress-track pointer-events-none fixed inset-x-0 top-0 z-[100] h-0.5 overflow-hidden"
		>
			<div
				data-state={progressState}
				className="route-transition-progress-fill h-full w-full transition-transform duration-200 ease-out"
			/>
		</div>
	);
}
