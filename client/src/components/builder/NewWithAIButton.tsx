/**
 * Builder entry point.
 *
 * Renders nothing unless the caller holds `solutions.build`. Creating a
 * private Solution navigates straight into its builder workspace.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useBuilderAccess, builderSolutionsQueryKey } from "@/hooks/useBuilderAccess";
import { createBuilderSolution } from "@/services/builder";

/** Lowercase, hyphenated, no leading/trailing separators. */
export function slugify(name: string): string {
	return name
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
}

interface NewWithAIButtonProps {
	/** Button copy. Solutions says "New with AI"; Apps says "Build an app". */
	label: string;
}

export function NewWithAIButton({ label }: NewWithAIButtonProps) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { canBuild } = useBuilderAccess();
	const [open, setOpen] = useState(false);
	const [name, setName] = useState("");
	const [error, setError] = useState<string | null>(null);

	const slug = slugify(name);

	const createMutation = useMutation({
		mutationFn: () => createBuilderSolution({ slug, name: name.trim() }),
		onSuccess: (solution) => {
			queryClient.invalidateQueries({ queryKey: builderSolutionsQueryKey });
			setOpen(false);
			setName("");
			navigate(`/solutions/${solution.id}/builder`);
		},
		onError: (err: Error) => setError(err.message),
	});

	if (!canBuild) return null;

	function handleOpenChange(next: boolean) {
		setOpen(next);
		if (!next) {
			setName("");
			setError(null);
		}
	}

	return (
		<>
			<Button
				variant="outline"
				size="sm"
				data-testid="builder-entry-point"
				onClick={() => setOpen(true)}
			>
				<Sparkles className="mr-2 h-4 w-4" />
				{label}
			</Button>

			<Dialog open={open} onOpenChange={handleOpenChange}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>{label}</DialogTitle>
						<DialogDescription>
							Creates a private Solution only you can see, edit, and run.
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-2">
						<Label htmlFor="builder-solution-name">Name</Label>
						<Input
							id="builder-solution-name"
							value={name}
							autoFocus
							placeholder="Expense tracker"
							onChange={(event) => {
								setName(event.target.value);
								setError(null);
							}}
						/>
						{slug && (
							<p className="text-xs text-muted-foreground">
								Slug: <code>{slug}</code>
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive" role="alert">
								{error}
							</p>
						)}
					</div>

					<DialogFooter>
						<Button variant="ghost" onClick={() => handleOpenChange(false)}>
							Cancel
						</Button>
						<Button
							disabled={!slug || createMutation.isPending}
							onClick={() => createMutation.mutate()}
						>
							{createMutation.isPending && (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							)}
							Create
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}
