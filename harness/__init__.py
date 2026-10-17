"""Harness registry — swap agent SDKs via the AGENT_HARNESS env var without deleting code."""
from harness import langchain_harness
from harness.base import FallbackAgentRunner

HARNESS_BUILDERS = {
    "langchain": langchain_harness.build,
}


def build_harness(
    name: str, tools: list, system_prompt: str, model_priority: list[str], provider: str = "gemini"
) -> FallbackAgentRunner:
    builder = HARNESS_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"Unknown AGENT_HARNESS={name!r}. Options: {sorted(HARNESS_BUILDERS)}")
    return builder(tools, system_prompt, model_priority, provider=provider)


__all__ = ["build_harness", "HARNESS_BUILDERS", "FallbackAgentRunner"]
