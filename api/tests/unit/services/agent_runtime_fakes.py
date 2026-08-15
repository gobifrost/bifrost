"""Provider-neutral model fakes shared by agent-runtime unit tests."""

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from src.services.llm.base import LLMResponse


class LegacyMockModel(Model):
    """Adapt the existing LLM response fixtures to Pydantic AI's model API."""

    def __init__(self, client):
        super().__init__()
        self.client = client

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def system(self) -> str:
        return "test"

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage()

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response: LLMResponse = await self.client.complete()
        parts = []
        if response.content:
            parts.append(TextPart(response.content))
        parts.extend(
            ToolCallPart(
                tool_name=call.name,
                args=call.arguments,
                tool_call_id=call.id,
            )
            for call in response.tool_calls or []
        )
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(
                input_tokens=response.input_tokens or 0,
                output_tokens=response.output_tokens or 0,
            ),
            model_name=response.model or self.model_name,
            provider_name="test",
            finish_reason="tool_call" if response.tool_calls else "stop",
        )
