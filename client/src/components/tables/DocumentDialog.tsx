import { useState, useCallback, useMemo, useRef } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";
import {
	useInsertDocument,
	useUpdateDocument,
	type DocumentPublic,
} from "@/services/tables";

interface DocumentDialogProps {
	document?: DocumentPublic | undefined;
	tableId: string;
	open: boolean;
	onClose: () => void;
}

/**
 * Inner dialog content component
 * Separate component so we can use key to force remount when document changes,
 * ensuring the editor initializes with the correct value.
 */
function DocumentDialogInner({
	document,
	tableId,
	onClose,
}: Omit<DocumentDialogProps, "open">) {
	const insertDocument = useInsertDocument();
	const updateDocument = useUpdateDocument();
	const isEditing = !!document;
	const { theme } = useTheme();
	const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);

	// Compute initial value from document prop - only computed once on mount
	const initialValue = useMemo(() => {
		if (document) {
			return JSON.stringify(document.data, null, 2);
		}
		return "{\n  \n}";
	}, [document]);

	const [jsonValue, setJsonValue] = useState(initialValue);
	const [jsonError, setJsonError] = useState<string | null>(null);

	const validateJson = useCallback((value: string): boolean => {
		try {
			JSON.parse(value);
			setJsonError(null);
			return true;
		} catch (e) {
			setJsonError(e instanceof Error ? e.message : "Invalid JSON");
			return false;
		}
	}, []);

	const handleJsonChange = useCallback(
		(value: string | undefined) => {
			const newValue = value ?? "";
			setJsonValue(newValue);
			if (newValue.trim()) {
				validateJson(newValue);
			} else {
				setJsonError(null);
			}
		},
		[validateJson],
	);

	const handleEditorMount: OnMount = (editor) => {
		editorRef.current = editor;
		// Focus the editor when mounted
		editor.focus();
	};

	const handleSubmit = async () => {
		if (!validateJson(jsonValue)) {
			return;
		}

		const data = JSON.parse(jsonValue);

		if (isEditing && document) {
			await updateDocument.mutateAsync({
				params: {
					path: { table_id: tableId, doc_id: document.id },
				},
				body: { data },
			});
		} else {
			await insertDocument.mutateAsync({
				params: {
					path: { table_id: tableId },
				},
				body: { data, upsert: false },
			});
		}
		onClose();
	};

	const isPending = insertDocument.isPending || updateDocument.isPending;

	const formatJson = useCallback(() => {
		if (editorRef.current) {
			editorRef.current.getAction("editor.action.formatDocument")?.run();
		}
	}, []);

	const monacoTheme = theme === "light" ? "vs" : "vs-dark";

	return (
		<>
			<DialogHeader>
				<DialogTitle>
					{isEditing ? "Edit Document" : "Create Document"}
				</DialogTitle>
				<DialogDescription>
					{isEditing
						? "Update the document data (will merge with existing)"
						: "Add a new document to this table"}
				</DialogDescription>
			</DialogHeader>

			<div className="flex-1 space-y-4 overflow-hidden min-h-0">
				<div className="flex items-center justify-between">
					<Label>Document Data (JSON)</Label>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={formatJson}
					>
						Format
					</Button>
				</div>

				<div className="border rounded-md overflow-hidden h-[400px]">
					<Editor
						height="100%"
						language="json"
						value={jsonValue}
						onChange={handleJsonChange}
						onMount={handleEditorMount}
						theme={monacoTheme}
						options={{
							minimap: { enabled: false },
							scrollBeyondLastLine: false,
							fontSize: 13,
							fontFamily:
								"ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
							wordWrap: "on",
							automaticLayout: true,
							tabSize: 2,
							insertSpaces: true,
							formatOnPaste: true,
							autoClosingBrackets: "always",
							autoClosingQuotes: "always",
							bracketPairColorization: { enabled: true },
							folding: true,
							foldingStrategy: "indentation",
							lineNumbers: "on",
							renderWhitespace: "selection",
							quickSuggestions: false,
							suggestOnTriggerCharacters: false,
							padding: { top: 8, bottom: 8 },
						}}
						loading={
							<div className="flex h-full items-center justify-center text-sm text-muted-foreground">
								Loading editor...
							</div>
						}
					/>
				</div>

				{jsonError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{jsonError}</AlertDescription>
					</Alert>
				)}
			</div>

			<DialogFooter>
				<Button type="button" variant="outline" onClick={onClose}>
					Cancel
				</Button>
				<Button
					onClick={handleSubmit}
					disabled={isPending || !!jsonError}
				>
					{isPending ? "Saving..." : isEditing ? "Update" : "Create"}
				</Button>
			</DialogFooter>
		</>
	);
}

/**
 * Document Dialog Component
 *
 * Dialog for creating and editing documents in a table.
 * Uses a key-based remount strategy to ensure the editor always
 * shows the correct initial value when switching documents.
 */
export function DocumentDialog({
	document,
	tableId,
	open,
	onClose,
}: DocumentDialogProps) {
	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (!isOpen) {
				onClose();
			}
		},
		[onClose],
	);

	// Generate a key based on document ID and open state
	// This forces a remount of the inner component when switching documents
	const dialogKey = `${document?.id ?? "new"}-${open}`;

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent className="sm:max-w-[700px] max-h-[85vh] flex flex-col">
				<DocumentDialogInner
					key={dialogKey}
					document={document}
					tableId={tableId}
					onClose={onClose}
				/>
			</DialogContent>
		</Dialog>
	);
}
