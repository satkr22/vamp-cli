import json

from langchain_core.runnables import Runnable
from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, HumanMessage

from vamp_cli.agent.state import AgentState
from vamp_cli.agent.state import AgentState
from vamp_cli.tools.registry import ToolRegistry



def create_planner_node(model: Runnable, tool_registry: ToolRegistry):

    def planner_node(state: AgentState):

        # The planner gets the same model or different model (if user as configured for planner explicitly) interface,
        # with a restricted read-only tool registry.

        messages = state["planner_messages"]

        response = model.invoke(messages)
        
        messages_to_return = [response]
        plan = response.content
        requires_execution = True
        
        for tc in (response.tool_calls or []):
            if tc["name"] == "submit_plan":
                messages_to_return.append(
                    ToolMessage(
                        content=json.dumps({"status": "Plan submitted."}),
                        tool_call_id=tc["id"],
                        name="submit_plan",
                        status="success"
                    )
                )
                args = tc.get("args") or {}
                plan = args.get("plan", plan)
                requires_execution = bool(args.get("requires_execution", True))
                break
        
        print("\n\nPLANNER------AI-RESPONSE:\n.\n.\n", response, "\n\n")
        print(".\n.\n.\n", plan)
        
        return {
            "planner_messages": messages_to_return,
            "plan": plan,
            "requires_execution": requires_execution,
            "iteration": state["iteration"] + 1,
        }

    return planner_node


def create_agent_node(model: Runnable):

    def agent_node(state: AgentState):
        
        message_to_send = state["coder_messages"]
        
        plan_injected = state.get("is_plan_injected", False)
        
        if state["mode"] == "plan":
            plan = state["plan"]
            
            if not plan_injected and plan:
                plan_message = SystemMessage(content=f"Here is the project plan:\n{state['plan']}")
                plan_instruction = SystemMessage(content=f"The plan above was produced by a separate planning agent after repository exploration. Treat it as the intended implementation plan, not as the user's original request. Use it as guidance, verify details against the repository when necessary, and execute the plan.")
                
                message_to_send += [plan_message] + [plan_instruction]
                
                plan_injected = True
        
        response: AIMessage = model.invoke(message_to_send)

        print("\n\nCODER------AI-RESPONSE:\n.\n.\n", response, "\n\n")
        
        return {
            "coder_messages": [response],
            "is_plan_injected": plan_injected,
            "iteration": state["iteration"] + 1,
        }
    return agent_node

def create_tool_node(tool_registry: ToolRegistry):

    def tool_node(state: AgentState):
        
        message_type: str = "coder_messages"
        
        if type(state[message_type][-1]) != AIMessage:
            message_type = "planner_messages"

        last_message = state[message_type][-1]
        
        tool_messages:list[ToolMessage] = []

        for tool_call in last_message.tool_calls: # type: ignore

            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") or {}

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
            message_type: tool_messages
        }

    return tool_node