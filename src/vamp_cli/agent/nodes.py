import json
import time
import asyncio
from typing import Any
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText, HTML
from prompt_toolkit.styles import Style

from langgraph.graph import END
from langgraph.types import interrupt, Command
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, BaseMessage, HumanMessage

from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.state import AgentState, TodoItem
from vamp_cli.agent.state import AgentState
from vamp_cli.agent.status import FeedbackResponse
from vamp_cli.agent.instructions import TODO_INSTRUCTIONS, PLAN_INSTRUCTIONS

_dim_style = Style.from_dict({
    'dimmed': '#767B7D', 
})

def create_planner_node(model: Runnable):

    async def planner_node(state: AgentState, config: RunnableConfig):
        # The planner gets the same model or different model (if user as configured for planner explicitly) interface,
        # with a restricted read-only tool registry.

        messages = state["planner_messages"]

        # response = await model.ainvoke(messages)
        response = await model.with_config(config).ainvoke(messages)
        pending = next(
            (tc for tc in (response.tool_calls or []) if tc["name"] == "submit_plan"), 
            None
        )
        
        if response.tool_calls:
            print_formatted_text(
                FormattedText([(
                    "class:dimmed",
                    "\n\n[planner] calling:"
                )]),
                style=_dim_style,
                end="\n"
            )
            for tc in response.tool_calls:
                print_formatted_text(
                    FormattedText([(
                        "class:dimmed", 
                        f">> {str(tc['name'])}({str(tc['args'])})"
                    )]),
                    style=_dim_style,
                    end="\n"
                )
                # print("Tool: ", tc['name'])
                # print("Arguments: ", tc['args'])
        
        return {
            "planner_messages": [response],
            "plan": pending["args"].get("plan") if pending else None,
            "iteration": state["iteration"] + 1,
        }
    
    return planner_node


def create_plan_approval_node(model: Runnable):
    
    async def plan_approval_node(state: AgentState, config: RunnableConfig):
        
        if not state.get("plan"):
            return {"requires_execution": False}
        
        checkpoint_payload = {
            "implementation_permission": True,
            "plan": state["plan"],
            "warning": "Asking before implementing this plan"
        }
                    
        # execution halts here
        fb: FeedbackResponse = interrupt(value=checkpoint_payload, response_schema=FeedbackResponse)
        return {"requires_execution": fb.approved}   
    
    return plan_approval_node



def create_agent_node(model: Runnable):

    async def agent_node(state: AgentState, config: RunnableConfig):
        
        message_to_send = list(state["coder_messages"])
        
        if state["mode"] == "plan":
            plan = state["plan"]
            
            if plan:
                plan_message = SystemMessage(content=f"Here is the project plan:\n{state['plan']}")
                plan_instruction = SystemMessage(content=PLAN_INSTRUCTIONS)
                
                message_to_send += [plan_message] + [plan_instruction]
                
                
        # print(f"\n\n\n------ CONTEXT START------\n\n")
        # for mess in message_to_send:
        #     if isinstance(mess, SystemMessage):
        #         print("SystemMessage: ", end="")
        #     if isinstance(mess, HumanMessage):
        #         print("HumanMessage: ", end="")
        #     if isinstance(mess, ToolMessage):
        #         print("ToolMessage: ", end="")
        #     print(mess)
        #     print()
        # print("\n\n------ CONTEXT END ------\n\n\n")
        
        response: AIMessage = await model.with_config(config).ainvoke(message_to_send)
        
        # print("\n\nCODER------AI-RESPONSE:\n.\n.\n", response, "\n\n")
        
        if response.tool_calls:
            print_formatted_text(
                FormattedText([(
                    "class:dimmed",
                    "\n\n[coder] calling:"
                )]),
                style=_dim_style,
                end="\n"
            )
            for tc in response.tool_calls:
                print_formatted_text(
                    FormattedText([(
                        "class:dimmed", 
                        f">> {str(tc['name'])}({str(tc['args'])})"
                    )]),
                    style=_dim_style,
                    end="\n"
                )
        
        return {
            "coder_messages": [response],
            # "is_plan_injected": plan_injected,
            "iteration": state["iteration"] + 1,
        }
    return agent_node


DANGEROUS_PATTERNS = ("rm ", "rmdir ", "curl ", "wget ", "pip install ", "shutdown", "dd ")

def _is_dangerous(tool_name: str, tool_args: dict) -> bool:
    is_bash = tool_name == "execute_command" or "execute" in tool_name.lower()
    if not is_bash:
        return False
    cmd = str(tool_args.get("command", "")).lower()
    return any(p in cmd for p in DANGEROUS_PATTERNS)


def create_tool_classify_node(channel: str):
    async def tool_classify_node(state: AgentState, config: RunnableConfig):
        last = state[channel][-1]

        if not isinstance(last, AIMessage) or not last.tool_calls:
            raise RuntimeError(
                f"classify_node invoked but last {channel} entry "
                f"is {type(last).__name__} with no tool calls"
            )

        dangerous_ids = [
            tc["id"]
            for tc in last.tool_calls
            if _is_dangerous(tc["name"], tc.get("args") or {})
        ]

        return {
            "_pending_tool_channel":    channel,
            "_pending_tool_calls":      [dict(tc) for tc in last.tool_calls],
            "_dangerous_tool_call_ids": dangerous_ids,
            "_approved_tool_call_ids":  [],
            "_denied_tool_call_ids":    [],
        }

    return tool_classify_node


