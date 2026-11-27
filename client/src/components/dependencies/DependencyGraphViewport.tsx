import { useEffect, useRef } from "react";
import {
	useNodesInitialized,
	useNodesStore,
	useReactFlow,
	useViewportStore,
} from "@xyflow/react";

const MIN_READABLE_AUTO_ZOOM = 0.5;
const ROOT_FOCUS_ZOOM = 0.8;
const FIT_PADDING = 0.2;

/** Fit new graph data without making dense graphs illegible. */
export function DependencyGraphViewport() {
	const width = useViewportStore((state) => state.width);
	const height = useViewportStore((state) => state.height);
	const nodes = useNodesStore((state) => state.nodes);
	const nodeSignature = nodes.map((node) => node.id).join("|");
	const rootNode = nodes.find(
		(node) =>
			typeof node.data === "object" &&
			node.data !== null &&
			"isRoot" in node.data &&
			node.data.isRoot === true,
	);
	const viewportSignature = `${rootNode?.id ?? ""}::${nodeSignature}`;
	const initialized = useNodesInitialized();
	const { fitView, setCenter } = useReactFlow();
	const fittedSignatureRef = useRef<string | null>(null);

	useEffect(() => {
		if (
			!initialized ||
			width <= 0 ||
			height <= 0 ||
			nodeSignature.length === 0 ||
			fittedSignatureRef.current === viewportSignature
		)
			return;

		fittedSignatureRef.current = viewportSignature;

		const bounds = nodes.reduce(
			(acc, node) => {
				const nodeWidth = node.measured?.width ?? node.width ?? 0;
				const nodeHeight = node.measured?.height ?? node.height ?? 0;
				return {
					minX: Math.min(acc.minX, node.position.x),
					minY: Math.min(acc.minY, node.position.y),
					maxX: Math.max(acc.maxX, node.position.x + nodeWidth),
					maxY: Math.max(acc.maxY, node.position.y + nodeHeight),
				};
			},
			{
				minX: Number.POSITIVE_INFINITY,
				minY: Number.POSITIVE_INFINITY,
				maxX: Number.NEGATIVE_INFINITY,
				maxY: Number.NEGATIVE_INFINITY,
			},
		);

		const graphWidth = bounds.maxX - bounds.minX;
		const graphHeight = bounds.maxY - bounds.minY;
		const viewportWidth = width * (1 - FIT_PADDING * 2);
		const viewportHeight = height * (1 - FIT_PADDING * 2);
		const fitZoom = Math.min(
			viewportWidth / Math.max(graphWidth, 1),
			viewportHeight / Math.max(graphHeight, 1),
			1.5,
		);

		if (fitZoom >= MIN_READABLE_AUTO_ZOOM || !rootNode) {
			void fitView({ padding: "8.34%", maxZoom: 1.5, duration: 0 });
			return;
		}

		const rootWidth = rootNode.measured?.width ?? rootNode.width ?? 0;
		const rootHeight = rootNode.measured?.height ?? rootNode.height ?? 0;
		void setCenter(
			rootNode.position.x + rootWidth / 2,
			rootNode.position.y + rootHeight / 2,
			{ zoom: ROOT_FOCUS_ZOOM, duration: 0 },
		);
	}, [
		width,
		height,
		nodeSignature,
		viewportSignature,
		nodes,
		rootNode,
		initialized,
		fitView,
		setCenter,
	]);
	return null;
}
