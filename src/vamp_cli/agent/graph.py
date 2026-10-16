from typing import Literal

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.state import AgentState
from vamp_cli.agent.nodes import (
    create_agent_node,
    create_planner_node,
    create_plan_approval_node,
    create_tool_classify_node,
    create_tool_approve_node,
    create_tool_execute_node,
)

MAX_ITERATIONS = 15


def route_start(state) -> Literal["agent", "planner"]:
    return "planner" if state["mode"] == "plan" else "agent"


def should_continue(state) -> Literal["classify", "__end__"]:
    if state["iteration"] >= MAX_ITERATIONS:
        return "__end__"
    last = state["coder_messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return "__end__"
    return "classify"


def route_after_planner(state) -> Literal["classify", "__end__"]:
    if state["iteration"] >= MAX_ITERATIONS:
        return "__end__"
    msgs = state["planner_messages"]
    last = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
    if last is None or not last.tool_calls:
        return "__end__"
    return "classify"


def route_after_execute(state) -> Literal["coder", "planner", "plan_approver"]:
    """Reads the (still-populated) pipeline channel + last planner AIMessage."""
    channel = state.get("_pending_tool_channel")

    if channel == "planner_messages":
        msgs = state["planner_messages"]
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        if last_ai and last_ai.tool_calls:
            if any(tc["name"] == "submit_plan" for tc in last_ai.tool_calls):
                return "plan_approver"
        return "planner"

    return "coder"


def route_after_plan_approver(state) -> Literal["coder", "__end__"]:
    return "coder" if state.get("requires_execution") else "__end__"


def create_graph(
    coder_model, 
    planner_model, 
    tool_registry: ToolRegistry,
    checkpointer=None,
):

    coder_node    = create_agent_node(coder_model)
    planner_node  = create_planner_node(planner_model)
    approver_node = create_plan_approval_node(planner_model)

    coder_classify   = create_tool_classify_node("coder_messages")
    planner_classify = create_tool_classify_node("planner_messages")
    
    approve_tools    = create_tool_approve_node()
    execute_tools    = create_tool_execute_node(tool_registry)

    g = StateGraph(AgentState)

    g.add_node("coder", coder_node)
    g.add_node("planner", planner_node)
    
    g.add_node("plan_approver", approver_node)
    
    g.add_node("coder_classify", coder_classify)
    g.add_node("planner_classify", planner_classify)
    
    g.add_node("approve_tools", approve_tools)
    g.add_node("execute_tools", execute_tools)

    # START
    g.add_conditional_edges(
        START, 
        route_start,
        {
            "agent": "coder", 
            "planner": "planner"
        }
    )

    # PLANNER -> classify -> approve -> execute -> route
    g.add_conditional_edges(
        "planner", 
        route_after_planner,
        {
            "classify": "planner_classify", 
            "__end__": END
        }
    )
    
    g.add_edge(
        "planner_classify", 
        "approve_tools"
    )

    # CODER -> classify -> approve -> execute -> route
    g.add_conditional_edges(
        "coder", 
        should_continue,
        {
            "classify": "coder_classify", 
            "__end__": END
        }
    )
    
    g.add_edge(
        "coder_classify", 
        "approve_tools"
    )

    # shared tail
    g.add_edge(
        "approve_tools", 
        "execute_tools"
    )
    
    g.add_conditional_edges(
        "execute_tools", 
        route_after_execute, 
        {
            "coder": "coder",
            "planner": "planner",
            "plan_approver": "plan_approver",
        }
    )

    # plan approval gate
    g.add_conditional_edges(
        "plan_approver", 
        route_after_plan_approver,
        {
            "coder": "coder", 
            "__end__": END
        }
    )

    return g.compile(checkpointer=checkpointer or MemorySaver())