/**
 * Builder preview pane.
 *
 * Generated apps render on a separate app origin through a one-time launch
 * URL. The control plane never frames a guessed app-origin path: it shows an
 * explicit deployment/launch state until the backend returns a real URL.
 */

import { useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
	AlertTriangle,
	ArrowRight,
	FileCode2,
	Loader2,
	Maximize2,
	Monitor,
	RefreshCw,
	ShieldCheck,
	Smartphone,
	Tablet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type PreviewDevice = "responsive" | "desktop" | "tablet" | "mobile";

const DEVICES: {
	value: PreviewDevice;
	label: string;
	icon: typeof Monitor;
	className: string;
}[] = [
	{
		value: "responsive",
		label: "Responsive",
		icon: Maximize2,
		className: "w-full",
	},
	{
		value: "desktop",
		label: "Desktop",
		icon: Monitor,
		className: "w-[1280px] max-w-full",
	},
	{
		value: "tablet",
		label: "Tablet",
		icon: Tablet,
		className: "w-[768px] max-w-full",
	},
	{
		value: "mobile",
		label: "Mobile",
		icon: Smartphone,
		className: "w-[390px] max-w-full",
	},
];

interface PreviewPaneProps {
	/** One-time app-host launch URL minted for this exact Solution app. */
	launchUrl: string | null;
	state: "unconfigured" | "waiting" | "loading" | "failed" | "ready";
	errorMessage?: string | null;
	route: string;
	onRouteChange: (route: string) => void;
	onReload: () => void;
	/** Source is ahead of the deployed revision — preview is last-good. */
	isStale: boolean;
	device?: PreviewDevice;
	onDeviceChange?: (device: PreviewDevice) => void;
}

interface PreviewRouteControlsProps {
	route: string;
	onRouteChange: (route: string) => void;
	canRequestLaunch: boolean;
	isLoading: boolean;
}

function PreviewRouteControls({
	route,
	onRouteChange,
	canRequestLaunch,
	isLoading,
}: PreviewRouteControlsProps) {
	const [routeDraft, setRouteDraft] = useState(route);

	function handleNavigate(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const nextRoute = routeDraft.trim();
		onRouteChange(
			nextRoute.startsWith("/") ? nextRoute : `/${nextRoute || ""}`,
		);
	}

	return (
		<form className="flex min-w-0 flex-1 gap-1" onSubmit={handleNavigate}>
			<Input
				value={routeDraft}
				aria-label="Preview route"
				placeholder="/"
				className="h-8 font-mono text-xs"
				onChange={(event) => setRouteDraft(event.target.value)}
			/>
			<Button
				type="submit"
				variant="ghost"
				size="icon"
				className="h-8 w-8 shrink-0"
				aria-label="Open preview route"
				disabled={!canRequestLaunch || isLoading}
			>
				<ArrowRight className="h-4 w-4" />
			</Button>
		</form>
	);
}

function PreviewRestoreState({
	phase,
	overlay = false,
}: {
	phase: "session" | "document";
	overlay?: boolean;
}) {
	const reduceMotion = useReducedMotion();
	const title =
		phase === "session"
			? "Restoring your preview"
			: "Starting your preview";
	const detail =
		phase === "session"
			? "Creating a secure app session for your last successful build."
			: "The secure session is ready. Loading the saved app now.";

	return (
		<motion.div
			role="status"
			aria-live="polite"
			aria-label={title}
			className={cn(
				"flex h-full min-h-[360px] flex-col items-center justify-center gap-5 p-8 text-center",
				overlay && "absolute inset-2 bg-muted/30",
			)}
			initial={reduceMotion ? false : { opacity: 0 }}
			animate={{ opacity: 1 }}
			exit={reduceMotion ? undefined : { opacity: 0 }}
			transition={{ duration: reduceMotion ? 0 : 0.18 }}
		>
			<div className="w-full max-w-xs" aria-hidden="true">
				<div className="flex items-center justify-between text-muted-foreground">
					<span className="flex h-9 w-9 items-center justify-center rounded-full bg-background shadow-sm ring-1 ring-border">
						<FileCode2 className="h-4 w-4" />
					</span>
					<span className="h-px flex-1 bg-border" />
					<span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary ring-1 ring-primary/25">
						<ShieldCheck className="h-4 w-4" />
					</span>
					<span className="h-px flex-1 bg-border" />
					<span className="flex h-9 w-9 items-center justify-center rounded-full bg-background shadow-sm ring-1 ring-border">
						<Monitor className="h-4 w-4" />
					</span>
				</div>
				<div className="mt-3 h-1 overflow-hidden rounded-full bg-muted">
					<motion.div
						className="h-full w-1/3 rounded-full bg-primary"
						initial={reduceMotion ? { x: "100%" } : { x: "-100%" }}
						animate={reduceMotion ? { x: "100%" } : { x: "300%" }}
						transition={
							reduceMotion
								? { duration: 0 }
								: {
										duration: 1.1,
										ease: [0.16, 1, 0.3, 1],
										repeat: Infinity,
									}
						}
					/>
				</div>
				<div className="mt-2 flex justify-between text-[10px] text-muted-foreground">
					<span>Saved build</span>
					<span>Secure session</span>
					<span>Preview</span>
				</div>
			</div>
			<div>
				<p className="text-sm font-medium">{title}</p>
				<p className="mt-1 max-w-sm text-sm text-muted-foreground">
					{detail}
				</p>
			</div>
		</motion.div>
	);
}

function PreviewDocument({
	launchUrl,
	device,
	deviceClassName,
}: {
	launchUrl: string;
	device: PreviewDevice;
	deviceClassName: string;
}) {
	const [frameReady, setFrameReady] = useState(false);

	return (
		<div className="relative flex h-full min-h-[360px] justify-center p-2">
			<div
				className={cn(
					"h-full min-h-[340px] overflow-hidden bg-background transition-[width] duration-200",
					device !== "responsive" &&
						"rounded-lg shadow-sm ring-1 ring-foreground/10",
					deviceClassName,
				)}
			>
				<iframe
					title="App preview"
					data-testid="preview-frame"
					src={launchUrl}
					className="h-full w-full border-0 bg-background"
					sandbox="allow-scripts allow-forms allow-same-origin"
					onLoad={() => setFrameReady(true)}
				/>
			</div>
			<AnimatePresence>
				{!frameReady ? (
					<PreviewRestoreState
						key="preview-document-loading"
						phase="document"
						overlay
					/>
				) : null}
			</AnimatePresence>
		</div>
	);
}

export function PreviewPane({
	launchUrl,
	state,
	errorMessage,
	route,
	onRouteChange,
	onReload,
	isStale,
	device: controlledDevice,
	onDeviceChange,
}: PreviewPaneProps) {
	const canRequestLaunch = state !== "unconfigured" && state !== "waiting";
	const [internalDevice, setInternalDevice] =
		useState<PreviewDevice>("responsive");
	const device = controlledDevice ?? internalDevice;
	const selectedDevice =
		DEVICES.find((option) => option.value === device) ?? DEVICES[0];

	function selectDevice(nextDevice: PreviewDevice) {
		setInternalDevice(nextDevice);
		onDeviceChange?.(nextDevice);
	}

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex items-center gap-2 border-b p-2">
				<PreviewRouteControls
					key={route}
					route={route}
					onRouteChange={onRouteChange}
					canRequestLaunch={canRequestLaunch}
					isLoading={state === "loading"}
				/>
				<Button
					variant="ghost"
					size="icon"
					className="h-8 w-8 shrink-0"
					aria-label="Reload preview"
					disabled={!canRequestLaunch || state === "loading"}
					onClick={onReload}
				>
					{state === "loading" ? (
						<Loader2 className="h-4 w-4 animate-spin" />
					) : (
						<RefreshCw className="h-4 w-4" />
					)}
				</Button>
				<div
					className="hidden shrink-0 items-center rounded-md bg-muted/60 p-0.5 sm:flex"
					role="group"
					aria-label="Preview device"
				>
					{DEVICES.map((option) => {
						const Icon = option.icon;
						return (
							<Button
								key={option.value}
								type="button"
								variant="ghost"
								size="icon"
								className={cn(
									"h-7 w-7",
									device === option.value &&
										"bg-background shadow-sm",
								)}
								aria-label={`${option.label} preview`}
								aria-pressed={device === option.value}
								onClick={() => selectDevice(option.value)}
							>
								<Icon className="h-3.5 w-3.5" />
							</Button>
						);
					})}
				</div>
				{isStale && (
					<Badge
						variant="outline"
						className="shrink-0 gap-1 border-amber-500/50 text-amber-600 dark:text-amber-400"
						data-testid="stale-preview-badge"
					>
						<AlertTriangle className="h-3 w-3" />
						Stale
					</Badge>
				)}
			</div>

			<div
				className="min-h-0 flex-1 overflow-auto bg-muted/30"
				data-device={device}
			>
				{state === "ready" && launchUrl ? (
					<PreviewDocument
						key={launchUrl}
						launchUrl={launchUrl}
						device={device}
						deviceClassName={selectedDevice.className}
					/>
				) : state === "loading" ? (
					<PreviewRestoreState phase="session" />
				) : (
					<div
						className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center"
						data-testid="preview-unavailable"
					>
						<AlertTriangle className="h-6 w-6 text-muted-foreground" />
						<p className="text-sm font-medium">
							{state === "waiting"
								? "Preview is not deployed yet"
								: "Preview unavailable"}
						</p>
						<p className="max-w-sm text-sm text-muted-foreground">
							{state === "unconfigured"
								? "The separate app host is not configured for this environment."
								: state === "waiting"
									? "Your source is saved. The preview will appear after the first successful build and deploy."
									: (errorMessage ??
										"The app host could not create a preview session.")}
						</p>
					</div>
				)}
			</div>
		</div>
	);
}
