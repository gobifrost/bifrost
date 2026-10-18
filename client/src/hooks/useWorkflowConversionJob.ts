import { useCallback, useEffect, useRef, useState } from "react";

import { getPlatformJob, type PlatformJob } from "@/services/platformJobs";
import { webSocketService } from "@/services/websocket";
import {
	getWorkflowConversionReport,
	type WorkflowConversionReport,
} from "@/services/workflowConversions";

const TERMINAL = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

type JobError = { jobId: string; message: string };

function messageFor(cause: unknown, fallback: string) {
	return cause instanceof Error ? cause.message : fallback;
}

export function useWorkflowConversionJob(jobId: string | null) {
	const [job, setJob] = useState<PlatformJob | null>(null);
	const [report, setReport] = useState<WorkflowConversionReport | null>(null);
	const [error, setError] = useState<JobError | null>(null);
	const [loadingJobId, setLoadingJobId] = useState<string | null>(jobId);
	const jobIdRef = useRef(jobId);
	const revisionRef = useRef(-1);

	useEffect(() => {
		jobIdRef.current = jobId;
		revisionRef.current = -1;
	}, [jobId]);

	const acceptJob = useCallback((next: PlatformJob) => {
		if (next.id !== jobIdRef.current || next.revision < revisionRef.current)
			return;
		revisionRef.current = next.revision;
		setJob(next);
	}, []);

	const refresh = useCallback(async () => {
		const requestedJobId = jobId;
		if (!requestedJobId) return;
		setLoadingJobId(requestedJobId);
		setError(null);
		try {
			const [nextJob, nextReport] = await Promise.all([
				getPlatformJob(requestedJobId),
				getWorkflowConversionReport(requestedJobId),
			]);
			if (requestedJobId !== jobIdRef.current) return;
			acceptJob(nextJob);
			setReport(nextReport);
			setError(null);
		} catch (cause) {
			if (requestedJobId === jobIdRef.current) {
				setError({
					jobId: requestedJobId,
					message: messageFor(
						cause,
						"Could not restore this conversion.",
					),
				});
			}
		} finally {
			if (requestedJobId === jobIdRef.current) setLoadingJobId(null);
		}
	}, [acceptJob, jobId]);

	useEffect(() => {
		let active = true;
		queueMicrotask(() => {
			if (active) void refresh();
		});
		return () => {
			active = false;
		};
	}, [refresh]);

	useEffect(() => {
		if (!jobId) return;
		const off = webSocketService.onAnyPlatformJobUpdate((update) => {
			if (update.id !== jobId) return;
			acceptJob(update);
			if (TERMINAL.has(update.status)) void refresh();
		});
		const offConnection = webSocketService.onConnectionStatusChange(
			(connected) => {
				if (connected) void refresh();
			},
		);
		const onOnline = () => void refresh();
		window.addEventListener("online", onOnline);
		return () => {
			off();
			offConnection();
			window.removeEventListener("online", onOnline);
		};
	}, [acceptJob, jobId, refresh]);

	return {
		job: job?.id === jobId ? job : null,
		report: report?.job_id === jobId ? report : null,
		error: error?.jobId === jobId ? error.message : null,
		loading: loadingJobId === jobId,
		refresh,
		setReport,
	};
}

/** Observe a follow-up apply/cutover job without trying to read an analysis report. */
export function useWorkflowConversionOperation(jobId: string | null) {
	const [job, setJob] = useState<PlatformJob | null>(null);
	const [error, setError] = useState<JobError | null>(null);
	const jobIdRef = useRef(jobId);
	const revisionRef = useRef(-1);

	useEffect(() => {
		jobIdRef.current = jobId;
		revisionRef.current = -1;
	}, [jobId]);

	const acceptJob = useCallback((next: PlatformJob) => {
		if (next.id !== jobIdRef.current || next.revision < revisionRef.current)
			return;
		revisionRef.current = next.revision;
		setJob(next);
	}, []);

	const refresh = useCallback(async () => {
		const requestedJobId = jobId;
		if (!requestedJobId) return;
		try {
			const next = await getPlatformJob(requestedJobId);
			if (requestedJobId !== jobIdRef.current) return;
			acceptJob(next);
			setError(null);
		} catch (cause) {
			if (requestedJobId !== jobIdRef.current) return;
			setError({
				jobId: requestedJobId,
				message: messageFor(
					cause,
					"Could not restore the requested change.",
				),
			});
		}
	}, [acceptJob, jobId]);
	useEffect(() => {
		let active = true;
		queueMicrotask(() => {
			if (active) void refresh();
		});
		return () => {
			active = false;
		};
	}, [refresh]);
	useEffect(() => {
		if (!jobId) return;
		const offJob = webSocketService.onPlatformJobUpdate(jobId, acceptJob);
		const offConnection = webSocketService.onConnectionStatusChange(
			(connected) => {
				if (connected) void refresh();
			},
		);
		return () => {
			offJob();
			offConnection();
		};
	}, [acceptJob, jobId, refresh]);
	return {
		job: job?.id === jobId ? job : null,
		error: error?.jobId === jobId ? error.message : null,
		refresh,
	};
}
