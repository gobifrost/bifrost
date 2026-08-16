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
			screen
				.getByText("Image Generation Model")
				.closest("label")
				?.querySelector("svg"),
		).toHaveClass("text-violet-500");
		expect(
			screen
				.getByText("Video Generation Model")
				.closest("label")
				?.querySelector("svg"),
		).toHaveClass("text-rose-500");
	});
});
