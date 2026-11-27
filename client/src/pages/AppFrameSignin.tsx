import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { PageLoader } from "@/components/PageLoader";
import { useAuth } from "@/contexts/AuthContext";
import { getCsrfToken } from "@/lib/api-client";
import { getFrameSessionId, isAppFrame, setFrameSession } from "@/lib/auth-token";

const POPUP_PATH = "/app-frame-signin";
const POPUP_CHALLENGE_KEY = "bifrost_frame_signin_challenge";
const CHALLENGE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const EXCHANGE_DELAY_MS = 2_000;
const EXCHANGE_TIMEOUT_MS = 5 * 60_000;

function encodeSecret(bytes: Uint8Array) {
	return btoa(String.fromCharCode(...bytes))
		.replaceAll("+", "-")
		.replaceAll("/", "_")
		.replace(/=+$/, "");
}

export function AppFrameGate({ children }: { children: ReactNode }) {
	const { isAuthenticated, isLoading } = useAuth();
	const [error, setError] = useState("");
	const [waiting, setWaiting] = useState(false);
	const attempt = useRef<AbortController | null>(null);

	useEffect(() => () => attempt.current?.abort(), []);

	useLayoutEffect(() => {
		if (!isAppFrame()) return;
		const url = new URL(location.href);
		const session = isAuthenticated ? getFrameSessionId() : null;
		if (session) url.searchParams.set("__bf_frame_session", session);
		else url.searchParams.delete("__bf_frame_session");
		if (url.href !== location.href) history.replaceState(history.state, "", url);
	}, [isAuthenticated]);

	const start = async () => {
		attempt.current?.abort();
		setError("");
		const popup = window.open(
			"about:blank",
			"_blank",
			"popup,width=520,height=680",
		);
		if (!popup) {
			setWaiting(false);
			setError("Allow popups for this site and try again.");
			return;
		}
		const controller = new AbortController();
		attempt.current = controller;
		setWaiting(true);
		try {
			const code = encodeSecret(
				crypto.getRandomValues(new Uint8Array(32)),
			);
			const digest = await crypto.subtle.digest(
				"SHA-256",
				new TextEncoder().encode(code),
			);
			popup.location.replace(
				`${location.origin}${POPUP_PATH}#${encodeSecret(new Uint8Array(digest))}`,
			);
			const deadline = Date.now() + EXCHANGE_TIMEOUT_MS;
			while (Date.now() < deadline && !controller.signal.aborted) {
				const response = await fetch(
					"/api/auth/app-frame-signin/exchange",
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ code }),
						signal: controller.signal,
					},
				);
				if (response.status === 200) {
					setFrameSession(await response.json());
					location.reload();
					return;
				}
				if (response.status !== 202) break;
				await new Promise((resolve) =>
					setTimeout(resolve, EXCHANGE_DELAY_MS),
				);
			}
			if (!controller.signal.aborted)
				setError("Sign-in could not be completed. Try again.");
		} catch {
			if (!controller.signal.aborted)
				setError("Sign-in could not be completed. Try again.");
		} finally {
			if (!controller.signal.aborted) setWaiting(false);
		}
	};

	if (!isAppFrame()) {
		return (
			<p className="p-6">
				Open this App from the site where it’s embedded.
			</p>
		);
	}
	if (isLoading) return <PageLoader message="Checking sign-in…" size="sm" />;
	if (isAuthenticated) return children;
	return (
		<main className="mx-auto flex max-w-sm flex-col items-start gap-3 p-6">
			<h1 className="text-xl font-semibold">Sign In to Bifrost</h1>
			<p className="text-sm text-muted-foreground">
				Sign in in a separate window to use this App here.
			</p>
			{waiting && (
				<p role="status" className="text-sm">
					Waiting for you to finish signing in.
				</p>
			)}
			{error && (
				<p role="alert" className="text-sm text-destructive">
					{error}
				</p>
			)}
			<Button onClick={() => void start()}>
				{waiting ? "Restart Sign-In" : "Sign In to Bifrost"}
			</Button>
		</main>
	);
}

function getPopupChallenge(): string | null {
	if (window.self !== window.top) return null;
	const requested = location.hash.slice(1);
	if (!requested) return sessionStorage.getItem(POPUP_CHALLENGE_KEY);
	const opener = window.opener as Window | null;
	if (
		!opener ||
		opener.location.origin !== location.origin ||
		!opener.location.pathname.startsWith("/embedded/apps/") ||
		opener.self === opener.top ||
		!CHALLENGE_PATTERN.test(requested)
	)
		return null;

	// Keep the public challenge in this top-level window across SSO. The
	// secret verifier stays in the iframe's separate storage partition.
	sessionStorage.setItem(POPUP_CHALLENGE_KEY, requested);
	history.replaceState(history.state, "", POPUP_PATH);
	return requested;
}

export function AppFrameSigninPopup() {
	const { isAuthenticated, isLoading } = useAuth();
	const navigate = useNavigate();
	const started = useRef(false);
	const [result, setResult] = useState<"pending" | "failed" | "completed">(
		"pending",
	);
	useEffect(() => {
		if (isLoading || started.current) return;
		started.current = true;
		void (async () => {
			try {
				const code = getPopupChallenge();
				if (!code) throw new Error("Missing App frame challenge");
				if (!isAuthenticated) {
					navigate("/login", {
						replace: true,
						state: { from: POPUP_PATH },
					});
					return;
				}
				const response = await fetch(
					"/api/auth/app-frame-signin/code",
					{
						method: "POST",
						headers: {
							"Content-Type": "application/json",
							"X-CSRF-Token": getCsrfToken() ?? "",
						},
						body: JSON.stringify({ code }),
					},
				);
				if (!response.ok)
					throw new Error("Could not approve frame sign-in");
				sessionStorage.removeItem(POPUP_CHALLENGE_KEY);
				setResult("completed");
				window.close();
			} catch {
				setResult("failed");
			}
		})();
	}, [isAuthenticated, isLoading, navigate]);

	if (result === "failed") {
		return (
			<p role="alert" className="p-6">
				Sign-in could not be completed. Close this window and try again
				from the embedded App.
			</p>
		);
	}
	if (result === "completed")
		return (
			<p role="status" className="p-6">
				You’re signed in. You can close this window.
			</p>
		);
	return <PageLoader message="Completing sign-in…" />;
}
