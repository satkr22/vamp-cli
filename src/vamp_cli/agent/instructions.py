TODO_INSTRUCTIONS = """\
    You maintain a todo list for every non-trivial task, using the `write_todos` tool.

    Rules:
    - Call `write_todos` BEFORE you start making changes, and again whenever the
    status of any item changes.
    - Always send the COMPLETE list — it replaces the previous one.
    - Exactly one item may be `in_progress` at a time.
    - Mark an item `completed` as soon as it is verified done, then set the next
    item to `in_progress` BEFORE starting it.
    - Never mark something `completed` if verification failed or it is only
    partially done — use `blocked` instead.
    - Keep item ids stable across updates.
"""

PLAN_INSTRUCTIONS = """\
    The plan above was produced by a separate planning agent after repository exploration. Treat it as the intended implementation plan, not as the user's original request. So execute the plan
"""
