import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, Box, ShieldCheck } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { AppLoadingSkeleton } from "@/components/jsx-app/AppLoadingSkeleton";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { createIsolatedApplicationLaunch } from "@/hooks/useApplications";

interface IsolatedSolutionAppProps {
	appId: string;
	appSlug: string;
}

interface RuntimeNavigationMessage {
	type: "bifrost:app-navigation";
	path?: string;
	search?: string;
	hash?: string;
}

function normalizeRoute(path: string | undefined): string {
	if (!path || path === "/") return "";
	return path.startsWith("/") ? path : `/${path}`;
}

export function IsolatedSolutionApp({
	appId,
	appSlug,
}: IsolatedSolutionAppProps) {
	const navigate = useNavigate();
	const location = useLocation();
	const params = useParams();
	const reduceMotion = useReducedMotion();
	const frameRef = useRef<HTMLIFrameElement>(null);
	const [documentReady, setDocumentReady] = useState(false);
	const nestedPath = normalizeRoute(params["*"]);
	const runtimePath = `${nestedPath || "/"}${location.search}${location.hash}`;

	const launch = useQuery({
		queryKey: ["application", "isolated-launch", appId, runtimePath],
		queryFn: ({ signal }) =>
			createIsolatedApplicationLaunch(appId, runtimePath, { signal }),
		retry: false,
		staleTime: 0,
		gcTime: 0,
		refetchOnMount: "always",
	});

	useEffect(() => {
		function handleNavigation(event: MessageEvent<unknown>) {
			if (event.source !== frameRef.current?.contentWindow) return;
			const message = event.data as Partial<RuntimeNavigationMessage> | null;
			if (!message || message.type !== "bifrost:app-navigation") return;
			const next = `/apps/${appSlug}${normalizeRoute(message.path)}${
				message.search ?? ""
			}${message.hash ?? ""}`;
			const current = `${location.pathname}${location.search}${location.hash}`;
			if (next !== current) navigate(next, { replace: true });
		}

		window.addEventListener("message", handleNavigation);
		return () => window.removeEventListener("message", handleNavigation);
	}, [appSlug, location.hash, location.pathname, location.search, navigate]);

	if (launch.isPending) {
		return <AppLoadingSkeleton message="Restoring secure app session..." />;
	}

	if (launch.isError || !launch.data) {
		return (
			<div className="flex min-h-screen items-center justify-center bg-muted/20 p-4">
				<Card className="w-full max-w-md">
					<CardHeader>
						<div className="flex items-center gap-2 text-destructive">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>App session unavailable</CardTitle>
						</div>
						<CardDescription>
							{launch.error instanceof Error
								? launch.error.message
								: "Bifrost could not restore this isolated app session."}
						</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						<Button onClick={() => launch.refetch()}>Try again</Button>
						<Button variant="outline" onClick={() => navigate("/apps")}>
							Back to apps
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	return (
		<div className="relative h-screen w-screen overflow-hidden bg-background">
			<iframe
				ref={frameRef}
				title={appSlug}
				src={launch.data.launch_url}
				className="h-full w-full border-0 bg-background"
				sandbox="allow-forms allow-scripts"
				referrerPolicy="origin"
				onLoad={() => setDocumentReady(true)}
			/>
			{!documentReady ? (
				<motion.div
					role="status"
					aria-label="Starting isolated app"
					className="absolute inset-0 flex flex-col items-center justify-center gap-5 bg-background p-8 text-center"
					initial={reduceMotion ? false : { opacity: 0 }}
					animate={{ opacity: 1 }}
					exit={reduceMotion ? undefined : { opacity: 0 }}
				>
					<div className="flex items-center gap-3 text-primary" aria-hidden="true">
						<Box className="h-5 w-5" />
						<span className="h-px w-12 bg-border" />
						<ShieldCheck className="h-6 w-6" />
					</div>
					<div>
						<p className="font-medium">Starting your app</p>
						<p className="mt-1 text-sm text-muted-foreground">
							Restoring its secure session and last deployed build.
						</p>
					</div>
				</motion.div>
			) : null}
		</div>
	);
}
