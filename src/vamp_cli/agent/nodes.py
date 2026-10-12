import json
import asyncio
from typing import Any, Optional
from pydantic import BaseModel

from langgraph.types import interrupt
from langchain_core.runnables import Runnable
from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, BaseMessage

from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.state import AgentState, TodoItem
from vamp_cli.agent.state import AgentState
from vamp_cli.agent.status import FeedbackResponse
from vamp_cli.agent.instructions import TODO_INSTRUCTIONS, PLAN_INSTRUCTIONS



def create_planner_node(model: Runnable):

    async def planner_node(state: AgentState):
        # print("#"*50)
        # print("INSIDE PLANNER NODE")
        # The planner gets the same model or different model (if user as configured for planner explicitly) interface,
        # with a restricted read-only tool registry.

        messages = state["planner_messages"]

        # print("#"*50)
        # print("INSIDE PLANNER NODE JUST BEFORE INVOKE")
        response = await model.ainvoke(messages)
        # print("#"*50)
        # print("INSIDE PLANNER NODE JUST AFTER INVOKE")
        
        messages_to_return = [response]
        plan = response.content
        requires_execution = False
        
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
                # requires_execution = args.get("requires_execution")
                # if requires_execution == False:
                #     break
                
                checkpoint_payload = {
                    "implementation_permission": True,
                    "plan": plan,
                    "warning": "Asking before implementing this plan"
                }
                    
                # execution halts here
                user_feedback: FeedbackResponse = interrupt(value=checkpoint_payload, response_schema=FeedbackResponse)
                   
                if user_feedback.approved == False:
                    requires_execution = False
                elif user_feedback.approved == True:
                    requires_execution = True
                    
                break
        
        print("\n\nPLANNER------AI-RESPONSE:\n.\n.\n", response, "\n\n")
        
        return {
            "planner_messages": messages_to_return,
            "plan": plan,
            "requires_execution": requires_execution,
            "iteration": state["iteration"] + 1,
        }
    return planner_node


def create_agent_node(model: Runnable):

    async def agent_node(state: AgentState):
        
        message_to_send = list(state["coder_messages"])
        
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
        
        response: AIMessage = await model.ainvoke(message_to_send)

        print("\n\nCODER------AI-RESPONSE:\n.\n.\n", response, "\n\n")
        
        return {
            "coder_messages": [response],
            "is_plan_injected": plan_injected,
            "iteration": state["iteration"] + 1,
        }
    return agent_node



def create_tool_node(tool_registry: ToolRegistry):

    async def tool_node(state: AgentState):
        
        # print("tool_node: mode=", state["mode"])
        # print("  coder_messages[-1]   =", type(state["coder_messages"][-1]).__name__)
        # print("  planner_messages[-1] =", type(state["planner_messages"][-1]).__name__)
        
        # print("#"*50)
        # print("INSIDE TOOL NODE")
        
        message_type = "planner_messages" if state["mode"] == "plan" else "coder_messages"
        
        # if not isinstance(state[message_type][-1], AIMessage):
        #     message_type = "planner_messages"

        last_message = state[message_type][-1]
        
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            raise RuntimeError(
                f"tool_node invoked but last {message_type} entry "
                f"is {type(last_message).__name__} with no tool calls"
            )
            
        
        tool_messages:list[ToolMessage] = []
        todo_update = None

        # print("messaeg_type: ", message_type)
        for tool_call in last_message.tool_calls: # type: ignore

            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") or {}
            
            
            # human in the loop checkpoint
            
            is_bash = tool_name == "execute_command" or "execute" in tool_name.lower()
            
            contains_danger = False
            if is_bash:
                contains_danger = "rm " in str(tool_args.get("command")).lower() or "rmdir " in str(tool_args.get("command")).lower() or "curl " in str(tool_args.get("command")).lower() or "pip install " in str(tool_args.get("command")).lower()
                
            
            if is_bash and contains_danger:
                # throw out to the ternimal shell loop
                checkpoint_payload = {
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "warning": "CAUTION: Executing terminal system commands, internet access or destructive deletions requires verification."
                }
                
                # execution halts here
                user_feedback: FeedbackResponse = interrupt(value=checkpoint_payload, response_schema=FeedbackResponse)
                
                if user_feedback.approved == False:
                    reason = user_feedback.reason
                    if reason is None:
                        reason = "Human explicitly denied execution authority."
                    
                    denial_message = (
                        f"CRITICAL ACCESS DENIED: The security administrator has EXPLICITLY FORBIDDEN "
                        f"to execute the denied task in any possible way.\n"
                        f"Reason: {reason}\n"
                        f"MANDATE: You are strictly ordered to CEASE all attempts to execute the denied task in any possbile way. Abort this task "
                        f"immediately and explain this security restriction to the user. do not try to complete this task again. move to another task."
                    )
                    
                    tool_messages.append(
                        ToolMessage(
                            content=denial_message,
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                            status="error"
                        )
                    )
                    # print("command skipped")
                    continue # Skip actual execution of this tool call
            
            # Proceed safely with execution if approved or harmless command    
            try:
                result = await tool_registry.call(tool_name, tool_args)
            except Exception as e:
                result = {"error": f"Tool '{tool_name}' failed: {str(e)}"}
                  
            # try:
            #     tool_obj = tool_registry.get_tool(tool_name)[tool_name]
            #     result = tool_obj.run(**tool_args)

            # except Exception as e:
            #     result = {
            #         "error": f"Tool '{tool_name}' failed: {str(e)}"
            #     }
                
            # intercept the todo tool and write into state
            if tool_name == "write_update_todos" and isinstance(result, str):
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
    
        return update

    return tool_node





def _turns_since_todo_update(messages: list[BaseMessage]) -> int:
    """How many AIMessages have passed since the last write_update_todos call."""
    count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            if any(tc["name"] == "write_update_todos" for tc in msg.tool_calls):
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
        "Call write_update_todos when the status of any item changes."
    )
    return "\n".join(lines)