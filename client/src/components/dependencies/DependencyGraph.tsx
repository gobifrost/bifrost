import { DependencyGraphControls } from "./DependencyGraphControls";
import { DependencyGraphViewport } from "./DependencyGraphViewport";
/**
 * DependencyGraph - React Flow visualization component
 *
 * Renders a directed graph of entity dependencies using React Flow
 * with automatic layout via dagre.
 */

import { useMemo, useEffect } from "react";
import { useReducedMotion } from "framer-motion";
import {
	ReactFlow,
	Background,
	ReactFlowProvider,
	MiniMap,
	useNodesState,
	useEdgesState,
	type Edge,
	type Node,
	MarkerType,
} from "@xyflow/react";
import dagre from "dagre";
import "@xyflow/react/style.css";
import "./dependency-graph.css";

import {
	EntityNode,
	ENTITY_TYPE_THEME,
	type EntityNodeData,
	type EntityType,
} from "./EntityNode";
import type { GraphNode, GraphEdge } from "@/hooks/useDependencyGraph";
import { cn } from "@/lib/utils";

// Node types for React Flow
const nodeTypes = {
	entity: EntityNode,
};

function getThemeForEntityType(entityType: EntityType | undefined) {
	return ENTITY_TYPE_THEME[entityType ?? "workflow"];
}

export const DEPENDENCY_GRAPH_LEGEND = (
	Object.entries(ENTITY_TYPE_THEME) as Array<
		[EntityType, (typeof ENTITY_TYPE_THEME)[EntityType]]
	>
).map(([entityType, theme]) => ({
	entityType,
	label: theme.label,
	color: theme.accent,
	softColor: theme.accentSoft,
}));

// Layout configuration
const NODE_WIDTH = 200;
const NODE_HEIGHT = 100;

interface DependencyGraphProps {
	nodes: GraphNode[];
	edges: GraphEdge[];
	rootId: string;
	className?: string;
}

/**
 * Apply dagre layout to position nodes in a hierarchical structure
 */
function getLayoutedElements(
	nodes: Node[],
	edges: Edge[],
	direction: "TB" | "LR" = "TB",
): { nodes: Node[]; edges: Edge[] } {
	const dagreGraph = new dagre.graphlib.Graph();
	dagreGraph.setDefaultEdgeLabel(() => ({}));
	dagreGraph.setGraph({
		rankdir: direction,
		nodesep: 80,
		ranksep: 100,
		marginx: 50,
		marginy: 50,
	});

	// Add nodes to dagre
	nodes.forEach((node) => {
		dagreGraph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
	});

	// Add edges to dagre
	edges.forEach((edge) => {
		dagreGraph.setEdge(edge.source, edge.target);
	});

	// Run layout
	dagre.layout(dagreGraph);

	// Get positioned nodes
	const layoutedNodes = nodes.map((node) => {
		const nodeWithPosition = dagreGraph.node(node.id);
		return {
			...node,
			position: {
				x: nodeWithPosition.x - NODE_WIDTH / 2,
				y: nodeWithPosition.y - NODE_HEIGHT / 2,
			},
		};
	});

	return { nodes: layoutedNodes, edges };
}

/**
 * Convert API response to React Flow nodes and edges
 */
function convertToFlowElements(
	apiNodes: GraphNode[],
	apiEdges: GraphEdge[],
	rootId: string,
	reduceMotion: boolean,
): { nodes: Node[]; edges: Edge[] } {
	const nodeTypeById = new Map(
		apiNodes.map((node) => [node.id, node.type as EntityType]),
	);

	// Convert API nodes to React Flow nodes
	const nodes: Node[] = apiNodes.map((node) => ({
		id: node.id,
		type: "entity",
		position: { x: 0, y: 0 }, // Will be set by layout
		data: {
			label: node.name,
			entityType: node.type as EntityType,
			orgId: node.org_id ?? null,
			isRoot: node.id === rootId,
		} satisfies EntityNodeData,
	}));

	// Convert API edges to React Flow edges
	const edges: Edge[] = apiEdges.map((edge, index) => ({
		id: `edge-${index}`,
		source: edge.source,
		target: edge.target,
		type: "smoothstep",
		animated: !reduceMotion,
		style: {
			strokeWidth: 2,
			stroke: getThemeForEntityType(nodeTypeById.get(edge.source)).accent,
		},
		markerEnd: {
			type: MarkerType.ArrowClosed,
			width: 20,
			height: 20,
			color: getThemeForEntityType(nodeTypeById.get(edge.source)).accent,
		},
		label: edge.relationship,
		labelStyle: {
			fontSize: 11,
			fontWeight: 500,
			fill: "var(--foreground)",
		},
		labelBgStyle: {
			fill: "var(--background)",
			fillOpacity: 0.92,
		},
		labelBgPadding: [4, 8] as [number, number],
		labelBgBorderRadius: 4,
	}));

	return { nodes, edges };
}

export function DependencyGraph(props: DependencyGraphProps) {
	return (
		<ReactFlowProvider>
			<DependencyGraphCanvas {...props} />
		</ReactFlowProvider>
	);
}

function DependencyGraphCanvas({
	nodes: apiNodes,
	edges: apiEdges,
	rootId,
	className,
}: DependencyGraphProps) {
	const prefersReducedMotion = useReducedMotion();
	const reduceMotion = prefersReducedMotion ?? false;

	const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
		const { nodes, edges } = convertToFlowElements(
			apiNodes,
			apiEdges,
			rootId,
			reduceMotion,
		);
		return getLayoutedElements(nodes, edges, "TB");
	}, [apiNodes, apiEdges, rootId, reduceMotion]);

	const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
	const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

	// Sync state when initial data changes (e.g., different entity selected)
	useEffect(() => {
		setNodes(initialNodes);
		setEdges(initialEdges);
	}, [initialNodes, initialEdges, setNodes, setEdges]);

	return (
		<div
			className={cn(
				"bf-dependency-graph flex min-h-0 w-full h-full flex-col",
				className,
			)}
		>
			<ReactFlow
				className="min-h-0 flex-1"
				nodes={nodes}
				edges={edges}
				onNodesChange={onNodesChange}
				onEdgesChange={onEdgesChange}
				nodeTypes={nodeTypes}
				minZoom={0.1}
				maxZoom={2}
				proOptions={{ hideAttribution: true }}
				nodesDraggable={false}
				nodesConnectable={false}
				elementsSelectable={true}
				elevateEdgesOnSelect={false}
				panOnScroll={true}
				zoomOnScroll={true}
			>
				<DependencyGraphViewport />
				<Background gap={16} size={1} color="var(--border)" />
				<MiniMap
					className="!hidden md:!block"
					nodeStrokeWidth={3}
					maskColor="color-mix(in srgb, var(--background) 74%, transparent)"
					nodeColor={(node) => {
						const data = node.data as EntityNodeData;
						return getThemeForEntityType(data.entityType).accent;
					}}
				/>
			</ReactFlow>
			<DependencyGraphControls />
		</div>
	);
}
