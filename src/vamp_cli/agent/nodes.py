import json
from typing import Any

from langchain_core.runnables import Runnable
from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, BaseMessage

from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.state import AgentState, TodoItem
from vamp_cli.agent.state import AgentState
from vamp_cli.agent.instructions import TODO_INSTRUCTIONS, PLAN_INSTRUCTIONS


def create_planner_node(model: Runnable):

    def planner_node(state: AgentState):
        print("#"*50)
        print("INSIDE PLANNER NODE")
        # The planner gets the same model or different model (if user as configured for planner explicitly) interface,
        # with a restricted read-only tool registry.

        messages = state["planner_messages"]

        print("#"*50)
        print("INSIDE PLANNER NODE JUST BEFORE INVOKE")
        response = model.invoke(messages)
        print("#"*50)
        print("INSIDE PLANNER NODE JUST AFTER INVOKE")
        
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
    print("#"*50)
    print("LEAVING PLANNER NODE")
    return planner_node


def create_agent_node(model: Runnable):

    def agent_node(state: AgentState):
        
        message_to_send = state["coder_messages"]
        
        plan_injected = state.get("is_plan_injected", False)
        
        if state["mode"] == "plan":
            plan = state["plan"]
            
            if not plan_injected and plan:
                plan_message = SystemMessage(content=f"Here is the project plan:\n{state['plan']}")
                plan_instruction = SystemMessage(content=PLAN_INSTRUCTIONS)
                
                message_to_send += [plan_message] + [plan_instruction]
                
                plan_injected = True
                
                
        if not state.get("todo"):
            message_to_send.append(SystemMessage(content=TODO_INSTRUCTIONS))
            
        if state.get("todo"):
            turns_since_update = _turns_since_todo_update(state["coder_messages"])
            if turns_since_update >= 4:
                message_to_send.append(
                    SystemMessage(content=_format_todo_reminder(state["todo"]))
                )
        
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
        
        print("#"*50)
        print("INSIDE TOOL NODE")
        
        message_type: str = "coder_messages"
        
        if type(state[message_type][-1]) != AIMessage:
            message_type = "planner_messages"

        last_message = state[message_type][-1]
        
        tool_messages:list[ToolMessage] = []
        todo_update = None

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
                
            # intercept the todo tool and write into state
            if tool_name == "write_todos" and isinstance(result, str):
                try:
                    todo_update = json.loads(result)
                except json.JSONDecodeError:
                    todo_update = None
                    
            string_result = result if isinstance(result, str) else json.dumps(result)
            
            tool_messages.append(
                ToolMessage(
                    content=string_result,
                    tool_call_id=tool_call["id"],
                    name=tool_name,
                    status="success" if isinstance(result, str) else "error"
                )
            )
        
        update = {message_type: tool_messages}
        if todo_update is not None:
            update["todo"] = todo_update
        
        print("#"*50)
        print("LEAVING TOOL NODE")
        return update

    return tool_node





def _turns_since_todo_update(messages: list[BaseMessage]) -> int:
    """How many AIMessages have passed since the last write_todos call."""
    count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            if any(tc["name"] == "write_todos" for tc in msg.tool_calls):
                return count
            count += 1
        elif isinstance(msg, AIMessage):
            count += 1
    return count


def _format_todo_reminder(todos: list[TodoItem]) -> str:
    marks = {
        "pending": "[ ]",
        "in_progress": "[~]",
        "completed": "[x]",
        "blocked": "[!]",
    }
    lines = ["Your current todo list:"]
    for t in todos:
        lines.append(f"  {marks[t['status']]} {t['id']}. {t['description']}")
    lines.append(
        "Continue with the next in_progress or pending item. "
        "Call write_todos when the status of any item changes."
    )
    return "\n".join(lines)