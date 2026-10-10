# tool_defs/file_tools_defs.py
import json
from typing import override
from vamp_cli.tools.base import Tools, InputSchema
from vamp_cli.tools.files import FileTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool


class ListDirTool(Tools):
    name = "list_dir"
    description = (
        "List the files and directories contained within a workspace directory." 
        "Use this tool when you need to inspect directory structure or discover what exists at a path." 
        "It does not read file contents and does not search inside files."
        "The listing recursively traverses subdirectories unless max_depth is specified." 
        ".gitignore rules are respected, including .gitignore files found inside nested directories. Symbolic links are skipped."
        "The returned entries contain the filesystem path and whether each entry is a 'file' or 'directory'." 
        "Paths in the returned list are workspace-resolved paths."
        
        "Use this instead of:"
            "- 'file_search' when you want to see the directory structure, not find files by name."
            "- 'read_file' when you do not yet need file contents."
            "- 'ripgrep_search' when you are not searching file contents."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {
                "type": "string", 
                "description": "Directory to inspect. Required."
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum traversal depth relative to path. 0 means do not traverse into the directory; 1 includes its immediate children; omit this parameter to traverse without a depth limit.",
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
    description = (
        "Read the text content of a specific file."
        "Use this tool when you already know the file path and need to inspect its contents. It supports reading either the beginning of the file or a specific inclusive line range."

        "Line numbers are 1-indexed. A single call can return at most 500 lines. If the requested range is larger, it is automatically capped to 500 lines."

        "The returned content includes the source line number before each line."
        "The response also reports the actual returned range, total number of lines in the file, and file size in bytes."

        "The file is decoded as UTF-8; invalid UTF-8 bytes are replaced rather than causing decoding to fail."
        
        "Use this instead of:"
            "- 'list_dir' when you need file contents."
            "- 'ripgrep_search' when you already know the file and want to inspect surrounding code or a contiguous section."
            "- 'file_search' when you are looking for text inside a file rather than in its filename."
    )
    
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {
                "type": "string", 
                "description": "Path of the file to read. Required."
            },
            "start": {
                "type": "integer", 
                "description": "First line to return, 1-indexed. Defaults to line 1."
            },
            "end": {
                "type": "integer", 
                "description": "Last line to return, inclusive. Defaults to start + 499."
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
        start = inputs.get("start")
        end = inputs.get("end")
        result = self.file_tools.read_file(path, start, end)
        return json.dumps(result)


class WriteFileTool(Tools):
    name = "write_file"
    description = (
        "Create a file or replace the complete contents of an existing file with the supplied content."

        "Use this tool when you need to create a new file or intentionally rewrite an entire file."

        "By default, an existing file is protected and will not be modified. Set overwrite=true only when the entire existing file should be replaced."

        "Parent directories are created automatically when necessary."

        "After writing, the tool performs a syntax check and may return a syntax_warning when the modification was successfully written but the resulting file contains a detected syntax error."

        "This tool is for full-file replacement, not for making a small targeted edit inside an existing file."

        "Use this instead of:"
            "- 'apply_search_replace' when you intend to replace the entire file."
            "- 'apply_search_replace' when the desired change is too broad or when you already have the complete desired file contents."

        "Do not use this for"
            "A small modification to an existing file when preserving the rest of the file is important. Use apply_search_replace instead."
    )
    
    input_schema = InputSchema(
        type="object",
        properties={
            "path": {
                "type": "string", 
                "description": "Destination file path. Required."
            },
            "content": {
                "type": "string", 
                "description": "Complete new contents of the file. Required."
            },
            "overwrite": {
                "type": "boolean",
                "description": "Whether to replace an existing file completely. Defaults to false.",
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
        "Make a targeted edit to an existing file by replacing exactly one occurrence of an existing text block with a replacement text block."

        "Use this tool when you need to modify only a specific section of an existing file while preserving all unrelated content."

        "The tool searches for the search text in two stages:"

            "1. It first looks for an exact character-for-character match."
            "2. If no exact match exists, it tries a whitespace-tolerant fallback where runs of whitespace are treated as equivalent."

        "The search block must identify exactly one location. If it matches multiple locations, the edit is not performed because the tool cannot safely determine which occurrence should be changed."

        "The replacement affects only the matched block; the rest of the file remains unchanged."

        "After the edit, the tool performs a syntax check and may return a syntax_warning if the resulting file contains a detected syntax error."

        "Use this instead of:"
            "- 'write_file' when making a localized edit to an existing file."
            "- 'read_file' when the goal is modification rather than inspection."

        "Important selection rule:"
            "Provide enough surrounding code in search to make the target unique. Do not use a tiny fragment such as a common variable name or generic statement when the same text may occur multiple times."
    )
    
    input_schema = InputSchema(
        type="object",
        properties={
            "file_path": {
                "type": "string",
                "description": "Path of the existing file to modify. Required."
            },
            "search": {
                "type": "string", 
                "description": "Existing text block that uniquely identifies the section to replace. Required."
            },
            "replace": {
                "type": "string", 
                "description": "New text that should replace the matched block. Required."
            },
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
        "Find files by their filename or path name, without searching their contents."

        "Use this tool when you know part of a filename, a filename pattern, or a glob pattern and need to discover matching files."

        "If query does not contain glob metacharacters, it is treated as a filename substring search by automatically surrounding it with *. For example, query='config' behaves like *config*."

        "If query contains glob syntax such as *, ?, or character classes such as [abc], the query is used as the glob pattern directly."

        "The search is performed with ripgrep's file listing functionality and respects ignore rules such as .gitignore."

        "Results are returned as paths relative to the workspace root when possible."

        "Examples:"
        "query='config' → filenames containing config"
        "query='*.py' → Python files"
        "query='*test*' → filenames containing test"
        "query='src/**/config.*' → matching config files under src"

        "Use this instead of:"
            "- 'ripgrep_search' when searching filenames."
            "- 'list_dir' when you want matching files rather than a directory listing."

        "Do not use this for:"
            "Searching for a string inside file contents."
    )
    
    input_schema = InputSchema(
        type="object",
        properties={
            "query": {
                "type": "string",
                "description": "Filename substring or glob pattern to search for. Required.",
            },
            "path": {
                "type": "string",
                "description": "Filename substring or glob pattern to search for. Required.",
            },
            "max_results": {
                "type": "integer", 
                "description": "Maximum number of matching paths to return. Defaults to 100"},
        },
        required=["query", "path"],
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
        "Search the contents of one or more files for a text string or regular expression and return the matching lines with their file paths and line numbers."

        "Use this tool when you need to find specific text, symbols, strings, code patterns, or regex matches inside files."

        "By default, is_regex=false, so query is treated as a literal string and the search is case-insensitive."

        "When is_regex=true, query is interpreted as a regular expression. In this mode the regex search is case-sensitive unless the regular expression itself specifies case-insensitive behavior."

        "The search can target either a single file or a directory."

        "file_pattern optionally restricts which files are searched using a glob pattern such as *.py."

        "Each returned match includes:"
            "- the file path,"
            "- the 1-indexed line number,"
            "- the matching line content."

        "Returned matching-line content is limited to 250 characters."

        "Results are capped by max_matches; when the cap is reached, the result can be marked as truncated."

        "Examples:"
            "- Find a function name: query='execute_command'"
            "- Find exact text ignoring case: query='TODO'"
            "- Find a regex pattern: query='def\\s+\\w+\\(', is_regex=true"
            "- Search only Python files: file_pattern='*.py'"

        "Use this instead of:"
            "- 'file_search' when searching inside file contents."
            "- 'read_file' when you do not know where the relevant text is and need to locate it first."
            "- 'apply_search_replace' when you only need to find, not modify, text."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "query": {
                "type": "string", 
                "description": "Literal text or regular expression to search for. Required."
            },
            "path": {
                "type": "string",
                "description": "File or directory to search. Defaults to '.'",
            },
            "is_regex": {
                "type": "boolean",
                "description": "Interpret query as a regular expression. Defaults to false.",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional glob restricting which files are searched, for example *.py.",
            },
            "max_matches": {
                "type": "integer",
                "description": "Maximum number of matching lines to return. Defaults to 50.",
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
        "Execute a shell command in the sandboxed terminal and return the command's execution result."

        "Use this tool when the requested operation is best performed by an operating-system shell command or CLI program, such as running a build, test suite, formatter, package command, version-control command, script, compiler, or other executable."

        "The command is executed with cwd as its working directory."

        "This is a general-purpose command-execution tool and can perform actions beyond reading files, including commands that modify files, create processes, run programs, or otherwise change the workspace."

        "Use this instead of:"
            "- 'read_file' when the desired operation requires executing a command."
            "- 'write_file' when a CLI program should perform the modification rather than directly writing content."
            "- 'ripgrep_search' when you need functionality that requires an external shell command rather than text search."
            
        "Important selection rule:"
            "Do not use this tool merely to read, search, or edit a file when a dedicated file tool already provides the required operation more directly. Use shell execution when the task actually requires command execution or a CLI utility."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "command": {
                "type": "string", 
                "description": "Shell command to execute. Required."},
            "cwd": {
                "type": "string",
                "description": "Working directory in which the command runs. Defaults to '.'",
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
    
    
class SubmitPlanTool(Tools):
    name = "submit_plan"
    description = (
        "Submit the final plan or answer and declare whether execution is required. "
        "Call this exactly once when you are completely finished investigating and ready to hand off. "
        "This tool does not read files, run commands, or modify anything. "
        "It is a control signal used by the runtime to decide whether to end the run or hand the plan to the coder. "
        "Use it when:"
            "- You have finished all your tool calls and are ready to produce the final output."
            "- You need the runtime to know whether the request is purely informational or requires execution."
        "Do not use it before you are done, and do not call it more than once. "
        "Set requires_execution to false only if the request is fully answered by the plan text alone and no files need to change and no commands need to run. "
        "Otherwise set requires_execution to true."
    )
    input_schema = InputSchema(
        type="object",
        properties={
            "plan": {
                "type": "string",
                "description": (
                    "The complete final plan or answer. "
                    "For execution requests, this should be a step-by-step plan the coder can follow. "
                    "For informational requests, this should be the full explanation or answer. Required."
                ),
            },
            "requires_execution": {
                "type": "boolean",
                "description": (
                    "True if the request requires modifying files or running commands. "
                    "False if the request is purely informational and is fully answered by the 'plan' text."
                    "Required."
                ),
            },
        },
        required=["plan", "requires_execution"],
        additionalProperties=False,
    )

    @override
    def run(self, **inputs) -> str:
        # This tool is a control signal and must be intercepted by the tool node.
        # If run() is ever reached, the interception logic is missing.
        # raise RuntimeError(
        #     "submit_plan is a control tool and must be intercepted before execution."
        # )
        return json.dumps({"status": "Plan submitted."})