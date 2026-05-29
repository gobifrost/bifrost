import { HelpSlideout } from "@/components/shared/HelpSlideout";
import type { TopicRegistryEntry } from "@/services/events";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

type EventTopicReferenceTopic = TopicRegistryEntry & {
	category?: string;
	emitted_by?: string;
	example_body?: unknown;
};

interface EventTopicReferencePanelProps {
	topics: EventTopicReferenceTopic[];
}

const WORKFLOW_EXAMPLE = `from bifrost import workflow, context

@workflow(name="handle_builtin_event")
async def handle_builtin_event():
    event = context.event
    if event is None:
        return {"handled": False}

    event_id = event.id
    event_type = event.type
    body = event.data
    organization_id = event.organization_id
    received_at = event.received_at

    raw_event = context.parameters["_event"]
    headers = raw_event.get("headers") or {}
    source_ip = raw_event.get("source_ip")

    workflow_name = body.get("workflow", {}).get("name")
    return {"event_id": event_id, "event_type": event_type}`;

const INPUT_MAPPING_EXAMPLE = `workflow_name: "{{ _event.body.workflow.name }}"
error_message: "{{ _event.body.error.message }}"
event_type: "{{ _event.type }}"`;

function formatJson(value: unknown) {
	return JSON.stringify(value, null, 2);
}

function ExampleBlock({
	label,
	language,
	children,
}: {
	label: string;
	language: string;
	children: string;
}) {
	return (
		<div className="space-y-2">
			<div className="text-xs font-medium text-muted-foreground">
				{label}
			</div>
			<SyntaxHighlighter
				language={language}
				style={oneDark}
				customStyle={{
					margin: 0,
					borderRadius: "0.375rem",
					fontSize: "0.75rem",
					lineHeight: "1.5",
				}}
				codeTagProps={{ style: { fontFamily: "inherit" } }}
			>
				{children}
			</SyntaxHighlighter>
		</div>
	);
}

function TopicExample({ topic }: { topic: EventTopicReferenceTopic }) {
	return (
		<section className="space-y-3">
			<div className="space-y-1">
				<div className="flex items-center gap-2">
					<code className="rounded border bg-muted px-1.5 py-0.5 text-xs">
						{topic.topic}
					</code>
					<span className="text-xs text-muted-foreground">
						{topic.category ?? "Built-in"}
					</span>
				</div>
				<p className="text-sm text-muted-foreground">
					{topic.description}
				</p>
				<p className="text-xs text-muted-foreground">
					Emitted by {topic.emitted_by ?? "Bifrost"}
				</p>
			</div>
			<ExampleBlock label={`${topic.topic} body`} language="json">
				{formatJson(topic.example_body ?? {})}
			</ExampleBlock>
		</section>
	);
}

export function EventTopicReferencePanel({
	topics,
}: EventTopicReferencePanelProps) {
	return (
		<HelpSlideout title="Event source reference">
			<section className="space-y-3">
				<p className="text-sm text-muted-foreground">
					Event-triggered workflows can read a typed envelope from{" "}
					<code>context.event</code>. The same raw payload is
					available at{" "}
					<code>context.parameters["_event"]["body"]</code> for input
					mappings.
				</p>
				<ExampleBlock label="Python workflow access" language="python">
					{WORKFLOW_EXAMPLE}
				</ExampleBlock>
				<ExampleBlock label="Input mapping access" language="yaml">
					{INPUT_MAPPING_EXAMPLE}
				</ExampleBlock>
			</section>

			<section className="space-y-3">
				<div className="space-y-1">
					<h3 className="text-sm font-medium">Webhook envelope</h3>
					<p className="text-sm text-muted-foreground">
						Webhooks expose adapter output as the event body, plus
						raw request metadata where available.
					</p>
				</div>
				<ExampleBlock label="_event envelope" language="json">
					{formatJson({
						id: "550e8400-e29b-41d4-a716-446655440000",
						type: "ticket.created",
						body: {
							ticket_id: "12345",
							status: "New",
						},
						headers: {
							"x-vendor-signature": "...",
						},
						received_at: "2026-05-28T12:34:56Z",
						source_ip: "203.0.113.10",
					})}
				</ExampleBlock>
			</section>

			<section className="space-y-5">
				<div className="space-y-1">
					<h3 className="text-sm font-medium">
						Built-in event bodies
					</h3>
					<p className="text-sm text-muted-foreground">
						Built-in bodies use the same common keys:{" "}
						<code>schema_version</code>, <code>occurred_at</code>,{" "}
						<code>organization</code>, and <code>actor</code>.
					</p>
				</div>
				{topics.map((topic) => (
					<TopicExample key={topic.topic} topic={topic} />
				))}
			</section>
		</HelpSlideout>
	);
}
