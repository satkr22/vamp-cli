from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown

import asyncio

from vamp_cli.tools.tools_classes import *
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.persistence.emitter import EventEmitter

from langgraph.types import Command
from langchain_core.runnables import RunnableConfig


async def streaming(
    console: Console,
    graph,
    payload: dict | Command,
    session_config: RunnableConfig,
    emitter: EventEmitter,
    tool_registry: ToolRegistry,
) -> dict[str, int]:
    
    call_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # 1. state buffers for fixed zones
    reasoning_buffer = ""
    content_buffer = ""

    # 2. Layout generator: Stacks Reasoning on top, Content on bottom
    THINKING_MAX_LINES = 8
    
    def generate_layout(is_final = False):
        renderables = []
        
        if reasoning_buffer:
            lines = reasoning_buffer.strip().split("\n")
            if not is_final and len(lines) > THINKING_MAX_LINES:
                display_lines = ["... (scrolling)"] + lines[-(THINKING_MAX_LINES - 1):]
                box_height = THINKING_MAX_LINES + 2
            else:
                display_lines = lines
                box_height = None  # Removes the height limit for the final render
                
            display_text = "\n".join(display_lines)
            renderables.append(
                Panel(
                    Text(display_text, style="dim"), 
                    title="[dim]Thinking[/dim]", 
                    title_align="left",
                    border_style="dim"
                )
            )
            
        if content_buffer or is_final:
            display_content = content_buffer if content_buffer else "**No response content or valid tool calls provided by agent.**"
            renderables.append(
                Panel(
                    Markdown(display_content), 
                    title="[bold green]Agent[/bold green]", 
                    title_align="left",
                    border_style="green"
                )
            )
            
        if not renderables:
            return Text("Starting...", style="dim italic")
            
        return Group(*renderables)

    # 3. Live Context Manager: Takes over rendering to update in-place
    try:
        with Live(generate_layout(), console=console, refresh_per_second=15, vertical_overflow="visible") as live:
            
            # UI CALLBACK
            def ui_tool_callback(tool_name: str, args: str,  output_text: str):
                nonlocal reasoning_buffer
                # Append the tool's actual output directly to the reasoning panel
                reasoning_buffer += f"\n>> {tool_name}: {args}\n\n"
                live.update(generate_layout())

            # INJECT IT TO TOOL REGISTRY 
            tool_registry.set_ui_callback(ui_tool_callback)
            
            async for event in graph.astream_events(payload, config=session_config, version="v2"):
                await emitter.consume(event)
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    
                    
                    # Extract reasoning
                    reasoning_chunk = getattr(chunk, "reasoning_content", None)
                    if not reasoning_chunk and "reasoning_content" in chunk.additional_kwargs:
                        reasoning_chunk = chunk.additional_kwargs["reasoning_content"]

                    # Route text to the correct buffer
                    if reasoning_chunk:
                        reasoning_buffer += str(reasoning_chunk)
                        live.update(generate_layout())

                    if chunk.content:
                        content_buffer += str(chunk.content)
                        live.update(generate_layout())

                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    um = getattr(output, "usage_metadata", None)
                    if um:
                        call_usage["input_tokens"] += um.get("input_tokens", 0)
                        call_usage["output_tokens"] += um.get("output_tokens", 0)
                        call_usage["total_tokens"] += um.get("total_tokens", 0)
                        
            live.update(generate_layout(is_final=True))
            
    except (asyncio.CancelledError, KeyboardInterrupt):
        console.print("\n[bold yellow]Action aborted by user.[/bold yellow]")
        return call_usage
    except Exception as e:
        console.print(f"\n[bold red]Error during run:[/bold red] {e}")
        return call_usage

    return call_usage
