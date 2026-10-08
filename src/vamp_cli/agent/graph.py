from typing import Literal

from langgraph.graph import StateGraph, START, END

from vamp_cli.agent.state import AgentState
from vamp_cli.agent.nodes import create_agent_node
from vamp_cli.agent.nodes import create_tool_node
from vamp_cli.tools.registry import ToolRegistry


def should_continue(state: AgentState) -> Literal["tools", "__end__"]:

    last_message = state["messages"][-1]

    if last_message.tool_calls: # type: ignore
        return "tools"

    return "__end__"


def create_graph(model, tool_registry: ToolRegistry):

    agent_node = create_agent_node(model)

    tool_node = create_tool_node(tool_registry)

    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "agent")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "__end__": END,
        },
    )

    graph.add_edge("tools", "agent")

    return graph.compile()