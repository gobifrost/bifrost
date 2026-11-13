import {
	Columns3,
	LayoutPanelTop,
	Type,
	Mail,
	Hash,
	ChevronDown,
	CheckSquare,
	TextCursorInput,
	CircleDot,
	Calendar,
	FileText,
	Code,
	Upload,
	type LucideIcon,
} from "lucide-react";
import type { FormField } from "@/lib/client-types";
import { Badge } from "@/components/ui/badge";
const structuralSection =
	"border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300";
const structuralRow =
	"border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300";
const input =
	"border-teal-500/40 bg-teal-500/10 text-teal-700 dark:text-teal-300";
const choice =
	"border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300";
const content =
	"border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300";
type ComponentType = { label: string; icon: LucideIcon; color: string };
export const FORM_COMPONENT_TYPES: Record<
	FormField["type"] | "section" | "row",
	ComponentType
> &
	Record<string, ComponentType> = {
	section: {
		label: "Section",
		icon: LayoutPanelTop,
		color: structuralSection,
	},
	row: { label: "Row", icon: Columns3, color: structuralRow },
	text: { label: "Text Input", icon: Type, color: input },
	email: { label: "Email", icon: Mail, color: input },
	number: { label: "Number", icon: Hash, color: input },
	select: { label: "Dropdown", icon: ChevronDown, color: choice },
	multi_select: { label: "Multi Select", icon: ChevronDown, color: choice },
	checkbox: { label: "Checkbox", icon: CheckSquare, color: choice },
	radio: { label: "Radio Buttons", icon: CircleDot, color: choice },
	textarea: { label: "Text Area", icon: TextCursorInput, color: input },
	date: { label: "Date", icon: Calendar, color: input },
	datetime: { label: "Date & Time", icon: Calendar, color: input },
	markdown: { label: "Markdown", icon: FileText, color: content },
	html: { label: "HTML Content", icon: Code, color: content },
	file: { label: "File Upload", icon: Upload, color: content },
};
export function ComponentTypeBadge({ type }: { type: string }) {
	const { label, icon: Icon, color } = FORM_COMPONENT_TYPES[type]!;
	return (
		<Badge variant="outline" className={`gap-1 ${color}`}>
			<Icon className="size-3" aria-hidden="true" />
			{label}
		</Badge>
	);
}
