"""SHA-256 hash utility for converting strings to hashes."""

import hashlib


def hash_string(input_string: str) -> str:
    """
    Convert a string to its SHA-256 hash representation.

    Args:
        input_string: The string to hash.

    Returns:
        A hexadecimal string representing the SHA-256 hash.

    Example:
        >>> hash_string("hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    if not isinstance(input_string, str):
        raise TypeError("Input must be a string")

    # Encode the string to bytes and compute SHA-256 hash
    hash_object = hashlib.sha256(input_string.encode('utf-8'))
    return hash_object.hexdigest()


if __name__ == "__main__":
    # Test with example
    test_input = "hello"
    result = hash_string(test_input)
    print(f"SHA-256 hash of '{test_input}': {result}")
