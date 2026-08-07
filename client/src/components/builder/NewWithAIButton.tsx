/**
 * Builder entry point.
 *
 * Renders nothing unless the caller holds `solutions.build`, then sends the
 * user to the app-first `/build` home. Project creation lives there so every
 * entry point shares the same prompt-first flow.
 */

import { useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBuilderAccess } from "@/hooks/useBuilderAccess";

/** Lowercase, hyphenated, no leading/trailing separators. */
export function slugify(name: string): string {
	return name
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
}

interface NewWithAIButtonProps {
	/** Context-specific button copy; every entry opens the shared Build home. */
	label: string;
}

export function NewWithAIButton({ label }: NewWithAIButtonProps) {
	const navigate = useNavigate();
	const { canBuild } = useBuilderAccess();

	if (!canBuild) return null;

	return (
		<Button
			variant="outline"
			size="sm"
			data-testid="builder-entry-point"
			onClick={() => navigate("/build")}
		>
			<Sparkles className="mr-2 h-4 w-4" />
			{label}
		</Button>
	);
}
