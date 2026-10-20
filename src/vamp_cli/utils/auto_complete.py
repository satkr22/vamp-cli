from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.tools.files import _read_ignore_file
import re
from pathlib import Path


class AtFileCompleter(Completer):
    """
    Activate filesystem completion only when the cursor is after an '@' symbol (ex: '@src/main.py')
    """
    def __init__(self, root_dir: str):
        
        ignore = IgnoreMatcher(root_dir)
        
        ignore_list: list[str] | None = _read_ignore_file(root_dir)
        if ignore_list is not None:
            ignore.add(ignore_list)
        
        # PathCompleter will list files under the project root.
        self._path_completer = PathCompleter(
            get_paths=lambda: [root_dir],
            only_directories=False,
            file_filter=ignore.should_keep
        )

    def get_completions(self, document: Document, complete_event):
        text_before = document.text_before_cursor

        # Find the last '@' that is not part of an email/word
        at_index = text_before.rfind("@")
        if at_index == -1:
            return

        # Ensure '@' is at the start of a token (preceded by whitespace or start)
        if at_index > 0 and not text_before[at_index - 1].isspace():
            return

        # The user types a file path right after '@'
        path_fragment = text_before[at_index + 1 :]
        # print("f: ", path_fragment)
        
        # Build a Document that contains only the path fragment
        path_document = Document(path_fragment)
        # print("d: ", path_fragment)
        
        # Delegate to PathCompleter
        for completion in self._path_completer.get_completions(
            path_document, complete_event
        ):
            # Prepend '@' to the suggestion text
            yield Completion(
                text=completion.text,
                start_position=completion.start_position,
                # start_position=completion.start_position - len(path_fragment) - 1,
                display=completion.display,
            )
            
            
def expand_at_references(text: str, workspace_root: str) -> str:
    """
    Find all @path tokens in text, read the files, and append their contents.
    """
    # Matches @ followed by non-whitespace characters (paths)
    pattern = r"@([^\s@]+)"
    matches = re.findall(pattern, text)

    
    if not matches:
        return text

    root = Path(workspace_root)
    attachments: list[str] = []

    for rel_path in matches:
        file_path = root / rel_path
        try:
            if file_path.is_file():
                content = file_path.read_text(encoding="utf-8")
                attachments.append(
                    f"\n\n--- FILE: {rel_path} ---\n{content}\n--- END OF FILE ---\n\n"
                )
        except Exception:
            # Ignore unreadable files (binary, permission, etc.)
            continue

    if not attachments:
        return text
    

    return text + "".join(attachments)