def create_tool_approve_node():
    async def tool_approve_node(state: AgentState, config: RunnableConfig):
        pending       = state.get("_pending_tool_calls", [])
        dangerous_ids = state.get("_dangerous_tool_call_ids", [])
        all_ids       = [tc["id"] for tc in pending]

        # No dangerous calls: auto-approve everything, no interrupt.
        if not dangerous_ids:
            return {
                "_approved_tool_call_ids": all_ids,
                "_denied_tool_call_ids":   [],
            }

        dangerous_calls = [tc for tc in pending if tc["id"] in dangerous_ids]

        payload = {
            "requires_tool_approval": True,
            "dangerous_calls": [
                {
                    "tool_call_id": tc["id"],
                    "tool_name":    tc["name"],
                    "tool_args":    tc.get("args") or {},
                }
                for tc in dangerous_calls
            ],
            "safe_call_count": len(pending) - len(dangerous_calls),
            "warning": (
                "The following tool calls were flagged as dangerous. "
                "Approving will execute them (plus all safe calls) exactly once."
            ),
        }

        # Halts here. On resume, returns the FeedbackResponse instead of raising.
        fb: FeedbackResponse = interrupt(value=payload, response_schema=FeedbackResponse)

        safe_ids = [i for i in all_ids if i not in dangerous_ids]

        if fb.approved:
            return {
                "_approved_tool_call_ids": safe_ids + dangerous_ids,
                "_denied_tool_call_ids":   [],
            }
        return {
            "_approved_tool_call_ids": safe_ids,
            "_denied_tool_call_ids":   dangerous_ids,
        }

    return tool_approve_node


def create_tool_execute_node(tool_registry: ToolRegistry):
    async def tool_execute_node(state: AgentState, config: RunnableConfig):
        channel = state["_pending_tool_channel"]
        pending = state.get("_pending_tool_calls", [])
        approved = set(state.get("_approved_tool_call_ids", []))
        denied = set(state.get("_denied_tool_call_ids", []))

        tool_messages: list[ToolMessage] = []
        todo_update = None
        
        interrupted = False
        
        emitter = (config.get("configurable") or {}).get("emitter")
        node_name = channel.replace("_messages", "") if channel else None

        for tc in pending:
            tc_id, tc_name, tc_args = tc["id"], tc["name"], tc.get("args") or {}

            if tc_id in denied:
                content=(
                    "CRITICAL ACCESS DENIED: The security administrator has "
                    "EXPLICITLY FORBIDDEN executing the denied task in any way. "
                    "MANDATE: cease all attempts to execute the denied task. "
                    "Abort this task immediately and explain the restriction "
                    "to the user. Do not retry. Move on to another task."
                )
                
                tool_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=tc_id,
                    name=tc_name,
                    status="error",
                ))
                
                if emitter is not None:
                    emitter.emit_tool_result(
                        tool_call_id=tc_id, tool_name=tc_name, node=node_name,
                        status="denied", content=content, duration_ms=0,
                    )
                continue

            if tc_id not in approved:
                content = f"Tool '{tc_name}' was not approved for execution."
                tool_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=tc_id, name=tc_name, status="error",
                ))
                if emitter is not None:
                    emitter.emit_tool_result(
                        tool_call_id=tc_id, tool_name=tc_name, node=node_name,
                        status="error", content=content, duration_ms=0,
                    )
                continue
            
            if interrupted:
                content="[Skipped — session was interrupted by user before this tool ran]"
                tool_messages.append(ToolMessage(
                    content=content,
                    tool_call_id=tc_id, name=tc_name, status="error",
                ))
                
                if emitter is not None:
                    emitter.emit_tool_result(
                        tool_call_id=tc_id, tool_name=tc_name, node=node_name,
                        status="error", content=content, duration_ms=0,
                    )
                continue
            
            start = time.time()
            try:
                result = await tool_registry.call(tc_name, tc_args)
            except (asyncio.CancelledError, KeyboardInterrupt):
                interrupted = True
                result = "[Tool execution was interrupted by user]"
                status = "error"
            except Exception as e:
                result = {"error": f"Tool '{tc_name}' failed: {e}"}
                status = "error"
            duration_ms = int((time.time() - start) * 1000)

            if tc_name == "write_update_todos" and isinstance(result, str) and not interrupted:
                try:
                    todo_update = json.loads(result)
                except json.JSONDecodeError:
                    todo_update = None

            string_result = result if isinstance(result, str) else json.dumps(result)
            tool_messages.append(ToolMessage(
                content=string_result,
                tool_call_id=tc_id,
                name=tc_name,
                status="success" if isinstance(result, str) else "error",
            ))
            if emitter is not None:
                emitter.emit_tool_result(
                    tool_call_id=tc_id, tool_name=tc_name, node=node_name,
                    status="success", content=string_result,
                    duration_ms=duration_ms,
                )

        update = {
            channel: tool_messages,
            "_pending_tool_calls":     [],
            "_dangerous_tool_call_ids": [],
            "_approved_tool_call_ids":  [],
            "_denied_tool_call_ids":    [],
        }
        if todo_update is not None:
            update["todo"] = todo_update
        
        return update

    return tool_execute_node


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