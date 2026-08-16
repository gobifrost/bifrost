import { Image as ImageIcon, Video, WandSparkles } from "lucide-react";

import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface GenerationModelOption {
	id: string;
	display_name: string;
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
	const options = models.map((model) => ({
		value: model.id,
		label: model.display_name,
		description: model.id !== model.display_name ? model.id : undefined,
	}));

	const fields = [
		{
			id: "image-generation-model",
			label: "Image Generation Model",
			placeholder: "Optional image model",
			value: imageModel,
			onChange: onImageModelChange,
			icon: ImageIcon,
			iconClassName: "text-violet-500",
		},
		{
			id: "video-generation-model",
			label: "Video Generation Model",
			placeholder: "Optional video model",
			value: videoModel,
			onChange: onVideoModelChange,
			icon: Video,
			iconClassName: "text-rose-500",
		},
	];

	return (
		<div className="space-y-3 rounded-lg border p-4">
			<div>
				<h5 className="flex items-center gap-2 text-sm font-medium">
					<WandSparkles className="h-4 w-4 text-violet-500" />
					Generation Models
				</h5>
				<p className="mt-1 text-xs text-muted-foreground">
					Reserve dedicated provider models for image and video
					generation. Leave either blank when that generator is
					unavailable.
				</p>
			</div>
			<div className="grid gap-3 sm:grid-cols-2">
				{fields.map((field) => {
					const Icon = field.icon;
					return (
						<div key={field.id} className="space-y-1">
							<Label
								htmlFor={field.id}
								className="flex items-center gap-1.5"
							>
								<Icon
									className={`h-3.5 w-3.5 ${field.iconClassName}`}
								/>
								{field.label}
							</Label>
							{models.length > 0 ? (
								<Combobox
									id={field.id}
									value={field.value}
									onValueChange={field.onChange}
									placeholder={field.placeholder}
									searchPlaceholder="Search models..."
									emptyText="No models found."
									options={options}
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
