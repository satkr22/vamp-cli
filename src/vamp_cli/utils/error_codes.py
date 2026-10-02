from enum import Enum

class Error_codes(Enum):
    PATH_SCOPE_ERROR = "Path is out of workspace"
    NOT_FILE_ERROR = "Path is not a file path"
    FILE_NOT_FOUND_ERROR = "File not found"
    