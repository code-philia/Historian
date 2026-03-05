import os
import json
import time
import shutil
import logging

from .utils import *
from claude_code_sdk._errors import ProcessError

LOG_DIR = os.getenv("LOG_DIR")
logger = logging.getLogger("Claude.main")

def main(json_input: dict):
    if json_input["status"] == "init":
        return setup(json_input)
    elif json_input["status"] == "suggestion(location+content)":
        return subsequent_edit_recommendation(json_input)
    elif json_input["status"] == "suggestion(content)":
        return generate_edit_solution(json_input)
    
def setup(json_input: dict):
    """
    Set up the project in backend. Claude Code does not need extra setup.
    """
    pass

def subsequent_edit_recommendation(json_input: dict):
    """
    Get subsequent edit recommendation from Claude Code.
    """
    global LOG_DIR
    # Snapshot the repo before Claude makes any changes, so we can diff before/after later
    clone_dir(json_input["repo_dir"], f"{json_input['repo_dir']}_clone")

    # Prepare the chat message
    last_edit = json_input["prior_edits"][-1]
    chat_message = construct_edit_recommendation_chat_request(last_edit, json_input["edit_description"])
    
    max_retries = 3
    retry_count = 0
    while retry_count < max_retries:
        try:
            start = time.time()
            json_log = get_claude_suggestion(chat_message, json_input["repo_dir"])
            end = time.time()
            total_time = end - start
            break
        except Exception as e:
            if "exit code 1" in str(e) or "exit code: 1" in str(e):
                retry_count += 1
                if retry_count < max_retries:
                    logger.warning(f"Command failed, wait 5s and retry ({retry_count}/{max_retries})")
                    time.sleep(5)
                else:
                    logger.error(f"Command failed {max_retries} times, raise exception")
                    raise
            else:
                raise

    os.makedirs(os.path.join(LOG_DIR, "Claude", f"{json_input['project_name']}-{str(json_input['id'])[:8]}"), exist_ok=True)
    with open(
        os.path.join(LOG_DIR, "Claude", f"{json_input['project_name']}-{str(json_input['id'])[:8]}", f"chat_{len(json_input['prior_edits'])}.json"),
        "w"
    ) as f:
        json.dump(json_log, f, indent=4)

    # Get AI suggestions
    dirty_files = get_dirty_files(f"{json_input['repo_dir']}_clone", json_input["repo_dir"])
    pred_snapshots = get_pred_snapshots(dirty_files, f"{json_input['repo_dir']}_clone", json_input["repo_dir"])
    
    total_token = json_log["session_info"]["usage"]["input_tokens"] + json_log["session_info"]["usage"]["output_tokens"] + json_log["session_info"]["usage"]["cache_creation_input_tokens"]
    total_price = json_log["session_info"]["total_cost_usd"]
    logger.info(f"Time: {total_time:.2f}s | Tokens: {total_token} | Cost: ${total_price:.4f}")

    return pred_snapshots


def generate_edit_solution(json_input: dict):
    """
    Generate edit content for a given target location.
    Called when location matching fails and the framework provides a specific target_edit.
    """
    global LOG_DIR
    # Snapshot the repo before Claude makes any changes, so we can diff before/after later
    clone_dir(json_input["repo_dir"], f"{json_input['repo_dir']}_clone")

    target_edit = json_input["target_edit"]
    chat_message = construct_edit_content_generation_chat_request(
        target_edit, json_input["prior_edits"][-1], json_input["edit_description"]
    )

    max_retries = 3
    retry_count = 0
    while retry_count < max_retries:
        try:
            start = time.time()
            json_log = get_claude_suggestion(chat_message, json_input["repo_dir"])
            end = time.time()
            total_time = end - start
            break
        except Exception as e:
            if "exit code 1" in str(e) or "exit code: 1" in str(e):
                retry_count += 1
                if retry_count < max_retries:
                    logger.warning(f"Command failed, wait 5s and retry ({retry_count}/{max_retries})")
                    time.sleep(5)
                else:
                    logger.error(f"Command failed {max_retries} times, raise exception")
                    raise
            else:
                raise

    os.makedirs(os.path.join(LOG_DIR, "Claude", f"{json_input['project_name']}-{str(json_input['id'])[:8]}"), exist_ok=True)
    with open(
        os.path.join(LOG_DIR, "Claude", f"{json_input['project_name']}-{str(json_input['id'])[:8]}", f"chat_{len(json_input['prior_edits'])}_content.json"),
        "w"
    ) as f:
        json.dump(json_log, f, indent=4)

    dirty_files = get_dirty_files(f"{json_input['repo_dir']}_clone", json_input["repo_dir"])
    pred_snapshots = get_pred_snapshots(dirty_files, f"{json_input['repo_dir']}_clone", json_input["repo_dir"])

    return pred_snapshots
