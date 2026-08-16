import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { GenerationModelSettings } from "./GenerationModelSettings";

describe("GenerationModelSettings", () => {
	it("presents separately configurable image and video models", async () => {
		const onImageModelChange = vi.fn();
		const onVideoModelChange = vi.fn();
		const { user } = renderWithProviders(
			<GenerationModelSettings
				models={[]}
				imageModel=""
				videoModel=""
				onImageModelChange={onImageModelChange}
				onVideoModelChange={onVideoModelChange}
			/>,
		);

		const imageInput = screen.getByRole("textbox", {
			name: "Image Generation Model",
		});
		const videoInput = screen.getByRole("textbox", {
			name: "Video Generation Model",
		});
		await user.type(imageInput, "image-model");
		await user.type(videoInput, "video-model");

		expect(onImageModelChange).toHaveBeenCalled();
		expect(onVideoModelChange).toHaveBeenCalled();
		expect(
			screen.getByText("Generation Models").querySelector("svg"),
		).toBeNull();
		expect(
			screen
				.getByText("Image Generation Model")
				.closest("label")
				?.querySelector("svg"),
		).toBeNull();
		expect(
			screen
				.getByText("Video Generation Model")
				.closest("label")
				?.querySelector("svg"),
		).toBeNull();
	});

	it("only offers models whose catalog output matches each generator", async () => {
		const { user } = renderWithProviders(
			<GenerationModelSettings
				models={[
					{
						id: "google/gemini-2.5-flash-image",
						display_name: "Nano Banana",
						output_modalities: ["text", "image"],
					},
					{
						id: "openai/sora",
						display_name: "Sora",
						output_modalities: ["video"],
					},
					{
						id: "deepseek/deepseek-v4-pro",
						display_name: "DeepSeek V4 Pro",
						output_modalities: ["text"],
					},
				]}
				imageModel=""
				videoModel=""
				onImageModelChange={vi.fn()}
				onVideoModelChange={vi.fn()}
			/>,
		);

		await user.click(
			screen.getByRole("combobox", { name: "Image Generation Model" }),
		);
		expect(
			screen.getByRole("option", { name: /Nano Banana/ }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: /DeepSeek V4 Pro/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: /Sora/ }),
		).not.toBeInTheDocument();
	});
});
