import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { SourceChangesSection } from "./SourceChangesSection";

it("retains cached records on failure and keeps retry separate from disclosure", async () => {
	const user = userEvent.setup();
	const onRetryLoad = vi.fn();
	render(
		<SourceChangesSection
			syncError={null}
			hasLoadError
			onRetryLoad={onRetryLoad}
			changedFiles={[
				{ path: "workflows/retained.py", change_type: "modified" },
			]}
			conflicts={[]}
			conflictResolutions={{}}
			onShowConflictDiff={vi.fn()}
			onResolveConflict={vi.fn()}
			commitMessage=""
			onCommitMessageChange={vi.fn()}
			onCommit={vi.fn()}
			onCompleteMerge={vi.fn()}
			allConflictsResolved={false}
			onSync={vi.fn()}
			onShowDiff={vi.fn()}
			onDiscardFiles={vi.fn().mockResolvedValue(undefined)}
			commitsBehind={0}
			commitsAhead={0}
			needsSync={false}
			loading={null}
			disabled={false}
			branch="main"
		/>,
	);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Previously loaded files",
	);
	expect(
		screen.getByRole("article", {
			name: "Changed file workflows/retained.py",
		}),
	).toBeVisible();
	expect(
		screen.queryByText("No uncommitted changes"),
	).not.toBeInTheDocument();
	expect(screen.getByRole("button", {name:"Discard all changes"})).toBeDisabled();
	expect(screen.getByRole("button", {name:"Discard changes to workflows/retained.py"})).toBeDisabled();
	expect(screen.getByRole("button", {name:"View changes for workflows/retained.py"})).toBeEnabled();

	await user.click(
		screen.getByRole("button", { name: "Retry loading changes" }),
	);
	expect(onRetryLoad).toHaveBeenCalledTimes(1);
	const disclosure = screen.getByRole("button", { name: "Changes 1" });
	await user.click(disclosure);
	expect(disclosure).toHaveAttribute("aria-expanded", "false");
	expect(screen.queryByRole("article")).not.toBeInTheDocument();
	await user.click(disclosure);
	expect(screen.getByRole("article")).toBeVisible();
});

it("offers publication retry for a durable sync failure", async () => {
	const user = userEvent.setup();
	const onRetryPublication = vi.fn();
	render(
		<SourceChangesSection
			syncError="remote unavailable"
			hasLoadError={false}
			onRetryLoad={vi.fn()}
			changedFiles={[]}
			conflicts={[]}
			conflictResolutions={{}}
			onShowConflictDiff={vi.fn()}
			onResolveConflict={vi.fn()}
			commitMessage=""
			onCommitMessageChange={vi.fn()}
			onCommit={vi.fn()}
			onCompleteMerge={vi.fn()}
			allConflictsResolved={false}
			onSync={vi.fn()}
			onShowDiff={vi.fn()}
			onDiscardFiles={vi.fn().mockResolvedValue(undefined)}
			commitsBehind={0}
			commitsAhead={0}
			needsSync={false}
			loading={null}
			disabled={false}
			branch="main"
			onRetryPublication={onRetryPublication}
		/>,
	);

	await user.click(screen.getByRole("button", { name: "Retry publication" }));

	expect(onRetryPublication).toHaveBeenCalledTimes(1);
});
