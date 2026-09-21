import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { agentPlatform } from "@/services/agentPlatform";
import { PlatformError } from "./PlatformEvidence";
import type { components } from "@/lib/v1";
export function SuiteEditor({
	suite,
	onSaved,
}: {
	suite: components["schemas"]["EvaluationSuitePublic"];
	onSaved: () => void;
}) {
	const [name, setName] = useState(suite.name);
	const [description, setDescription] = useState(suite.description ?? "");
	const save = useMutation({
		mutationFn: () =>
			agentPlatform.updateSuite(suite.id, {
				name,
				description,
				expected_version: suite.version,
			}),
		onSuccess: onSaved,
	});
	return (
		<details>
			<summary className="w-fit cursor-pointer text-sm font-medium">
				Suite settings
			</summary>
			<form
				className="mt-3 space-y-3"
				onSubmit={(event) => {
					event.preventDefault();
					save.mutate();
				}}
			>
				<Label htmlFor="edit-suite-name">Suite name</Label>
				<Input
					id="edit-suite-name"
					required
					value={name}
					onChange={(event) => setName(event.target.value)}
				/>
				<Label htmlFor="edit-suite-description">
					Suite description
				</Label>
				<Input
					id="edit-suite-description"
					value={description}
					onChange={(event) => setDescription(event.target.value)}
				/>
				<PlatformError error={save.error} />
				<Button disabled={save.isPending}>
					{save.isPending ? "Saving…" : "Save suite details"}
				</Button>
				{save.isSuccess && (
					<p role="status" className="text-sm">
						Suite details saved.
					</p>
				)}
			</form>
		</details>
	);
}
