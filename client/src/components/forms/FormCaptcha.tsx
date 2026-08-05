import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import {
	solveChallenge,
	type Challenge,
	type DeriveKeyFunction,
} from "altcha/lib";
import { createSHA256, pbkdf2 } from "hash-wasm";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { authFetch } from "@/lib/api-client";

interface FormCaptchaProps {
	formId: string;
	onPayloadChange: (payload: string | null) => void;
}

type VerificationState =
	"loading" | "ready" | "verifying" | "verified" | "error";

export function FormCaptcha({ formId, onPayloadChange }: FormCaptchaProps) {
	const solveControllerRef = useRef<AbortController | null>(null);
	const [attempt, setAttempt] = useState(0);
	const [challenge, setChallenge] = useState<Challenge | null>(null);
	const [state, setState] = useState<VerificationState>("loading");
	const [error, setError] = useState<string | null>(null);

	const retry = useCallback(() => {
		solveControllerRef.current?.abort();
		onPayloadChange(null);
		setState("loading");
		setError(null);
		setChallenge(null);
		setAttempt((current) => current + 1);
	}, [onPayloadChange]);

	useEffect(() => {
		const requestController = new AbortController();
		onPayloadChange(null);

		void (async () => {
			try {
				const response = await authFetch(
					`/api/forms/${formId}/captcha/challenge`,
					{ method: "POST", signal: requestController.signal },
				);
				if (!response.ok) throw new Error("Challenge request failed");
				setChallenge((await response.json()) as Challenge);
				setState("ready");
			} catch (requestError) {
				if ((requestError as Error).name !== "AbortError") {
					setState("error");
					setError("Verification could not be loaded.");
				}
			}
		})();

		return () => requestController.abort();
	}, [attempt, formId, onPayloadChange]);

	useEffect(
		() => () => {
			solveControllerRef.current?.abort();
		},
		[],
	);

	const verify = async () => {
		if (!challenge || state !== "ready") return;
		const controller = new AbortController();
		const hashFunction = createSHA256();
		const deriveKey: DeriveKeyFunction = async (
			parameters,
			salt,
			password,
		) => {
			if (parameters.algorithm !== "PBKDF2/SHA-256") {
				throw new Error("Unsupported verification algorithm");
			}
			return {
				derivedKey: await pbkdf2({
					password,
					salt,
					iterations: parameters.cost,
					hashLength: parameters.keyLength ?? 32,
					hashFunction,
					outputType: "binary",
				}),
			};
		};
		solveControllerRef.current = controller;
		setState("verifying");
		setError(null);
		onPayloadChange(null);
		try {
			const solution = await solveChallenge({
				challenge,
				controller,
				deriveKey,
				timeout: 90_000,
			});
			if (!solution) throw new Error("Verification timed out");
			const payload = globalThis.btoa(
				JSON.stringify({ challenge, solution }),
			);
			setState("verified");
			onPayloadChange(payload);
		} catch (verificationError) {
			if ((verificationError as Error).name !== "AbortError") {
				setState("error");
				setError("Verification failed. Try again.");
			}
		}
	};

	return (
		<div
			className="rounded-xl border bg-muted/20 p-3"
			aria-busy={state === "loading" || state === "verifying"}
		>
			<div className="flex min-h-7 items-center gap-3">
				{state === "loading" || state === "verifying" ? (
					<Loader2 className="h-5 w-5 shrink-0 animate-spin text-muted-foreground" />
				) : state === "verified" ? (
					<CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
				) : (
					<Checkbox
						id={`form-verification-${formId}`}
						checked={false}
						disabled={state !== "ready"}
						onCheckedChange={(checked) => {
							if (checked) void verify();
						}}
						aria-label="I'm not a robot"
					/>
				)}
				<div className="min-w-0 flex-1">
					<Label
						htmlFor={`form-verification-${formId}`}
						className="font-medium"
					>
						{state === "loading"
							? "Loading verification…"
							: state === "verifying"
								? "Verifying…"
								: state === "verified"
									? "Verified"
									: "I'm not a robot"}
					</Label>
					<p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
						<ShieldCheck className="h-3 w-3" /> Private, self-hosted
						spam protection
					</p>
				</div>
			</div>
			{error ? (
				<div className="mt-2 flex items-center justify-between gap-3 border-t pt-2">
					<p className="text-sm text-destructive" role="alert">
						{error}
					</p>
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={retry}
					>
						Try again
					</Button>
				</div>
			) : null}
		</div>
	);
}
