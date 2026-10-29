import { useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { EventStatusBadge } from "./EventStatusBadge";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { XCircle, Loader2, Copy, Check } from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";
import { useEvent, useDeliveries, type Event } from "@/services/events";
import { DeliveriesTable } from "./DeliveriesTable";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";

interface EventDetailDialogProps {
	event: Event | null;
	eventId?: string;
	onClose: () => void;
	canManage?: boolean;
}

function EventReadError({
	label,
	pending,
	onRetry,
}: {
	label: string;
	pending: boolean;
	onRetry: () => void;
}) {
	return (
		<div
			role="alert"
			className="min-w-0 space-y-3 rounded-[var(--bf-radius-surface)] border p-4 text-sm leading-6"
		>
			<p>
				Could not load {label.toLowerCase()}. Any previously loaded data
				remains visible below.
			</p>
			<Button
				type="button"
				variant="outline"
				className="min-h-11"
				disabled={pending}
				onClick={onRetry}
			>
				{pending && (
					<Loader2 className="size-4 motion-safe:animate-spin" />
				)}
				Retry {label.toLowerCase()}
			</Button>
		</div>
	);
}

function TextViewer({ data, label }: { data: unknown; label: string }) {
	const [copiedText, setCopiedText] = useState<string | null>(null);
	const [copyFailed, setCopyFailed] = useState(false);
	const displayText =
		(typeof data === "string" ? data : JSON.stringify(data, null, 2)) ?? "";
	const copied = copiedText === displayText;
	const handleCopy = async () => {
		try {
			await navigator.clipboard.writeText(displayText);
			setCopiedText(displayText);
			setCopyFailed(false);
			toast.success(`${label} copied to clipboard`);
		} catch {
			setCopyFailed(true);
			setCopiedText(null);
		}
	};
	return (
		<div className="min-w-0 space-y-2">
			<Button
				type="button"
				variant="outline"
				className="min-h-11"
				onClick={handleCopy}
				aria-label={`Copy ${label.toLowerCase()}`}
			>
				{copied ? (
					<Check className="size-4 text-[var(--bf-success)]" />
				) : (
					<Copy className="size-4" />
				)}
				{copied ? "Copied" : `Copy ${label.toLowerCase()}`}
			</Button>
			{copyFailed && (
				<p role="alert" className="text-sm leading-6 text-destructive">
					Could not copy. Select the text below to copy it.
				</p>
			)}
			<pre
				tabIndex={0}
				aria-label={label}
				className="max-h-80 min-w-0 overflow-auto whitespace-pre-wrap rounded-[var(--bf-radius-surface)] border bg-muted/50 p-4 font-mono text-sm leading-6 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				{displayText}
			</pre>
		</div>
	);
}

function EventMetadata({ event }: { event: Event }) {
	return (
		<dl className="grid min-w-0 gap-4 border-b pb-6 text-sm leading-6 @sm/event:grid-cols-2">
			<div className="min-w-0 @sm/event:col-span-2">
				<dt className="text-muted-foreground">Event Type</dt>
				<dd className="mt-1 font-mono [overflow-wrap:anywhere]">
					{event.event_type || "—"}
				</dd>
			</div>
			<div className="min-w-0">
				<dt className="text-muted-foreground">Status</dt>
				<dd className="mt-1">
					<EventStatusBadge status={event.status} />
				</dd>
			</div>
			<div className="min-w-0">
				<dt className="text-muted-foreground">Received At</dt>
				<dd className="mt-1 font-medium">
					{format(new Date(event.received_at), "PPpp")}
				</dd>
			</div>
			<div className="min-w-0 @sm/event:col-span-2">
				<dt className="text-muted-foreground">Source IP</dt>
				<dd className="mt-1 font-mono [overflow-wrap:anywhere]">
					{event.source_ip || "Unknown"}
				</dd>
			</div>
		</dl>
	);
}

function EventDetailContent({
	eventId,
	canManage,
}: {
	eventId: string;
	canManage: boolean;
}) {
	const {
		data: event,
		isLoading: eventLoading,
		isError: eventError,
		isFetching: eventFetching,
		refetch: refetchEvent,
	} = useEvent(eventId);
	const {
		data: deliveriesData,
		isLoading: deliveriesLoading,
		isError: deliveriesError,
		isFetching: deliveriesFetching,
		refetch: refetchDeliveries,
	} = useDeliveries(eventId);

	if (eventLoading && !event) {
		return (
			<div className="space-y-4">
				<Skeleton className="h-24 w-full" />
				<Skeleton className="h-48 w-full" />
			</div>
		);
	}

	if (!event && eventError)
		return (
			<EventReadError
				label="Event"
				pending={eventFetching}
				onRetry={() => void refetchEvent()}
			/>
		);

	if (!event) {
		return (
			<div className="flex flex-col items-center justify-center py-8">
				<XCircle className="h-12 w-12 text-muted-foreground mb-4" />
				<p className="text-muted-foreground">Event not found</p>
			</div>
		);
	}

	return (
		<div className="@container/event min-w-0 space-y-6">
			{eventError && (
				<EventReadError
					label="Event"
					pending={eventFetching}
					onRetry={() => void refetchEvent()}
				/>
			)}
			<EventMetadata event={event} />

			{/* Headers Accordion */}
			{event.headers && Object.keys(event.headers).length > 0 && (
				<Accordion type="single" collapsible>
					<AccordionItem value="headers">
						<AccordionTrigger className="min-h-11 text-base font-semibold">
							Request Headers ({Object.keys(event.headers).length}
							)
						</AccordionTrigger>
						<AccordionContent>
							<VariablesTreeView
								data={event.headers as Record<string, unknown>}
							/>
						</AccordionContent>
					</AccordionItem>
				</Accordion>
			)}

			{/* Payload */}
			<div>
				<h4 className="text-base font-semibold mb-3">Event Payload</h4>
				{event.data !== null &&
				typeof event.data === "object" &&
				!Array.isArray(event.data) ? (
					<VariablesTreeView
						data={event.data as Record<string, unknown>}
					/>
				) : (
					<TextViewer data={event.data} label="Payload" />
				)}
			</div>

			{/* Deliveries */}
			<div>
				<h4 className="text-base font-semibold mb-3">
					Deliveries
					{deliveriesData ? ` (${deliveriesData.total})` : ""}
				</h4>
				{deliveriesError && (
					<EventReadError
						label="Deliveries"
						pending={deliveriesFetching}
						onRetry={() => void refetchDeliveries()}
					/>
				)}
				{deliveriesLoading && !deliveriesData ? (
					<Skeleton className="h-32 w-full" />
				) : deliveriesData ? (
					<DeliveriesTable
						deliveries={deliveriesData?.items || []}
						eventId={eventId}
						canManage={canManage}
					/>
				) : null}
			</div>
		</div>
	);
}

export function EventDetailDialog({
	event,
	eventId,
	onClose,
	canManage = false,
}: EventDetailDialogProps) {
	const dialogEventId = event?.id ?? eventId;
	return (
		<Dialog
			open={!!dialogEventId}
			onOpenChange={(open) => !open && onClose()}
		>
			<DialogContent className="flex flex-col overflow-hidden sm:max-w-[700px]">
				<DialogHeader className="shrink-0 border-b pb-4">
					<DialogTitle>Event Details</DialogTitle>
					<DialogDescription>
						Inspect the received payload and its workflow or agent
						deliveries.
					</DialogDescription>
				</DialogHeader>
				{dialogEventId && (
					<div className="-mx-1 min-h-0 min-w-0 overflow-y-auto px-1">
						<EventDetailContent
							key={dialogEventId}
							eventId={dialogEventId}
							canManage={canManage}
						/>
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
