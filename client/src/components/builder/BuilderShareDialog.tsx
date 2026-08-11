import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, Pencil, Trash2, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
	listBuilderCollaborators,
	removeBuilderCollaborator,
	saveBuilderCollaborator,
	type BuilderCollaborator,
} from "@/services/builder";

interface BuilderShareDialogProps {
	solutionId: string;
	solutionName: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

const ACCESS_OPTIONS = [
	{ value: "edit", label: "Can edit", description: "Build, chat, and change source" },
	{ value: "view", label: "Can view", description: "Review conversation, preview, and source" },
];

export function BuilderShareDialog({
	solutionId,
	solutionName,
	open,
	onOpenChange,
}: BuilderShareDialogProps) {
	const queryClient = useQueryClient();
	const queryKey = ["builder", "collaborators", solutionId] as const;
	const [email, setEmail] = useState("");
	const [access, setAccess] = useState<"view" | "edit">("edit");

	const collaboratorsQuery = useQuery({
		queryKey,
		queryFn: ({ signal }) => listBuilderCollaborators(solutionId, { signal }),
		enabled: open,
	});
	const saveMutation = useMutation({
		mutationFn: (request: { email: string; access: "view" | "edit" }) =>
			saveBuilderCollaborator(solutionId, request),
		onSuccess: async (collaborator) => {
			setEmail("");
			await queryClient.invalidateQueries({ queryKey });
			toast.success(`${collaborator.name || collaborator.email} can now ${collaborator.access === "edit" ? "edit" : "view"} this build`);
		},
		onError: (error: Error) => toast.error(error.message),
	});
	const removeMutation = useMutation({
		mutationFn: (collaborator: BuilderCollaborator) =>
			removeBuilderCollaborator(solutionId, collaborator.user_id),
		onSuccess: async () => {
			await queryClient.invalidateQueries({ queryKey });
			toast.success("Collaborator removed");
		},
		onError: (error: Error) => toast.error(error.message),
	});

	function updateAccess(collaborator: BuilderCollaborator, next: string) {
		if (next !== "view" && next !== "edit") return;
		saveMutation.mutate({ email: collaborator.email, access: next });
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-xl overflow-hidden p-0">
				<DialogHeader className="border-b px-6 py-5">
					<div className="flex items-center gap-3">
						<span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary"><Users className="h-5 w-5" /></span>
						<div className="min-w-0"><DialogTitle>Share {solutionName}</DialogTitle><DialogDescription className="mt-1">Invite someone in the same customer organization.</DialogDescription></div>
					</div>
				</DialogHeader>

				<div className="space-y-5 px-6 py-5">
					<div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_150px_auto] sm:items-end">
						<div className="space-y-2"><Label htmlFor="builder-collaborator-email">Email address</Label><Input id="builder-collaborator-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="colleague@customer.com" /></div>
						<div className="space-y-2"><Label htmlFor="builder-collaborator-access">Access</Label><Combobox id="builder-collaborator-access" aria-label="Collaborator access" options={ACCESS_OPTIONS} value={access} onValueChange={(value) => value && setAccess(value as "view" | "edit")} /></div>
						<Button disabled={!email.trim() || saveMutation.isPending} onClick={() => saveMutation.mutate({ email: email.trim(), access })}>{saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserPlus className="h-4 w-4" />}Invite</Button>
					</div>

					<div>
						<div className="mb-2 flex items-center justify-between"><p className="text-sm font-medium">People with access</p><Badge variant="outline">{collaboratorsQuery.data?.length ?? 0}</Badge></div>
						{collaboratorsQuery.isLoading ? <div className="space-y-2"><Skeleton className="h-14 w-full" /><Skeleton className="h-14 w-full" /></div> : collaboratorsQuery.isError ? <div className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">Could not load collaborators. <Button variant="link" className="h-auto p-0 text-destructive" onClick={() => collaboratorsQuery.refetch()}>Try again</Button></div> : collaboratorsQuery.data?.length ? (
							<div className="max-h-64 divide-y overflow-auto rounded-2xl border">
								{collaboratorsQuery.data.map((collaborator) => (
									<div key={collaborator.id} className="flex flex-wrap items-center gap-3 p-3">
										<Avatar className="h-8 w-8"><AvatarFallback className="text-xs">{initials(collaborator.name || collaborator.email)}</AvatarFallback></Avatar>
										<div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{collaborator.name || collaborator.email}</p>{collaborator.name ? <p className="truncate text-xs text-muted-foreground">{collaborator.email}</p> : null}</div>
										<div className="w-32"><Combobox aria-label={`Access for ${collaborator.name || collaborator.email}`} options={ACCESS_OPTIONS} value={collaborator.access} onValueChange={(next) => updateAccess(collaborator, next)} className="h-8 border-0 shadow-none" /></div>
										<Button variant="ghost" size="icon" aria-label={`Remove ${collaborator.name || collaborator.email}`} disabled={removeMutation.isPending} onClick={() => removeMutation.mutate(collaborator)}><Trash2 className="h-4 w-4" /></Button>
									</div>
								))}
							</div>
						) : <div className="rounded-2xl border border-dashed p-6 text-center"><Users className="mx-auto h-6 w-6 text-muted-foreground" /><p className="mt-2 text-sm font-medium">Only you have access</p><p className="mt-1 text-xs text-muted-foreground">Invite an editor to collaborate or a viewer to review.</p></div>}
					</div>

					<div className="flex gap-3 rounded-2xl bg-muted/35 p-3 text-xs leading-5 text-muted-foreground"><span className="mt-0.5">{access === "edit" ? <Pencil className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}</span><p>Private builds never appear in another user’s normal library unless you explicitly share them. Platform support can still find them from the separate customer-support catalog.</p></div>
				</div>
			</DialogContent>
		</Dialog>
	);
}

function initials(value: string): string {
	return value.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";
}
