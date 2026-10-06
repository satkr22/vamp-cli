# tool_defs/file_tools_defs.py
import json
from typing import override
from vamp_cli.tools.base import Tools, InputSchema
from vamp_cli.tools.files import FileTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool


class ListDirTool(Tools):
    name = "list_dir"
    description = (
        "List files and directories under a given path, respecting .gitignore. "
        "Optionally limit recursion depth with max_depth."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "Directory to list."},
            "max_depth": {
                "type": "integer",
                "description": "Maximum recursion depth. Omit for unlimited.",
            },
        },
        required=["path"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        path = inputs["path"]
        max_depth = inputs.get("max_depth")
        result = self.file_tools.list_dir(path, max_depth)
        return json.dumps(result)


class ReadFileTool(Tools):
    name = "read_file"
    description = "Read a file with optional line range (1-indexed, max 500 lines)."
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "File path to read."},
            "start": {"type": "integer", "description": "First line (1-indexed)."},
            "end": {"type": "integer", "description": "Last line (inclusive)."},
        },
        required=["path"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        path = inputs["path"]
        start = inputs.get("start")
        end = inputs.get("end")
        result = self.file_tools.read_file(path, start, end)
        return json.dumps(result)


class WriteFileTool(Tools):
    name = "write_file"
    description = (
        "Create a new file or overwrite an existing one. "
        "If overwrite=False (default) an existing file is not modified."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "File path to write."},
            "content": {"type": "string", "description": "Full file content."},
            "overwrite": {
                "type": "boolean",
                "description": "Replace the entire file if it exists.",
            },
        },
        required=["path", "content"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        path = inputs["path"]
        content = inputs["content"]
        overwrite = inputs.get("overwrite", False)
        result = self.file_tools.write_file(path, content, overwrite)
        return json.dumps(result)


class ApplySearchReplaceTool(Tools):
    name = "apply_search_replace"
    description = (
        "Replace a unique 'search' block with 'replace' inside a file. "
        "Uses exact match first, then a fuzzy-whitespace fallback. "
        "Fails if the search block is not unique."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "file_path": {"type": "string"},
            "search": {"type": "string", "description": "Existing block to find."},
            "replace": {"type": "string", "description": "Replacement block."},
        },
        required=["file_path", "search", "replace"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        file_path = inputs["file_path"]
        search = inputs["search"]
        replace = inputs["replace"]
        result = self.file_tools.apply_search_replace(file_path, search, replace)
        return json.dumps(result)


class FileSearchTool(Tools):
    name = "file_search"
    description = (
        "Find files by name or glob pattern (e.g. 'config', '*.py', '*test*'). "
        "Uses ripgrep's file listing and respects .gitignore."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "query": {
                "type": "string",
                "description": "Filename fragment or glob pattern.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in. Defaults to '.'.",
            },
            "max_results": {"type": "integer", "description": "Default 100."},
        },
        required=["query"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        query = inputs["query"]
        path = inputs.get("path", ".")
        max_results = inputs.get("max_results", 100)
        result = self.file_tools.file_search(query, path, max_results)
        return json.dumps(result)


class RipgrepSearchTool(Tools):
    name = "ripgrep_search"
    description = (
        "Search file contents for text or regex using ripgrep. "
        "Returns matching lines with file paths and line numbers."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "query": {"type": "string", "description": "Text or regex to find."},
            "path": {
                "type": "string",
                "description": "File or directory. Defaults to '.'.",
            },
            "is_regex": {
                "type": "boolean",
                "description": "Treat query as a regex. Default false (literal, case-insensitive).",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional glob to restrict searched files, e.g. '*.py'.",
            },
            "max_matches": {
                "type": "integer",
                "description": "Maximum matches to return. Default 50.",
            },
        },
        required=["query"],
        additionalProperties=False,
    )

    def __init__(self, file_tools: FileTools):
        self.file_tools = file_tools

    @override
    def run(self, **inputs) -> str:
        query = inputs["query"]
        path = inputs.get("path", ".")
        is_regex = inputs.get("is_regex", False)
        file_pattern = inputs.get("file_pattern")
        max_matches = inputs.get("max_matches", 50)
        result = self.file_tools.ripgrep_search(
            query, path, is_regex, file_pattern, max_matches
        )
        return json.dumps(result)
    

class ExecuteCommandToolDef(Tools):
    name = "execute_command"
    description = (
        "Run a shell command inside the sandboxed terminal at the given "
        "working directory and return its output."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "command": {"type": "string", "description": "Shell command to run."},
            "cwd": {
                "type": "string",
                "description": "Working directory. Defaults to '.'.",
            },
        },
        required=["command"],
        additionalProperties=False,
    )

    def __init__(self, execute_command_tool: ExecuteCommandTool):
        self.execute_command_tool = execute_command_tool

    @override
    def run(self, **inputs) -> str:
        command = inputs["command"]
        cwd = inputs.get("cwd", ".")
        result = self.execute_command_tool.execute_command(command, cwd)
        return json.dumps(result)