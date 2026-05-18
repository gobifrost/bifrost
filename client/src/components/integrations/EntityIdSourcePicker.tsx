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

			<div className="max-h-80 overflow-y-auto space-y-2 pr-1">
				{candidates.map((c) => {
					const isSelected = selectedKey === keyId(c);
					return (
						<button
							type="button"
							key={keyId(c)}
							onClick={() => setSelectedKey(keyId(c))}
							className={`w-full text-left rounded-md border p-2 cursor-pointer transition-colors ${
								isSelected
									? "border-primary bg-primary/5"
									: "hover:bg-muted/30"
							}`}
						>
							<div className="min-w-0 space-y-1">
								<div className="flex items-center gap-2 min-w-0">
									<span className="font-mono text-xs truncate">
										{c.key}
									</span>
									<Badge
										variant="outline"
										className="text-[10px] shrink-0"
									>
										{c.type}
									</Badge>
								</div>
								<div
									className="font-mono text-xs text-muted-foreground break-all"
									title={c.value}
								>
									{c.value}
								</div>
							</div>
						</button>
					);
				})}
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
