import asyncio
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

async def peek(session_id: str):
    
    db = Path.home() / f".vamp/checkpoints/{session_id}.sqlite"
    
    async with AsyncSqliteSaver.from_conn_string(str(db)) as cp:
        cfg = {"configurable": {"thread_id": session_id}}
        tup = await cp.aget_tuple(cfg) # type: ignore
        print("has checkpoint:", tup is not None)
        if tup:
            values = tup.checkpoint.get("channel_values", {})
            print("iteration:", values.get("iteration"))
            print("coder_messages:", len(values.get("coder_messages", [])))
            print("planner_messages:", len(values.get("planner_messages", [])))
        

session_id = "01a145b4-5103-7014-97da-d5ca9c871d68"
asyncio.run(peek(session_id=session_id))





"""
session_id: 01a145b4-5103-7014-97da-d5ca9c871d68

"""
