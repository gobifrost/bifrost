import { lazyWithReload } from "@/lib/lazy-with-reload";

export const preloadRunFormPage = () =>
	import("@/pages/RunForm").then((module) => ({ default: module.RunForm }));

export const RunFormRoute = lazyWithReload(preloadRunFormPage);
