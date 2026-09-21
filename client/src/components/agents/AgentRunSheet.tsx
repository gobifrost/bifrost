import { useRef, useState } from "react";
import type { components } from "@/lib/v1";
import { useQueryClient } from "@tanstack/react-query";
import { RunActionFeedback } from "./RunActionFeedback";
import { Button } from "@/components/ui/button";
import {
	useAgentRun,
	useSetVerdict,
	useClearVerdict,
} from "@/services/agentRuns";
import { RunReviewSheet } from "./RunReviewSheet";
import type { Verdict } from "./RunReviewPanel";
import { RunFindingAction } from "./RunFindingAction";

function ReadNotice({
	resource,
	pending,
	onRetry,
}: {
	resource: string;
	pending: boolean;
	onRetry: () => void;
}) {
	return (
		<div
			role="alert"
			className="space-y-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
		>
			<p>Could not load {resource}. Retry to get the latest details.</p>
			<Button
				type="button"
				variant="outline"
				className="min-h-11"
				disabled={pending}
				onClick={onRetry}
			>
				Retry {resource}
			</Button>
		</div>
	);
}

interface AgentRunSheetProps {
	openRunId: string | null;
	onClose: () => void;
}

export function AgentRunSheet({ openRunId, onClose }: AgentRunSheetProps) {
	const detailQuery = useAgentRun(openRunId ?? undefined);
	const runDetail = detailQuery.data as unknown as
		components["schemas"]["AgentRunDetailResponse"] | undefined;
	const verdict: Verdict =
		runDetail?.verdict === "up" || runDetail?.verdict === "down"
			? runDetail.verdict
			: null;
	const queryClient = useQueryClient();
	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	const reviewBusy = useRef(false);
	const [saving, setSaving] = useState(false);
	const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
	const [failure, setFailure] = useState<{
		runId: string;
		verdict: Verdict;
		note: string;
	} | null>(null);
	const note = openRunId
		? (noteDrafts[openRunId] ?? runDetail?.verdict_note ?? "")
		: "";
	function setNote(value: string) {
		if (openRunId)
			setNoteDrafts((previous) => ({ ...previous, [openRunId]: value }));
	}
	function saveReview(next: Verdict, savedNote = note) {
		if (!openRunId || reviewBusy.current) return;
		const runId = openRunId;
		reviewBusy.current = true;
		setSaving(true);
		setFailure(null);
		const callbacks = {
			onSuccess: () => {
				const nextNote = next === null ? "" : savedNote;
				setNoteDrafts((previous) => ({
					...previous,
					[runId]: nextNote,
				}));
				queryClient.setQueryData<typeof runDetail>(
					["agent-runs", runId],
					(previous) =>
						previous
							? {
									...previous,
									verdict: next,
									verdict_note: nextNote || null,
								}
							: previous,
				);
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs"],
				});
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs-infinite"],
				});
			},
			onError: () =>
				setFailure({ runId, verdict: next, note: savedNote }),
			onSettled: () => {
				reviewBusy.current = false;
				setSaving(false);
			},
		};
		if (next === null)
			clearVerdict.mutate(
				{ params: { path: { run_id: runId } } },
				callbacks,
			);
		else
			setVerdict.mutate(
				{
					params: { path: { run_id: runId } },
					body: { verdict: next, note: savedNote || null },
				},
				callbacks,
			);
	}
	const open = !!openRunId;
	const noteChanged = note !== (runDetail?.verdict_note ?? "");
	const findingAction =
		runDetail && verdict === "down" ? (
			<RunFindingAction run={runDetail} note={note} />
		) : null;

	return (
		<RunReviewSheet
			open={open}
			onOpenChange={(o) => {
				if (!o && !reviewBusy.current) onClose();
			}}
			run={runDetail ?? null}
			verdict={verdict}
			note={note}
			onVerdict={saveReview}
			reviewPending={saving}
			reviewFeedback={
				saving || (failure !== null && failure.runId === openRunId) ? (
					<RunActionFeedback
						pending={saving}
						failed={
							failure?.runId === openRunId && failure !== null
						}
						onRetry={() => {
							if (failure)
								saveReview(failure.verdict, failure.note);
						}}
					/>
				) : null
			}
			reviewActions={
				noteChanged || findingAction ? (
					<div className="space-y-3">
						{noteChanged ? (
							verdict ? (
								<Button
									type="button"
									className="min-h-11"
									onClick={() => saveReview(verdict)}
								>
									Save review note
								</Button>
							) : (
								<p className="text-sm text-muted-foreground">
									Choose Good or Wrong to save this note.
								</p>
							)
						) : null}
						{findingAction}
					</div>
				) : null
			}
			onNote={setNote}
			readFeedback={
				detailQuery.isError ? (
					<ReadNotice
						resource="run details"
						pending={detailQuery.isFetching}
						onRetry={() => void detailQuery.refetch()}
					/>
				) : null
			}
		/>
	);
}
