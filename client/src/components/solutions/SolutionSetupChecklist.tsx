/**
 * SolutionSetupChecklist
 *
 * Presentational component that lists a solution's config declarations and
 * lets the user set values for any that are missing. The parent page owns
 * data-fetching and the real config-set mutation; this component is kept
 * deliberately side-effect-free so it's unit-testable without a network.
 */

import { useState } from "react";
import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SolutionSetupItem } from "@/services/solutions";

export interface SolutionSetupChecklistProps {
	items: SolutionSetupItem[];
	setupComplete: boolean;
	/** Called when the user submits a value for a config key. */
	onSet: (key: string, value: string) => void | Promise<void>;
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

function ConfigItem({
	item,
	onSet,
}: {
	item: SolutionSetupItem;
	onSet: (key: string, value: string) => void | Promise<void>;
}) {
	const [value, setValue] = useState("");
	const [pending, setPending] = useState(false);
	const secret = isSecretType(item.type);
	const requiredUnset = item.required && !item.is_set;

	const handleSet = async () => {
		if (!value.trim()) return;
		setPending(true);
		try {
			await onSet(item.key, value);
			setValue("");
		} finally {
			setPending(false);
		}
	};

	const placeholder = item.is_set
		? "Enter a new value…"
		: item.default
			? `Default: ${item.default}`
			: "Enter a value…";

	return (
		<div
			className={
				"rounded-lg border p-4 " +
				(requiredUnset ? "border-yellow-500/60 bg-yellow-500/5" : "")
			}
		>
			{/* Key + meta row */}
			<div className="flex items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<span className="truncate font-mono text-sm font-medium">
						{item.key}
					</span>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						{item.type}
					</Badge>
					{item.required && (
						<span className="shrink-0 text-xs text-destructive">required</span>
					)}
				</div>
				<span
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(item.is_set
							? "text-green-600 dark:text-green-500"
							: "text-muted-foreground")
					}
				>
					{item.is_set ? (
						<CheckCircle2 className="h-3.5 w-3.5" />
					) : (
						<Circle className="h-3.5 w-3.5" />
					)}
					{item.is_set ? "Set" : "Not set"}
				</span>
			</div>

			{item.description && (
				<p className="mt-1 text-xs text-muted-foreground">{item.description}</p>
			)}

			{/* Value input — always rendered so the user can override an existing value */}
			<div className="mt-3 flex items-center gap-2">
				<Input
					data-testid={`config-value-input-${item.key}`}
					type={secret ? "password" : "text"}
					value={value}
					placeholder={placeholder}
					onChange={(e) => setValue(e.target.value)}
					onKeyDown={(e) => {
						if (e.key === "Enter" && value.trim()) {
							void handleSet();
						}
					}}
				/>
				{(value.trim() || requiredUnset) && (
					<Button
						disabled={!value.trim() || pending}
						onClick={() => void handleSet()}
					>
						{pending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
						Set
					</Button>
				)}
			</div>
		</div>
	);
}

export function SolutionSetupChecklist({
	items,
	setupComplete,
	onSet,
}: SolutionSetupChecklistProps) {
	if (items.length === 0) {
		return (
			<div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">
				This Solution declares no configuration.
			</div>
		);
	}

	return (
		<div className="space-y-3">
			{setupComplete && (
				<div
					data-testid="setup-complete-banner"
					className="flex items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/5 px-4 py-3 text-sm text-green-700 dark:text-green-400"
				>
					<CheckCircle2 className="h-4 w-4 shrink-0" />
					All required configs are set — this Solution is ready to run.
				</div>
			)}
			{items.map((item) => (
				<ConfigItem key={item.key} item={item} onSet={onSet} />
			))}
		</div>
	);
}
