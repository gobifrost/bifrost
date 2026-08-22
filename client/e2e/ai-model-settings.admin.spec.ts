import { expect, test } from "@playwright/test";

test.describe("AI model settings", () => {
	test("configures generation models and inspects compact Chat capabilities", async ({
		page,
	}, testInfo) => {
		const capabilities = {
			image_input: false,
			pdf_input: false,
			tool_calling: true,
			source: "openrouter",
			fingerprint: "openrouter-model",
		};
		let savedBody: Record<string, unknown> | null = null;
		let config = {
			provider: "openai",
			model: "deepseek/deepseek-v4-pro",
			endpoint: "https://openrouter.ai/api/v1",
			max_tokens: 16384,
			default_system_prompt: null,
			summarization_model: null,
			tuning_model: null,
			image_generation_model: null as string | null,
			video_generation_model: null as string | null,
			chat_fast_label: "Fast",
			chat_fast_model: "fast-model",
			chat_balanced_label: "Balanced",
			chat_balanced_model: "deepseek/deepseek-v4-pro",
			chat_pro_label: "Pro",
			chat_pro_model: "pro-model",
			chat_fast_capabilities: capabilities,
			chat_balanced_capabilities: capabilities,
			chat_pro_capabilities: capabilities,
			is_configured: true,
			api_key_set: true,
		};

		await page.route("**/api/admin/llm/config", async (route) => {
			if (route.request().method() === "POST") {
				savedBody = route.request().postDataJSON() as Record<
					string,
					unknown
				>;
				config = {
					...config,
					image_generation_model: String(
						savedBody.image_generation_model,
					),
					video_generation_model: String(
						savedBody.video_generation_model,
					),
				};
			}
			await route.fulfill({ json: config });
		});
		await page.route("**/api/admin/llm/test-saved", async (route) => {
			await route.fulfill({
				json: {
					success: true,
					message: "Connected",
					models: [
						{
							id: "deepseek/deepseek-v4-pro",
							display_name: "DeepSeek V4 Pro",
							output_modalities: ["text"],
						},
						{
							id: "google/nano-banana",
							display_name: "Nano Banana",
							output_modalities: ["image"],
						},
						{
							id: "video-model",
							display_name: "Video Model",
							output_modalities: ["video"],
						},
					],
				},
			});
		});

		await page.goto("/settings/ai");
		await expect(page.getByText("Chat Model Choices")).toBeVisible();
		const fastTier = page.getByRole("group", { name: "Fast" });
		await expect(fastTier.getByText("Fast", { exact: true })).toBeVisible();
		await expect(
			fastTier.getByText("Model", { exact: true }),
		).toBeVisible();
		await expect(
			fastTier.getByText("OpenRouter", { exact: true }),
		).toBeVisible();
		await expect(fastTier.getByText("Capabilities")).toHaveCount(0);

		const unsupportedImage = page
			.getByRole("button", {
				name: "Image Input: Not Supported",
			})
			.first();
		await expect(unsupportedImage).toHaveClass(/text-red-600/);
		await expect(
			page
				.getByRole("button", { name: "Tool Calling: Supported" })
				.first(),
		).toHaveClass(/text-green-600/);
		await unsupportedImage.hover();
		await expect(
			page.getByText("Not Supported · OpenRouter"),
		).toBeVisible();

		const imageModel = page.getByRole("combobox", {
			name: "Image Generation Model",
		});
		await imageModel.click();
		await page.getByPlaceholder("Search models...").fill("banana");
		const imageOption = page.getByRole("option", { name: /Nano Banana/ });
		await expect(
			page.getByRole("option", { name: /DeepSeek V4 Pro/ }),
		).toHaveCount(0);
		await imageOption.click();
		await expect(imageOption).toBeHidden();
		const videoModel = page.getByRole("combobox", {
			name: "Video Generation Model",
		});
		await videoModel.click();
		const videoOption = page.getByRole("option", { name: "Video Model" });
		await videoOption.click();
		await expect(videoOption).toBeHidden();

		const summarizationModel = page.getByRole("combobox", {
			name: "Summarization Model (Optional)",
		});
		await summarizationModel.click();
		const summarizationOption = page.getByRole("option", {
			name: "DeepSeek V4 Pro",
		});
		await summarizationOption.click();
		await expect(summarizationOption).toBeHidden();
		const tuningModel = page.getByRole("combobox", {
			name: "Tuning Model (Optional)",
		});
		await tuningModel.click();
		const tuningOption = page.getByRole("option", {
			name: "DeepSeek V4 Pro",
		});
		await tuningOption.click();
		await expect(tuningOption).toBeHidden();

		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.getByText("Chat Model Choices").scrollIntoViewIfNeeded();
		await testInfo.attach("AI settings — Model routing", {
			body: await page.screenshot(),
			contentType: "image/png",
		});
		await page.getByRole("button", { name: "Save Configuration" }).click();
		await expect.poll(() => savedBody).not.toBeNull();
		expect(savedBody).toMatchObject({
			image_generation_model: "google/nano-banana",
			video_generation_model: "video-model",
			summarization_model: "deepseek/deepseek-v4-pro",
			tuning_model: "deepseek/deepseek-v4-pro",
		});
	});
});
