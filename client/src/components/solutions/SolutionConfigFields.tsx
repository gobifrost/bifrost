import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { SolutionInstallPreview } from "@/services/solutions";

/** A declared config schema item on a preview, narrowed from the loose dict. */
export interface PreviewConfigSchema {
	key: string;
	type: string;
	required: boolean;
	requiresInput: boolean;
	exists: boolean;
	hasExistingValue: boolean;
	hasPackageDefault: boolean;
	description: string | null;
}

export function asConfigSchemas(
	raw: SolutionInstallPreview["config_schemas"],
): PreviewConfigSchema[] {
	if (!raw) return [];
	return raw
		.map((item) => {
			const key = typeof item.key === "string" ? item.key : "";
			if (!key) return null;
			return {
				key,
				type: typeof item.type === "string" ? item.type : "string",
				required: item.required === true,
				requiresInput: item.requires_input === true,
				exists: item.exists === true,
				hasExistingValue: item.has_existing_value === true,
				hasPackageDefault: item.has_package_default === true,
				description:
					typeof item.description === "string"
						? item.description
						: null,
			};
		})
		.filter((x): x is PreviewConfigSchema => x !== null);
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

export function ConfigValueFields({
	configs,
	values,
	onChange,
	disabledKeys,
}: {
	configs: PreviewConfigSchema[];
	values: Record<string, string>;
	onChange: (key: string, value: string) => void;
	disabledKeys?: Set<string>;
}) {
	return configs.map((cfg) => {
		const value = values[cfg.key] ?? "";
		const disabled = disabledKeys?.has(cfg.key) ?? false;
		const placeholder = cfg.hasExistingValue
			? "Existing value if left blank"
			: cfg.exists
				? "No value set"
				: cfg.hasPackageDefault
					? "Package default if left blank"
					: cfg.description ?? undefined;
		return (
			<div key={cfg.key} className="min-w-0 space-y-1">
				<Label htmlFor={`cfg-${cfg.key}`} className="flex items-center gap-1 break-all">
					{cfg.key}
					{cfg.required && <span className="text-destructive" aria-hidden>*</span>}
				</Label>
				<Input
					id={`cfg-${cfg.key}`}
					type={isSecretType(cfg.type) ? "password" : "text"}
					value={value}
					placeholder={placeholder}
					aria-description={cfg.description ?? undefined}
					disabled={disabled}
					aria-required={cfg.required && !disabled}
					onChange={(event) => onChange(cfg.key, event.target.value)}
				/>
			</div>
		);
	});
}

/** Build the install-time config-value map, dropping blank entries. */
export function nonBlankConfigValues(
	configValues: Record<string, string>,
): Record<string, string> {
	const values: Record<string, string> = {};
	for (const [k, v] of Object.entries(configValues)) {
		if (v.trim() !== "") values[k] = v;
	}
	return values;
}
