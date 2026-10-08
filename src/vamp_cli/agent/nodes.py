import json

from langchain_core.runnables import Runnable
from langchain_core.messages import ToolMessage

from vamp_cli.agent.state import AgentState
from vamp_cli.agent.state import AgentState
from vamp_cli.tools.registry import ToolRegistry



def create_agent_node(model: Runnable):

    def agent_node(state: AgentState):
        response = model.invoke(state["messages"])

        return {
            "messages": [response],
            "iteration": state["iteration"] + 1
        }

    return agent_node




def create_tool_node(tool_registry: ToolRegistry):

    def tool_node(state: AgentState):

        last_message = state["messages"][-1]
        
        # print(type(last_message))
        # print(last_message)

        tool_messages:list[ToolMessage] = []

        for tool_call in last_message.tool_calls: # type: ignore

            tool_name = tool_call["name"]
            tool_args = tool_call.get("args" or {})

            tool_obj = tool_registry.get_tool(tool_name)[tool_name]

            try:
                result = tool_obj.run(**tool_args)

            except Exception as e:
                result = {
                    "error": f"Tool '{tool_name}' failed: {str(e)}"
                }

            tool_messages.append(
                ToolMessage(
                    content=[result],
                    tool_call_id=tool_call["id"],
                    name=tool_name,
                    status="success" if isinstance(result, str) else "error"
                )
            )

        return {
            "messages": tool_messages
        }

    return tool_node