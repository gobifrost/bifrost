import {
	createContext,
	useCallback,
	useContext,
	useMemo,
	useState,
	useEffect,
	useRef,
	ReactNode,
} from "react";
import { initializeBranding, applyBrandingTheme } from "@/lib/branding";
import {
	DEFAULT_TERMINOLOGY,
	mergeTerminology,
	TerminologyContext,
	type Terminology,
} from "@/lib/terminology";
import {
	getImpersonationTokenClaims,
	isImpersonating,
} from "@/lib/auth-token";

export interface OrgScope {
	type: "global" | "organization";
	orgId: string | null;
	orgName: string | null;
}

interface OrgScopeContextType {
	scope: OrgScope;
	setScope: (scope: OrgScope) => void;
	isGlobalScope: boolean;
	brandingLoaded: boolean;
	logoLoaded: boolean;
	squareLogoUrl: string | null;
	rectangleLogoUrl: string | null;
	applicationName: string | null;
	terminology: Terminology;
	refreshBranding: () => void;
}

const OrgScopeContext = createContext<OrgScopeContextType | undefined>(
	undefined,
);

const SCOPE_STORAGE_KEY = "msp-automation-org-scope";

export function OrgScopeProvider({ children }: { children: ReactNode }) {
	const impersonationScope = useMemo<OrgScope | null>(() => {
		const impersonationClaims = getImpersonationTokenClaims();
		if (!isImpersonating()) return null;
		return impersonationClaims?.org_id
			? {
					type: "organization",
					orgId: impersonationClaims.org_id,
					orgName: null,
				}
			: { type: "global", orgId: null, orgName: null };
	}, []);
	const [scope, setScope] = useState<OrgScope>(() => {
		if (impersonationScope) return impersonationScope;
		// Load from localStorage on init
		const stored = localStorage.getItem(SCOPE_STORAGE_KEY);
		if (stored) {
			try {
				return JSON.parse(stored);
			} catch {
				// Failed to parse stored scope, using default
			}
		}
		return { type: "global", orgId: null, orgName: null };
	});

	const [brandingLoaded, setBrandingLoaded] = useState(false);
	const [logoLoaded, setLogoLoaded] = useState(false);
	const [squareLogoUrl, setSquareLogoUrl] = useState<string | null>(null);
	const [rectangleLogoUrl, setRectangleLogoUrl] = useState<string | null>(
		null,
	);
	const [applicationName, setApplicationName] = useState<string | null>(null);
	const [terminology, setTerminology] =
		useState<Terminology>(DEFAULT_TERMINOLOGY);
	const [refreshTrigger, setRefreshTrigger] = useState(0);

	// Function to trigger a branding refresh
	const refreshBranding = useCallback(() => {
		setRefreshTrigger((prev) => prev + 1);
	}, []);

	// A support session must use the target's home organization while leaving
	// the operator's shared, cross-tab choice untouched.
	useEffect(() => {
		if (!impersonationScope) {
			localStorage.setItem(SCOPE_STORAGE_KEY, JSON.stringify(scope));
		}
	}, [impersonationScope, scope]);

	const setScopedScope = useCallback(
		(next: OrgScope) => {
			if (!impersonationScope) setScope(next);
		},
		[impersonationScope],
	);

	// Track current loading request to avoid race conditions
	const loadingRequestRef = useRef(0);

	// Initialize branding when scope changes or on mount
	useEffect(() => {
		const requestId = ++loadingRequestRef.current;

		async function loadBrandingAndLogo() {
			// Keep the current page and branding mounted during refreshes.
			// Initial state already gates the first load; clearing it here loses drafts.

			try {
				// Fetch branding data (public endpoint, always GLOBAL)
				const response = await fetch("/api/branding");

				// Check if this request is still current
				if (loadingRequestRef.current !== requestId) return;

				if (!response.ok) {
					// Fallback to default branding
					await initializeBranding();
					if (loadingRequestRef.current !== requestId) return;
					setBrandingLoaded(true);
					setLogoLoaded(true);
					return;
				}

				const branding = await response.json();
				const rectUrl = branding.rectangle_logo_url;
				const sqUrl = branding.square_logo_url;
				const nextTerminology = mergeTerminology(branding.terminology);

				// Preload both logos if they exist
				const preloadPromises: Promise<void>[] = [];

				if (rectUrl) {
					preloadPromises.push(
						new Promise<void>((resolve) => {
							const img = new Image();
							img.onload = () => resolve();
							img.onerror = () => resolve(); // Continue even on error
							img.src = rectUrl;
							// Timeout after 5 seconds
							setTimeout(() => resolve(), 5000);
						}),
					);
				}

				if (sqUrl) {
					preloadPromises.push(
						new Promise<void>((resolve) => {
							const img = new Image();
							img.onload = () => resolve();
							img.onerror = () => resolve();
							img.src = sqUrl;
							setTimeout(() => resolve(), 5000);
						}),
					);
				}

				// Wait for all logos to preload
				await Promise.all(preloadPromises);

				// Check if this request is still current
				if (loadingRequestRef.current !== requestId) return;

				// Store logo URLs in context
				setSquareLogoUrl(sqUrl || null);
				setRectangleLogoUrl(rectUrl || null);
				setApplicationName(branding.application_name || null);
				setTerminology(nextTerminology);

				// Apply branding theme (colors to CSS) - no need to fetch again
				applyBrandingTheme(branding);

				// Mark everything as loaded - UI can now render
				setBrandingLoaded(true);
				setLogoLoaded(true);
			} catch {
				// Check if this request is still current
				if (loadingRequestRef.current !== requestId) return;
				// Apply default branding on error
				await initializeBranding();
				if (loadingRequestRef.current !== requestId) return;
				setBrandingLoaded(true);
				setLogoLoaded(true);
			}
		}

		loadBrandingAndLogo();
	}, [scope.orgId, refreshTrigger]);

	const isGlobalScope = scope.type === "global";

	const value = useMemo(
		() => ({
			scope,
			setScope: setScopedScope,
			isGlobalScope,
			brandingLoaded,
			logoLoaded,
			squareLogoUrl,
			rectangleLogoUrl,
			applicationName,
			terminology,
			refreshBranding,
		}),
		[scope, setScopedScope, isGlobalScope, brandingLoaded, logoLoaded, squareLogoUrl, rectangleLogoUrl, applicationName, terminology, refreshBranding],
	);

	return (
		<OrgScopeContext.Provider value={value}>
			<TerminologyContext.Provider value={terminology}>
				{children}
			</TerminologyContext.Provider>
		</OrgScopeContext.Provider>
	);
}

export function useOrgScope() {
	const context = useContext(OrgScopeContext);
	if (!context) {
		throw new Error("useOrgScope must be used within OrgScopeProvider");
	}
	return context;
}
