import os
import json
import time
import dotenv
import asyncio
from openai import AsyncOpenAI
from .utils import get_renameable_elements
from agents import (Agent, ModelSettings, function_tool, Runner,
                   set_default_openai_client, set_tracing_disabled,
                   set_default_openai_api)

dotenv.load_dotenv(".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

custom_client = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
set_default_openai_client(custom_client)
set_tracing_disabled(True)
set_default_openai_api("chat_completions")

# Global variables
repo_dir = None
language = None
prior_edit_seqs = None
current_location_of_prior_edits = None


def init_agent() -> Agent:
    current_path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(current_path, "navigator_instructions.md"), "r") as f:
        instructions = f.read()
        
    agent = Agent(
        name="Navigator",
        instructions=instructions,
        model="gpt-4.1",
        tools=[rename, read_file],
        model_settings=ModelSettings(tool_choice="auto") # Allow agent to choose tools automatically
    )
    
    return agent


@function_tool
def rename(target_edit_idx: int, old_name: str, new_name: str) -> list:
    """
    When a prior edit has the intention of renaming a variable/function/class, this tool performs the rest of the renaming across the entire project.
    
    Args:
        target_edit_idx (int): The index of prior edit that indicates the rename intention.
        old_name (str): The current name of the variable/function/class to be renamed.
        new_name (str): The new name to replace the old name.
    """
    global LSP
    global repo_dir, prior_edit_seqs, current_location_of_prior_edits
    
    target_edit = None
    for edit in prior_edit_seqs:
        if edit["idx"] == target_edit_idx:
            target_edit = edit
            break
    if target_edit is None:
        raise ValueError("[ERROR:SUT] Target edit set by agent not found in prior edits.")
    
    # Step 1: Update the project to the status when the target edit has not been applied yet
    target_edit_location = current_location_of_prior_edits[target_edit_idx]
    target_edit_abs_file_path = os.path.join(repo_dir, target_edit_location["file_path"])
    
    with open(target_edit_abs_file_path, "r") as f:
        code_lines = f.readlines()
        
    reverted_version = code_lines[:target_edit_location["start_line"]] + target_edit["before"] + code_lines[target_edit_location["start_line"] + len(target_edit["after"]):]
    
    with open(target_edit_abs_file_path, "w") as f:
        f.writelines(reverted_version)
    
    # Step 2: Use tree-sitter to parse the code before edit
    elements = get_renameable_elements("".join(target_edit["before"]), language)
    
    # Step 3: find the position of old_name
    target_element = None
    for element in elements:
        if element["name"] == old_name:
            target_element = element
            break
    
    if target_element is None:
        raise ValueError("[ERROR:SUT] Target element to rename not found in the code before edit.")
    
    position = {
        "line": target_element["line"] + target_edit_location["start_line"],
        "character": target_element["column"] + len(old_name) // 2
    }
    
    # Step 4: invoke LSP's rename functionality
    print(f"[INFO:SUT] LSP info: {LSP}")
    print(f"[INFO:SUT] Invoking LSP rename operation for `{old_name}` to `{new_name}` at position {position} in file {target_edit_abs_file_path}.")
    try:
        response = LSP.rename(target_edit_abs_file_path, position, new_name)
    except:
        print("[ERROR:SUT] LSP rename operation failed, sleep 5 seconds and return None.")
        time.sleep(5)
        return None
    
    # Parse the response of LSP rename operation
    if len(response) == 0 or "error" in response[0] or "result" not in response[0] or response[0]["result"] is None: 
        return None
    # var edits is a dictionary: {file_path: [list of rename edits]}
    edits = LSP._parse_rename_response(response, {}, old_name, new_name)
    
    # Filter out the target prior edit
    for abs_file_path, edits_in_file in edits.items():
        if abs_file_path != target_edit_abs_file_path:
            # if the rename at file not the same as the last edit file, it will not be the last edit
            continue
        
        filtered_edits = []
        for edit in edits_in_file:
            # if the rename location is in the last edit, remove it
            need_filter = False
            print(f"edit: {edit}, target_edit_location: {target_edit_location}")
            if edit["range"]["start"]["line"] == target_edit_location["start_line"]:
                need_filter = True
            if not need_filter:
                filtered_edits.append(edit)
        
        edits[abs_file_path] = filtered_edits
    
    # Step 5: include the target edit's offset to the result of rename
    if len(target_edit["before"]) != len(target_edit["after"]):
        offset = len(target_edit["after"]) - len(target_edit["before"])
        target_edit_starts_at_line = target_edit_location["start_line"]
    else:
        offset = 0
        target_edit_starts_at_line = 0
        
    for abs_file_path, edits_in_file in edits.items():
        if abs_file_path != target_edit_abs_file_path:
            continue
        for edit in edits_in_file:
            if edit["range"]["start"]["line"] >= target_edit_starts_at_line:
                edit["range"]["start"]["line"] += offset
                edit["range"]["end"]["line"] += offset
    
    # Step 6: recover the reverted version to the latest version after all prior edits applied
    with open(target_edit_abs_file_path, "w") as f:
        f.writelines(code_lines)
    
    return edits

# @function_tool
# def find_references(file_path: str, code_window_starts_at_line: int, name: str) -> list:
#     """
#     Find all references to a variable/function/class in the project.
    
#     Args:
#         file_path (str): The relative path to the file where the search is initiated.
#         code_window_starts_at_line (int): The line number where the code window starts.
#         name (str): The name of the variable/function/class to find references for.
#     """
#     raise NotImplementedError("This is a stub for the find references tool.")

@function_tool
def read_file(file_path: str, reading_starts_at_line: int = 0, num_lines_to_read: int = 10) -> str:
    """
    Read a specified number of lines from a file starting at a given line number.
    
    Args:
        file_path (str): The relative path to the file to read from.
        reading_starts_at_line (int): The line number to start reading from, default is 0.
        num_lines_to_read (int): The number of lines to read, default is 10. If set to None, will read till end of file.
    """
    global repo_dir
    abs_file_path = os.path.join(repo_dir, file_path)
    
    try:
        with open(abs_file_path, "r") as f:
            code_lines = f.readlines()
    except Exception as e:
        return f"Error: {str(e)}"
        
    if num_lines_to_read is None:
        return "".join(code_lines[reading_starts_at_line:])
    else:
        return "".join(code_lines[reading_starts_at_line: reading_starts_at_line + num_lines_to_read])
    
# def keyword_searching(grep_command: str) -> ?:
#     """
#     Perform a keyword search in the codebase using grep command.
    
#     Args:
#         grep_command (str): The grep command to execute for searching keywords.
#     """
#     raise NotImplementedError("This is a stub for the keyword searching tool.")

# def code_diagnose()


if __name__ == "__main__":
    agent = init_agent()

    payload = {
        "prior_edits": [
            {
                "file_path": "src/transformers/activations.py",
                "code_window_starts_at_line": 107,
                "prefix": "\n",
                "code_before": "def get_activation(activation_string):\n",
                "code_after": "def get_activation(activation_str):\n",
                "suffix": "    if activation_string in ACT2FN:\n        return ACT2FN[activation_string]\n"
            }
        ],
        "project_dir" : "/home/chenyan/workspace/repos_tmp/transformers"
    }

    
    # result = await Runner.run(agent, json.dumps(payload))