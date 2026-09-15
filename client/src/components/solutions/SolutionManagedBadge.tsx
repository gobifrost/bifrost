/**
 * SolutionManagedBadge
 *
 * A compact lock icon shown on solution-managed entity cards/rows.
 * ADMIN-ONLY (platform admins) — a non-admin sees nothing. Links to the owning
 * Solution's detail view so an operator can jump from any managed entity to the
 * install that owns it. Read-only ENFORCEMENT is server-side regardless; this is
 * purely the operator affordance.
 */
import { Lock } from "lucide-react";
import { Link } from "react-router-dom";

import { useAuth } from "@/contexts/AuthContext";

export interface SolutionManagedBadgeProps {
	/** The owning Solution install id (from the entity's solution_id). */
	solutionId: string | null | undefined;
}

export function SolutionManagedBadge({
	solutionId,
}: SolutionManagedBadgeProps) {
	const { isPlatformAdmin } = useAuth();
	if (!isPlatformAdmin || !solutionId) return null;
	return (
		<Link
			to={`/solutions/${solutionId}`}
			className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] text-muted-foreground transition-colors motion-reduce:transition-none hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
			aria-label="Managed by a Solution"
			title="Managed by a Solution — read-only on the platform. Click to view the Solution."
			data-testid="solution-managed-badge"
			onClick={(e) => e.stopPropagation()}
		>
			<Lock aria-hidden="true" className="h-4 w-4" />
		</Link>
	);
}

export default SolutionManagedBadge;
