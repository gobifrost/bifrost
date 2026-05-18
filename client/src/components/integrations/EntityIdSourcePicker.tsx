import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/v1";

export type Candidate = components["schemas"]["EntityIdPickerCandidate"];

interface EntityIdSourcePickerProps {
	candidates: Candidate[];
	onSelect: (candidate: Candidate) => void;
	onSkip: () => void;
	isPending: boolean;
}

export function EntityIdSourcePicker({
	candidates,
	onSelect,
	onSkip,
	isPending,
}: EntityIdSourcePickerProps) {
	const [selectedKey, setSelectedKey] = useState<string | null>(null);

	const keyId = (c: Candidate) => `${c.type}:${c.key}`;
	const selected = candidates.find((c) => keyId(c) === selectedKey) ?? null;

	return (
		<div className="space-y-4">
			<div>
				<h3 className="text-lg font-semibold">
					Set up entity ID auto-capture
				</h3>
				<p className="text-sm text-muted-foreground mt-1">
					Pick the field that uniquely identifies the tenant or account you
					just authorized. Future connections will auto-fill this
					mapping's entity ID from the same field.
				</p>
			</div>

			<div className="max-h-80 overflow-y-auto rounded-md border">
				<table className="w-full text-sm">
					<thead className="border-b bg-muted/50 text-left">
						<tr>
							<th className="p-2 w-8"></th>
							<th className="p-2">Source</th>
							<th className="p-2">Field</th>
							<th className="p-2">Value</th>
						</tr>
					</thead>
					<tbody>
						{candidates.map((c) => (
							<tr
								key={keyId(c)}
								className={`border-b cursor-pointer hover:bg-muted/30 ${
									selectedKey === keyId(c) ? "bg-blue-50" : ""
								}`}
								onClick={() => setSelectedKey(keyId(c))}
							>
								<td className="p-2">
									<input
										type="radio"
										name="entity_id_source"
										checked={selectedKey === keyId(c)}
										onChange={() => setSelectedKey(keyId(c))}
									/>
								</td>
								<td className="p-2">
									<Badge variant="outline" className="text-xs">
										{c.type}
									</Badge>
								</td>
								<td className="p-2 font-mono text-xs">{c.key}</td>
								<td
									className="p-2 font-mono text-xs truncate max-w-[200px]"
									title={c.value}
								>
									{c.value}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>

			<div className="flex justify-end gap-2">
				<Button variant="ghost" onClick={onSkip} disabled={isPending}>
					Skip
				</Button>
				<Button
					onClick={() => selected && onSelect(selected)}
					disabled={!selected || isPending}
				>
					{isPending ? "Saving…" : "Use this field"}
				</Button>
			</div>
		</div>
	);
}
