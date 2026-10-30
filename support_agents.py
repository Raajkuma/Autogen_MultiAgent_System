"""
Multi-agent customer support implementation using AutoGen.
Exposes BASE_URL, MODEL_NAME, and handle_query for Streamlit integration.
"""
import os
import re
import sys
from pathlib import Path
from typing import AsyncIterator

import store
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import TaskResult
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent, ToolCallRequestEvent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core.models import ModelFamily
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools

# Module-level exports required by app.py
MODEL_NAME = os.getenv("LLM_MODEL", "ai/qwen3-vl:2B-BF16")
BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:12434/engines/v1")
MCP_SERVER_PATH = str(Path(__file__).with_name("mcp_products_server.py"))


def make_model_client() -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model=MODEL_NAME,
        base_url=BASE_URL,
        api_key="not-needed",
        temperature=0.2,
        model_info={
            "vision": True,
            "function_calling": True,
            "json_output": True,
            "structured_output": False,
            "family": ModelFamily.UNKNOWN,
        },
    )


def lookup_order(order_id: str) -> str:
    """Look up order details, status, and date using an order ID string."""
    o = store.get_order(order_id)
    return str(o) if o else f"Order {order_id} not found."


def cancel_order_tool(order_id: str) -> str:
    """Cancel an active order ONLY if status is 'placed'. Triggers 100% auto-refund."""
    res = store.cancel_order(order_id, actor="Payment_Agent")
    if res["ok"]:
        return f"Order {order_id} has been cancelled and auto-refunded."
    return f"Cancellation failed: {res['error']}"


def request_refund_or_service_tool(order_id: str, reason: str) -> str:
    """Evaluates refund and warranty policies for shipped/delivered orders and queues human approval."""
    res = store.evaluate_refund_policy(order_id, reason)
    if res["ok"]:
        return f"Policy evaluated: {res['tier']}. Request submitted and PENDING HUMAN APPROVAL (ID: {res['approval_id']})."
    return f"Request failed: {res['error']}"


ROUTER_PROMPT = """You are Supervisor. Pick ONE agent:
- SALES: product details, pricing, specs
- PAYMENT: order lookup, cancellation (unshipped orders), or policy refund/service requests (shipped/delivered)
Reply ONLY in this format: ROUTE: <SALES|PAYMENT>"""

REVIEW_PROMPT = """You are Supervisor. Summarize the specialist's outcome clearly for the customer.
If a refund or service request was queued, confirm that it is pending human review/approval. End with TERMINATE."""

SPECIALIST_PROMPTS = {
    "SALES": "You are Sales Agent. Answer product questions using tools.",
    "PAYMENT": "You are Payment Agent. Use lookup_order first. If 'placed', use cancel_order_tool. If 'shipped' or 'delivered', use request_refund_or_service_tool.",
}


def parse_route(text: str) -> str:
    m = re.search(r"ROUTE:\s*(SALES|PAYMENT)", text.upper())
    return m.group(1) if m else "PAYMENT"


async def load_mcp_tools():
    params = StdioServerParams(command=sys.executable, args=[MCP_SERVER_PATH], read_timeout_seconds=30)
    return await mcp_server_tools(params)


def build_specialist(route: str, client, mcp_tools) -> AssistantAgent:
    pick = lambda names: [t for t in mcp_tools if t.name in names]
    tools_by_route = {
        "SALES": pick({"search_products", "get_product", "list_products", "check_stock"}),
        "PAYMENT": [lookup_order, cancel_order_tool, request_refund_or_service_tool],
    }
    return AssistantAgent(
        name=f"{route.capitalize()}_Agent",
        model_client=client,
        tools=tools_by_route[route],
        system_message=SPECIALIST_PROMPTS[route] + "\nBe concise. Do not write TERMINATE.",
    )


def build_task(query: str, history: list[dict]) -> str:
    if not history:
        return query
    recent = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history[-8:])
    return f"Previous conversation history:\n{recent}\n\nCustomer's new message: {query}"


async def handle_query(query: str, history: list[dict], ctx: dict = None) -> AsyncIterator[tuple[str, dict]]:
    """Main entry point consumed by app.py to stream agent execution events."""
    if ctx is None:
        ctx = {}
    task = build_task(query, history)
    client = make_model_client()
    try:
        router = AssistantAgent("Supervisor", model_client=client, system_message=ROUTER_PROMPT)
        res = await router.run(task=task)
        route = parse_route(res.messages[-1].content)
        yield "route", {"route": route}

        mcp_tools = await load_mcp_tools()
        specialist = build_specialist(route, client, mcp_tools)
        reviewer = AssistantAgent("Supervisor", model_client=client, system_message=REVIEW_PROMPT)

        team = RoundRobinGroupChat(
            [specialist, reviewer],
            termination_condition=TextMentionTermination("TERMINATE") | MaxMessageTermination(6),
        )

        final = ""
        async for item in team.run_stream(task=task):
            if isinstance(item, TaskResult):
                break
            if isinstance(item, ToolCallRequestEvent):
                for c in item.content:
                    yield "tool_call", {"source": item.source, "name": c.name, "args": c.arguments}
            elif isinstance(item, ToolCallExecutionEvent):
                for r in item.content:
                    yield "tool_result", {"source": item.source, "name": r.name, "content": r.content}
            elif isinstance(item, TextMessage) and item.source != "user":
                clean_text = item.content.replace("TERMINATE", "").strip()
                yield "message", {"source": item.source, "content": clean_text}
                if item.source == "Supervisor":
                    final = clean_text

        yield "final", {"content": final or "Query processed."}
    finally:
        await client.close()