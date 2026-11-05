import { IntegrationTestResult } from "./IntegrationTestResult";
import { Loader2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { type IntegrationTestResponse } from "@/services/integrations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";

export interface IntegrationTestPanelProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	testOrgId: string | null;
	onTestOrgIdChange: (value: string | null) => void;
	testEndpoint: string;
	onTestEndpointChange: (value: string) => void;
	testResult: IntegrationTestResponse | null;
	onClearResult: () => void;
	onTest: () => void;
	isTestPending: boolean;
	canTestAt: (organizationId: string | null) => boolean;
	testableOrganizations: Array<{ id: string; name: string }>;
}

export function IntegrationTestPanel({
	open,
	onOpenChange,
	testOrgId,
	onTestOrgIdChange,
	testEndpoint,
	onTestEndpointChange,
	testResult,
	onClearResult,
	onTest,
	isTestPending,
	canTestAt,
	testableOrganizations,
}: IntegrationTestPanelProps) {
	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!isTestPending) onOpenChange(next);
			}}
		>
			<DialogContent className="max-w-md">
				<DialogHeader>
					<DialogTitle>Test Integration Connection</DialogTitle>
					<DialogDescription>
						Test connectivity by making a GET request to the
						specified endpoint.
					</DialogDescription>
				</DialogHeader>
				<fieldset
					disabled={isTestPending}
					className="min-w-0 space-y-4 py-4"
				>
					<div className="space-y-2">
						<Label htmlFor="test-org">Organization</Label>
						<OrganizationSelect
							id="test-org"
							disabled={isTestPending}
							value={testOrgId}
							onChange={(value) => {
								// OrganizationSelect uses undefined for "All", but we only care about null (Global) or string (org)
								onTestOrgIdChange(
									value === undefined ? null : value,
								);
								onClearResult();
							}}
							showGlobal={canTestAt(null)}
							showAll={false}
							knownOrganizations={testableOrganizations}
							loadOrganizations={false}
							filterOrganizations={(organization) =>
								canTestAt(organization.id)
							}
							placeholder="Select organization..."
						/>
						<p className="text-sm text-muted-foreground">
							{canTestAt(null)
								? "Select Global to test with integration defaults only, or choose an organization to test with merged config and OAuth."
								: "Choose an organization to test with its merged configuration and OAuth."}
						</p>
					</div>

					<div className="space-y-2">
						<Label htmlFor="test-endpoint">Endpoint</Label>
						<Input
							id="test-endpoint"
							className="min-h-11"
							value={testEndpoint}
							onChange={(e) => {
								onTestEndpointChange(e.target.value);
								onClearResult();
							}}
							placeholder="/api/users"
						/>
						<p className="text-sm text-muted-foreground">
							API endpoint path to test. Will be appended to the
							integration's base_url.
						</p>
					</div>
				</fieldset>
				{testResult && <IntegrationTestResult result={testResult} />}
				<DialogFooter>
					<Button
						className="min-h-11"
						type="button"
						variant="outline"
						disabled={isTestPending}
						onClick={() => onOpenChange(false)}
					>
						Close
					</Button>
					<Button
						className="min-h-11"
						onClick={onTest}
						disabled={isTestPending || !canTestAt(testOrgId)}
					>
						{isTestPending ? (
							<>
								<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
								Testing...
							</>
						) : (
							<>
								<Zap className="h-4 w-4 mr-2" />
								Test
							</>
						)}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
