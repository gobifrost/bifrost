import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface GenerationModelOption {
	id: string;
	display_name: string;
	output_modalities?: string[] | null;
}

interface GenerationModelSettingsProps {
	models: GenerationModelOption[];
	imageModel: string;
	videoModel: string;
	onImageModelChange: (model: string) => void;
	onVideoModelChange: (model: string) => void;
}

export function GenerationModelSettings({
	models,
	imageModel,
	videoModel,
	onImageModelChange,
	onVideoModelChange,
}: GenerationModelSettingsProps) {
	const hasCapabilityCatalog = models.some(
		(model) => model.output_modalities != null,
	);
	const optionsFor = (modality: "image" | "video") =>
		models
			.filter((model) => model.output_modalities?.includes(modality))
			.map((model) => ({
				value: model.id,
				label: model.display_name,
				description:
					model.id !== model.display_name ? model.id : undefined,
			}));

	const fields = [
		{
			id: "image-generation-model",
			label: "Image Generation Model",
			placeholder: "Optional image model",
			value: imageModel,
			onChange: onImageModelChange,
			options: optionsFor("image"),
			emptyText: "No image generation models reported.",
		},
		{
			id: "video-generation-model",
			label: "Video Generation Model",
			placeholder: "Optional video model",
			value: videoModel,
			onChange: onVideoModelChange,
			options: optionsFor("video"),
			emptyText: "No video generation models reported.",
		},
	];

	return (
		<div className="space-y-3 rounded-lg border p-4">
			<div>
				<h5 className="text-sm font-medium">Generation Models</h5>
				<p className="mt-1 text-xs text-muted-foreground">
					Reserve dedicated provider models for image and video
					generation. Leave either blank when that generator is
					unavailable.
				</p>
			</div>
			<div className="grid gap-3 sm:grid-cols-2">
				{fields.map((field) => {
					return (
						<div key={field.id} className="space-y-1">
							<Label htmlFor={field.id}>{field.label}</Label>
							{hasCapabilityCatalog ? (
								<Combobox
									id={field.id}
									value={field.value}
									onValueChange={field.onChange}
									placeholder={field.placeholder}
									searchPlaceholder="Search models..."
									emptyText={field.emptyText}
									options={field.options}
								/>
							) : (
								<Input
									id={field.id}
									value={field.value}
									onChange={(event) =>
										field.onChange(event.target.value)
									}
									placeholder={field.placeholder}
								/>
							)}
						</div>
					);
				})}
			</div>
		</div>
	);
}
