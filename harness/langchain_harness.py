"""Plain LangChain create_agent harness — only the tools we pass, no fs/execute/subagent bloat."""
from langchain.agents import create_agent

from harness.base import FallbackAgentRunner, build_chat_model


def build(tools: list, system_prompt: str, model_priority: list[str], provider: str = "gemini") -> FallbackAgentRunner:
    agents = [
        (model_name, create_agent(build_chat_model(provider, model_name), tools=tools, system_prompt=system_prompt))
        for model_name in model_priority
    ]
    return FallbackAgentRunner(agents)
