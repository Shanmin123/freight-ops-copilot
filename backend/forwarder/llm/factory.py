"""build_agent_model: the chat model agent runs use, picked from the settings."""

from forwarder.config import Settings

from .base import ChatModel
from .mock_demo import DemoMockModel
from .openai_compat import OpenAICompatModel


def build_agent_model(settings: Settings) -> ChatModel:
    """Use the API or a test provider explicitly injected by a test runner."""
    if settings.llm_provider == "mock":
        return DemoMockModel()
    if settings.llm_provider != "deepseek" or not settings.deepseek_api_key:
        raise ValueError("DeepSeek API 配置不完整。")
    return OpenAICompatModel(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        model=settings.agent_model,
        thinking=settings.agent_thinking,
    )
