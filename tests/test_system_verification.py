#!/usr/bin/env python3
"""
System Verification Test Script for VAMP-CLI

This script tests the complete agent system including:
1. Human-in-the-loop intercept (dangerous commands)
2. File operations (list_dir, read_file, write_file, apply_search_replace)
3. Search operations (file_search, ripgrep_search)
4. Command execution (safe commands)
5. Todo management

Run this script to verify all tools and human-in-the-loop are working correctly.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()

TEST_PROMPTS = {
    # Test 1: Human-in-the-loop with dangerous command (rm)
    "human_in_loop_rm": (
        "I want to test the human-in-the-loop security feature. "
        "Please execute this command to remove a test file: "
        "rm -rf /tmp/test_dangerous_file.txt "
        "First create the test file, then try to remove it with rm. "
        "This should trigger the security intercept."
    ),
    
    # Test 2: Human-in-the-loop with pip install
    "human_in_loop_pip": (
        "I want to test the human-in-the-loop security feature. "
        "Please try to install a package with: pip install requests "
        "This should trigger the security intercept for pip install commands."
    ),
    
    # Test 3: Human-in-the-loop with curl
    "human_in_loop_curl": (
        "I want to test the human-in-the-loop security feature. "
        "Please try to execute: curl https://example.com "
        "This should trigger the security intercept for curl commands."
    ),
    
    # Test 4: Comprehensive tool test (safe operations)
    "comprehensive_test": (
        "I want to verify all tools are working. Please do the following steps in order:\n"
        "1. List the contents of the current directory using list_dir\n"
        "2. Search for all Python files using file_search with query='*.py'\n"
        "3. Read the README.md file using read_file\n"
        "4. Search for the word 'Agent' in all Python files using ripgrep_search\n"
        "5. Create a test file at tests/test_verification_temp.py with content: "
        "'# Test file for verification\\nprint(\"Hello from test\")'\n"
        "6. Read the file you just created to verify it was written correctly\n"
        "7. Update the todo list with these tasks and mark them as completed\n"
        "8. Run a safe command: echo 'System verification successful'\n"
        "9. Clean up by deleting the test file you created\n"
        "10. Report back which tools worked and any issues found"
    ),
    
    # Test 5: File operations only
    "file_operations_test": (
        "Please perform these file operations:\n"
        "1. List the src/vamp_cli directory\n"
        "2. Read the src/vamp_cli/__init__.py file\n"
        "3. Create a new file tests/test_temp_created.txt with content 'Test content 123'\n"
        "4. Use apply_search_replace to change 'Test content 123' to 'Modified test content 456' in that file\n"
        "5. Read the modified file to show the change\n"
        "6. Delete the test file using a safe command (not rm -rf)\n"
        "7. Update todos to track these operations"
    ),
    
    # Test 6: Search operations only
    "search_operations_test": (
        "Please test all search capabilities:\n"
        "1. Use file_search to find all files containing 'config' in their name\n"
        "2. Use file_search to find all .md files\n"
        "3. Use ripgrep_search to find 'def main' in all Python files\n"
        "4. Use ripgrep_search with regex to find all function definitions (def \\w+)\n"
        "5. Report the results of each search"
    ),
    
    # Test 7: Safe command execution
    "safe_command_test": (
        "Please execute these safe commands and show their output:\n"
        "1. pwd - show current directory\n"
        "2. ls -la - list all files with details\n"
        "3. python --version - show Python version\n"
        "4. echo 'Hello from VAMP-CLI test' - simple echo\n"
        "5. whoami - show current user"
    ),
}


def print_menu():
    """Print the test selection menu."""
    print("\n" + "=" * 70)
    print("VAMP-CLI SYSTEM VERIFICATION TEST SUITE")
    print("=" * 70)
    print("\nSelect a test to run:\n")
    
    for i, (key, _) in enumerate(TEST_PROMPTS.items(), 1):
        test_name = key.replace("_", " ").title()
        print(f"  {i}. {test_name}")
    
    print(f"\n  {len(TEST_PROMPTS) + 1}. Run ALL tests sequentially")
    print(f"  0. Exit")
    print("\n" + "=" * 70)


def get_test_prompt(choice: int) -> str | None:
    """Get the test prompt based on user choice."""
    keys = list(TEST_PROMPTS.keys())
    
    if choice == 0:
        return None
    
    if 1 <= choice <= len(keys):
        return TEST_PROMPTS[keys[choice - 1]]
    
    if choice == len(keys) + 1:
        # Return all prompts joined
        return "\n\n".join(
            f"TEST {i+1}: {key.upper()}\n{prompt}" 
            for i, (key, prompt) in enumerate(TEST_PROMPTS.items())
        )
    
    return None


def main():
    """Main entry point for the test script."""
    print(f"\nProject Root: {PROJECT_ROOT}")
    print(f"\nThis script provides test prompts to verify the VAMP-CLI system.")
    print(f"You can either:\n")
    print(f"  A) Copy a test prompt and paste it into the running vamp-cli")
    print(f"  B) Run the test directly if integrated with main.py")
    
    while True:
        print_menu()
        
        try:
            choice = int(input("\nEnter your choice (0-{}): ".format(len(TEST_PROMPTS) + 1)).strip())
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue
        
        if choice == 0:
            print("\nExiting test suite.\n")
            break
        
        prompt = get_test_prompt(choice)
        if prompt:
            print("\n" + "=" * 70)
            print("TEST PROMPT TO COPY/PASTE INTO VAMP-CLI:")
            print("=" * 70)
            print(prompt)
            print("=" * 70)
            
            print("\n💡 INSTRUCTIONS:")
            print("   1. Start vamp-cli: python -m vamp_cli.main")
            print("   2. Select mode: 'normal'")
            print("   3. Paste the above prompt when asked 'You: '")
            print("   4. For human-in-the-loop tests, respond 'yes' or 'no' when prompted")
            print("   5. Observe the tool execution and results\n")
            
            input("Press Enter to continue...")
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()
