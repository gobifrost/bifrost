import { useState } from "react";
import { $api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function AppEmbeddingSettings() {
	const settings = $api.useQuery("get", "/auth/app-frame-signin/settings");
	const save = $api.useMutation("put", "/auth/app-frame-signin/settings");
	const [draft, setDraft] = useState<string[] | null>(null);
	const [input, setInput] = useState("");
	const [notice, setNotice] = useState("");
	const [error, setError] = useState("");
	const origins = draft ?? settings.data?.allowed_origins ?? [];

	function addOrigin(event: React.FormEvent) {
		event.preventDefault();
		setNotice("");
		setError("");
		const value = input.trim();
		try {
			const url = new URL(value);
			if (
				!/^https?:\/\/[^/?#@*]+$/i.test(value) ||
				(url.protocol !== "https:" &&
					!["localhost", "127.0.0.1"].includes(url.hostname))
			) {
				throw new Error("Expected HTTPS origin");
			}
			setDraft([...new Set([...origins, url.origin])].sort());
			setInput("");
		} catch {
			setError(
				"Enter an HTTPS origin without a path, such as https://yourcompany.halopsa.com.",
			);
		}
	}

	async function saveSettings() {
		setError("");
		setNotice("");
		try {
			await save.mutateAsync({ body: { allowed_origins: origins } });
			await settings.refetch();
			setDraft(null);
			setNotice("Settings saved.");
		} catch {
			setError("Could not save the allowed origins. Try again.");
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle>App Embedding</CardTitle>
				<CardDescription>
					Choose which sites can host Bifrost Apps with user sign-in.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{settings.isPending && <p role="status">Loading settings…</p>}
				{settings.isError && (
					<div className="space-y-2">
						<p role="alert">Could not load the allowed origins.</p>
						<Button
							variant="outline"
							onClick={() => void settings.refetch()}
						>
							Try Again
						</Button>
					</div>
				)}
				{settings.data && (
					<>
						<form onSubmit={addOrigin} className="space-y-2">
							<Label htmlFor="app-frame-origin">
								Allowed Embed Origins
							</Label>
							<p
								id="app-frame-origin-help"
								className="text-sm text-muted-foreground"
							>
								Frame sign-in is disabled when this list is
								empty.
							</p>
							<div className="flex items-start gap-2">
								<Input
									id="app-frame-origin"
									value={input}
									onChange={(event) =>
										setInput(event.target.value)
									}
									placeholder="https://yourcompany.halopsa.com"
									aria-describedby="app-frame-origin-help"
									disabled={save.isPending}
									required
								/>
								<Button
									variant="outline"
									type="submit"
									disabled={save.isPending}
								>
									Add Origin
								</Button>
							</div>
						</form>
						{origins.length > 0 && (
							<ul className="divide-y rounded-md border">
								{origins.map((origin) => (
									<li
										key={origin}
										className="flex items-center justify-between gap-2 px-3 py-1"
									>
										<code className="break-all text-sm">
											{origin}
										</code>
										<Button
											variant="ghost"
											size="sm"
											aria-label={`Remove ${origin}`}
											disabled={save.isPending}
											onClick={() => {
												setNotice("");
												setDraft(
													origins.filter(
														(value) =>
															value !== origin,
													),
												);
											}}
										>
											Remove
										</Button>
									</li>
								))}
							</ul>
						)}
						<p className="text-sm text-muted-foreground">
							Use <code>/embedded/apps/&lt;app-slug&gt;</code> as
							the iframe path on an allowed site.
						</p>
						<div className="flex justify-end">
							<Button
								disabled={!draft || save.isPending}
								onClick={() => void saveSettings()}
							>
								Save Changes
							</Button>
						</div>
					</>
				)}
				{notice && (
					<p role="status" className="text-sm">
						{notice}
					</p>
				)}
				{error && (
					<p role="alert" className="text-sm text-destructive">
						{error}
					</p>
				)}
			</CardContent>
		</Card>
	);
}
