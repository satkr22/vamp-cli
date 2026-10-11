# VAMP-CLI System Verification Tests

This document explains how to test the complete VAMP-CLI system including human-in-the-loop security intercepts and all tool operations.

## Quick Start

### Option 1: Run the Test Script (Recommended)

```bash
# Make the script executable (optional)
chmod +x test_system_verification.py

# Run the test script
python test_system_verification.py
```

This will show you a menu of test prompts. Copy the prompt you want to test and paste it into the running vamp-cli.

### Option 2: Direct One-Liner Commands

Copy and paste any of these directly into vamp-cli:

#### Test Human-in-the-Loop (Dangerous Commands)

**Test 1 - rm command (should trigger intercept):**
```
Please execute: rm -rf /tmp/test_dangerous_file.txt
First create the file with: touch /tmp/test_dangerous_file.txt
Then try to remove it. This should trigger the security intercept.
```

**Test 2 - pip install (should trigger intercept):**
```
Please install the requests package using: pip install requests
This should trigger the human-in-the-loop security check.
```

**Test 3 - curl command (should trigger intercept):**
```
Please execute: curl https://example.com
This should trigger the security intercept for internet access.
```

#### Test All Tools (Comprehensive)

**Test 4 - Full system verification:**
```
I want to verify all tools are working. Please do these steps:
1. List the contents of the current directory using list_dir
2. Search for all Python files using file_search with query='*.py'
3. Read the README.md file using read_file
4. Search for the word 'Agent' in all Python files using ripgrep_search
5. Create a test file at tests/test_verification_temp.py with content: '# Test file\nprint("Hello")'
6. Read the file you just created to verify it was written
7. Update the todo list with these tasks
8. Run a safe command: echo 'System verification successful'
9. Report which tools worked
```

#### Test File Operations Only

**Test 5 - File operations:**
```
Please perform these file operations:
1. List the src/vamp_cli directory
2. Read the src/vamp_cli/__init__.py file
3. Create tests/test_temp_created.txt with content 'Test content 123'
4. Use apply_search_replace to change 'Test content 123' to 'Modified content 456'
5. Read the modified file to show the change
6. Update todos to track these operations
```

#### Test Search Operations Only

**Test 6 - Search operations:**
```
Please test all search capabilities:
1. Use file_search to find all files containing 'config' in their name
2. Use file_search to find all .md files
3. Use ripgrep_search to find 'def main' in all Python files
4. Report the results of each search
```

#### Test Safe Command Execution

**Test 7 - Safe commands:**
```
Please execute these safe commands and show their output:
1. pwd
2. ls -la
3. python --version
4. echo 'Hello from VAMP-CLI test'
5. whoami
```

## Expected Behavior

### Human-in-the-Loop Tests

When you run tests with dangerous commands (`rm`, `pip install`, `curl`, `rmdir`), you should see:

```
******************************
SECURITY VERIFICATION INTERCEPTED
Requested Tool:  execute_command
Arguments Passed: {
  "command": "rm -rf /tmp/test_dangerous_file.txt"
}
System Message:   CAUTION: Executing terminal system commands, internet access or destructive deletions requires verification.
******************************

Do you authorize this execution run? (yes/no):
```

- Type `yes` to allow the command
- Type `no` to block it (you can provide a reason)

### Tool Operation Tests

For file and search operations, you should see:
1. Tool calls being made (visible in agent output)
2. Results returned in JSON format
3. Todo list updates if requested
4. No security intercepts for safe operations

## Troubleshooting

### Human-in-the-loop not triggering?

Check that the command contains one of these trigger patterns:
- `rm ` (with space)
- `rmdir `
- `curl `
- `pip install `

The check is case-insensitive and looks for these patterns in the command string.

### Tools not working?

Verify:
1. Docker sandbox is running (check for container startup messages)
2. ripgrep (`rg`) is installed for search operations
3. You're in the project root directory

### Getting errors?

Check the logs directory for detailed error logs if you enabled logging in main.py.

## Test Coverage Matrix

| Test | Human-in-Loop | list_dir | read_file | write_file | apply_search_replace | file_search | ripgrep_search | execute_command | write_update_todos |
|------|---------------|----------|-----------|------------|---------------------|-------------|----------------|-----------------|-------------------|
| 1 (rm) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 2 (pip) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 3 (curl) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 4 (Comprehensive) | ❌ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| 5 (File ops) | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| 6 (Search ops) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| 7 (Safe cmds) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |

✅ = Tested | ❌ = Not tested in this specific test

## Cleanup

After testing, clean up any test files created:

```bash
# Remove test files if they still exist
rm -f tests/test_verification_temp.py
rm -f tests/test_temp_created.txt
```
