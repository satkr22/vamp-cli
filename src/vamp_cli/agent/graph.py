from typing import Literal

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.state import AgentState
from vamp_cli.agent.nodes import (
    create_agent_node,
    create_tool_node,
    create_planner_node,
)


def route_start(
    state: AgentState,
) -> Literal["agent", "planner"]:

    if state["mode"] == "plan":
        return "planner"

    return "agent"


def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    
    if state["mode"] == "plan" and not state.get("is_plan_injected", False):
        message_type = "planner_messages"
    else:
        message_type = "coder_messages"

    last_message = state[message_type][-1]

    if not last_message.tool_calls: # type: ignore
        return "__end__"

    return "tools"


def route_after_planner(state):
    
    last = state["planner_messages"][-1]
    if isinstance(last, ToolMessage):
        last = state["planner_messages"][-2]
    
    if last.tool_calls and not any(
        tc["name"] == "submit_plan" for tc in last.tool_calls
    ):
        return "tools"

    if state.get("requires_execution"):
        return "coder"

    return "__end__"


def create_graph(
    coder_model,
    planner_model,
    tool_registry: ToolRegistry,
):

    coder_node = create_agent_node(coder_model)

    planner_node = create_planner_node(planner_model)

    coder_tool_node = create_tool_node(tool_registry)
    planner_tool_node = create_tool_node(tool_registry)

    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("coder", coder_node)
    graph.add_node("planner", planner_node)
    graph.add_node("coder_tools", coder_tool_node)
    graph.add_node("planner_tools", planner_tool_node)

    # --------------------------------------------------
    # START
    # --------------------------------------------------

    graph.add_conditional_edges(
        START,
        route_start,
        {
            "agent": "coder",
            "planner": "planner",
        },
    )

    # --------------------------------------------------
    # PLANNER LOOP
    # --------------------------------------------------

    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "tools": "planner_tools",
            "coder": "coder",
            "__end__": END,
        },
    )

    graph.add_edge(
        "planner_tools",
        "planner",
    )

    # --------------------------------------------------
    # CODER LOOP
    # --------------------------------------------------

    graph.add_conditional_edges(
        "coder",
        should_continue,
        {
            "tools": "coder_tools",
            "__end__": END,
        },
    )

    graph.add_edge(
        "coder_tools",
        "coder",
    )
    
    memory = MemorySaver()

    return graph.compile(checkpointer=memory)