"""Shared reply extraction + model-priority fallback runner, used by every harness adapter."""
import logging
import os
import re
import time

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

logger = logging.getLogger(__name__)

AMBIGUOUS_RE = re.compile(r"^AMBIGUOUS: Found (.+)\.\s*Clarify", re.DOTALL)

# Tools whose ambiguous match is over item_name; everything else in the ambiguous
# set (khata tools) is ambiguous over customer_name.
_ITEM_NAME_TOOLS = {"check_stock"}


def extract_ambiguous(result: dict) -> dict | None:
    """If the latest tool call returned an AMBIGUOUS string, pull out the tool name,
    its original args, which arg was ambiguous, and the list of candidate names.

    Returns None if no tool call in this turn was ambiguous.
    """
    messages = result.get("messages") or []
    tool_calls_by_id = {}
    for message in messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                tool_calls_by_id[tc["id"]] = tc

    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        match = AMBIGUOUS_RE.match(content.strip())
        if not match:
            continue
        tc = tool_calls_by_id.get(message.tool_call_id)
        if not tc:
            continue
        names_blob = match.group(1).strip().removeprefix("'").removesuffix("'")
        candidates = [n.strip() for n in names_blob.split("' and '") if n.strip()]
        if len(candidates) < 2:
            continue
        field = "item_name" if tc["name"] in _ITEM_NAME_TOOLS else "customer_name"
        return {
            "tool_name": tc["name"],
            "args": dict(tc["args"]),
            "field": field,
            "candidates": candidates,
        }
    return None


ARTIFACT_EXTENSIONS = (".pdf", ".pptx")


def extract_artifact_path(result: dict) -> str | None:
    """If the latest tool call returned a PDF or PPTX file path, pull it out.

    LangChain's final AI text often wraps or paraphrases tool output, so this scans
    ToolMessages directly (newest first) rather than relying on the AI reply.
    """
    messages = result.get("messages") or []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        candidate = content.strip()
        if candidate.startswith("ERROR:"):
            continue
        if candidate.endswith(ARTIFACT_EXTENSIONS) and os.path.isfile(candidate):
            return candidate
    return None


def extract_reply_text(result: dict) -> str:
    """Pull the last AI message text from a Deep Agents / LangGraph result."""
    messages = result.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if not content:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
            joined = "".join(parts).strip()
            if joined:
                return joined
    return "No response generated."


def build_chat_model(provider_name: str, model_name: str):
    """Builds a zero-thinking, cost-capped model for transactional tool orchestration.

    Our tools are single-hop lookups (check_stock / add_to_bill_draft / finalize_bill) —
    no multi-step reasoning needed, so extended thinking only adds latency/cost.
    """
    if provider_name == "anthropic":
        # temperature is deprecated/rejected on newer Claude models (e.g. claude-sonnet-5) —
        # omit it rather than pin a param some models 400 on.
        return ChatAnthropic(
            model=model_name,
            max_tokens=1024,  # prevent conversational token bloat between tool hops
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    # Gemini: disable/minimize thinking. 2.x supports thinking_budget=0 outright;
    # 3.x replaced that with thinking_level, whose lowest setting is "minimal"
    # (no off switch). Gemma models don't support either param, so leave them alone.
    bare = model_name.removeprefix("models/").split(":")[-1]
    kwargs: dict = {"temperature": 0.0}
    if bare.startswith("gemini-3"):
        kwargs["thinking_level"] = "minimal"
    elif bare.startswith("gemini-2.5") or bare.startswith("gemini-2.0"):
        kwargs["thinking_budget"] = 0
    return ChatGoogleGenerativeAI(model=bare, api_key=os.getenv("GOOGLE_API_KEY"), **kwargs)


def build_gemini_model(name: str) -> ChatGoogleGenerativeAI:
    """Thin wrapper kept for any remaining call sites; prefer build_chat_model."""
    return build_chat_model("gemini", name)


class FallbackAgentRunner:
    """Tries each (model_name, compiled_agent) pair in priority order; falls back on any error."""

    def __init__(self, agents: list[tuple[str, object]]):
        if not agents:
            raise ValueError("FallbackAgentRunner needs at least one agent.")
        self.agents = agents

    async def ainvoke(self, messages: list[dict]) -> dict:
        """Returns {"text": final reply, "ambiguous": extract_ambiguous(...) or None, "artifact_path": extract_artifact_path(...) or None}."""
        last_exc: Exception | None = None
        for model_name, agent in self.agents:
            start = time.monotonic()
            try:
                result = await agent.ainvoke({"messages": messages})
                elapsed = time.monotonic() - start
                logger.info("Model %s replied in %.2fs", model_name, elapsed)
                return {
                    "text": extract_reply_text(result),
                    "ambiguous": extract_ambiguous(result),
                    "artifact_path": extract_artifact_path(result),
                }
            except Exception as exc:  # noqa: BLE001 - deliberately broad: any model error triggers fallback
                elapsed = time.monotonic() - start
                logger.warning(
                    "Model %s failed after %.2fs (%s); falling back to next in priority list.",
                    model_name, elapsed, exc,
                )
                last_exc = exc
                continue
        raise RuntimeError(f"All models in priority list failed. Last error: {last_exc}")
