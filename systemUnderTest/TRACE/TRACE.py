import os
import time
import json
import logging

from collections import defaultdict
from torch.utils.data import DataLoader

from .Invoker import ask_invoker
from .Locator import make_locator_dataset, locator_predict
from .Generator import edit_location_2_snapshots
from .logic_gate import logic_gate
from .code_window import CodeWindow
from .is_clone import find_clone_in_project
from .enriched_semantic import construct_edit_hunk
from .utils import merge_snapshots, diagnostic_2_sliding_windows

def TRACE(json_input, MODELS, LSP, logger):
    """
    Use LSP & Invoker to predict the next edit location
    """
    prior_edits = json_input["prior_edits"]
    language = json_input["language"]
    service, service_info = ask_invoker(prior_edits, language, MODELS, logger)
    
    predictions = None
    # STEP 1: logic based code navigation
    # STEP 1.1: rename edit composition
    if service == "rename" :
        start = time.time()
        predict_snapshots = process_rename(service_info, json_input, LSP, logger)
        end = time.time()
        if predict_snapshots is None or predict_snapshots == {}:
            logger.info(f"[SUT] LSP rename retrieved 0 locations.")
        else:
            return predict_snapshots
        
    # STEP 1.2: def&ref edit composition
    if service == "def&ref" or service == "all":
        start = time.time()
        predictions = process_def_ref(service_info, json_input, LSP, MODELS, logger)
        end = time.time()
        if predictions is None:
            logger.info(f"LSP def&ref returned empty")
        # we don't return prediction directly, as we may receive diagnostics from LSP
        
    # STEP 1.3: code clone edit composition
    if service == "clone" or service == "all":
        start = time.time()
        predictions = process_code_clone(service_info, json_input, MODELS, logger)
        end = time.time()
        if predictions is None:
            logger.info(f"LSP clone returned empty")

    # STEP 2: error based code navigation
    # try:
    diagnose_predictions = process_diagnose(json_input, LSP, MODELS, logger)
    if diagnose_predictions is not None:
        if predictions is not None:
            # merge the predictions from logic and error
            predictions = merge_snapshots(predictions, diagnose_predictions)
        else:
            predictions = diagnose_predictions
    # except:
    #     return predictions
    
    return predictions

def process_rename(service_info, json_input, LSP, logger):
    """
    Process rename edit composition using LSP.
    
    Args:
        service_info (dict): Information provided by the Invoker about the rename service, including keys: ['deleted_identifiers', 'added_identifiers', 'map'], where 'map' is a dictionary mapping old identifier names to new identifier names.
        json_input (dict): The input JSON containing necessary information, including keys: ['id', 'language', 'project_name', 'status', 'repo_dir', 'prior_edits', 'edit_description']
        LSP (LanguageServer): The initialized Language Server Protocol (LSP) instance for the target programming language.
        logger (Logger): Logger for logging information.
        
    Returns:
        predict_snapshots (dict): A dictionary mapping file paths to predicted snapshots after applying rename edits.
    """
    # Step 1: Revert the project to the state before the target rename edit
    target_edit = json_input["prior_edits"][-1]
    target_edit_idx = target_edit["idx"]
    target_edit_start_at_line = target_edit["currently_start_at_line"]
    target_edit_abs_file_path = os.path.join(json_input["repo_dir"], target_edit["file_path"])
    
    with open(target_edit_abs_file_path, "r") as f:
        code_lines = f.readlines()
        
    reverted_version = code_lines[:target_edit_start_at_line] + target_edit["before"] + code_lines[target_edit_start_at_line + len(target_edit["after"]):]
    
    with open(target_edit_abs_file_path, "w") as f:
        f.writelines(reverted_version)
        
    # Step 2: for all renamed identifiers, invoke LSP rename services
    edits = {}
    for old_name, new_name in service_info["map"].items():
        # find the position of the old name
        for delete_identifier in service_info["deleted_identifiers"]:
            if old_name == delete_identifier["name"]:
                position = {
                    "line": delete_identifier["start"][0] + target_edit_start_at_line,
                    "character": (delete_identifier["start"][1] + delete_identifier["end"][1]) // 2
                }
        try:
            response = LSP.rename(target_edit_abs_file_path, position, new_name, wait_time=1)
        except:
            logger.error("[LSP] Error in getting rename services.")
            time.sleep(5)
            # restore the latest version before returning
            with open(target_edit_abs_file_path, "w") as f:
                f.writelines(code_lines)
            return None
        
        if len(response) == 0 or "error" in response[0] or "result" not in response[0] or response[0]["result"] is None: 
            continue
        edits = LSP._parse_rename_response(response, edits, old_name, new_name)
        
    # Step 3: filter out the last edit (the target rename edit)
    for abs_file_path, edits_in_file in edits.items():
        if abs_file_path != target_edit_abs_file_path:
            # if the rename at file not the same as the last edit file, it will not be the last edit
            continue
        
        filtered_edits = []
        for edit in edits_in_file:
            # if the rename location is in the last edit, remove it
            need_filter = False
            logger.debug(f"[SUT] Edit retrieved by LSP from same file as last prior edit: {edit}, last prior edit location: {target_edit['file_path']}:{target_edit_start_at_line}")
            if edit["range"]["start"]["line"] in list(range(target_edit_start_at_line, target_edit_start_at_line + len(target_edit["before"]))):
                need_filter = True
            if not need_filter:
                filtered_edits.append(edit)
        
        edits[abs_file_path] = filtered_edits
    
    # Step 4: Add the target edit's offset to the retrieved rename edits
    if len(target_edit["before"]) != len(target_edit["after"]):
        offset = len(target_edit["after"]) - len(target_edit["before"])
        target_edit_start_at_line = target_edit_start_at_line
    else:
        offset = 0
        target_edit_start_at_line = 0
        
    for abs_file_path, edits_in_file in edits.items():
        if abs_file_path != target_edit_abs_file_path:
            continue
        for edit in edits_in_file:
            if edit["range"]["start"]["line"] >= target_edit_start_at_line:
                edit["range"]["start"]["line"] += offset
                edit["range"]["end"]["line"] += offset
    logger.debug(f"[SUT] LSP rename retrieved the following locations: \n{json.dumps(edits, indent=2)}")
    
    # Step 5: Restore the latest version after all prior edits applied
    with open(target_edit_abs_file_path, "w") as f:
        f.writelines(code_lines)
        
    # Step 6: Construct the predictions
    predict_snapshots = {}
    for abs_file_path, edits_in_file in edits.items():
        if edits_in_file == []:
            continue
        with open(abs_file_path, "r") as f:
            file_content = f.readlines()
            
        rel_file_path = os.path.relpath(abs_file_path, json_input["repo_dir"])
        if rel_file_path not in predict_snapshots:
            predict_snapshots[rel_file_path] = file_content
            
        line_idx = 0
        predict_snapshots[rel_file_path] = convert_rename_edits_to_snapshot(predict_snapshots[rel_file_path], rel_file_path, edits_in_file, logger)
                
    return predict_snapshots

def process_def_ref(service_info, json_input, LSP, MODELS, logger):
    repo_dir = json_input["repo_dir"]
    changed_files = json_input["changed_files"]
    target_edit = json_input["prior_edits"][-1]
    target_edit_file_path = os.path.join(repo_dir, target_edit["file_path"])
    
    position = {
        "line": service_info["name_range_start"][0] + target_edit["currently_start_at_line"],
        "character": (service_info["name_range_start"][1] + service_info["name_range_end"][1]) // 2
    }
    
    try:
        response = LSP.references(target_edit_file_path, position, wait_time=1)
    except:
        logger.error("[LSP] Error in finding references.")
        time.sleep(5)
        return None
    
    if response == []:
        return None
    else:
        response = response[0]
    
    if "error" in response or "result" not in response or response["result"] is None or response["result"] == []:
        return None
    
    # Filter out the last edit location
    identified_locations = []
    for location in response["result"]:
        if target_edit_file_path == location["uri"][7:] and location["range"]["start"]["line"] == position["line"]:
            # this will be the last prior edit
            continue
        location["file_path"] = os.path.relpath(location["uri"][7:], repo_dir)
        if location["file_path"] not in changed_files:
            continue
        identified_locations.append(location)
        
    logger.debug(f"[SUT] LSP def&ref retrieved the following locations: \n{json.dumps(identified_locations, indent=2)}")
    return LSP_location_to_predicted_snapshots("def&ref", identified_locations, json_input, MODELS, logger)
        
def process_code_clone(service_info, json_input, MODELS, logger):
    repo_dir = json_input["repo_dir"]
    language = json_input["language"]
    changed_files = json_input["changed_files"]
    
    clone_locations = find_clone_in_project(service_info, changed_files, repo_dir, threshold=80, lsp_style=True)
    return LSP_location_to_predicted_snapshots("clone", clone_locations, json_input, MODELS, logger)

def process_diagnose(json_input, LSP, MODELS, logger):
    repo_dir = json_input["repo_dir"]
    changed_files = json_input["changed_files"]
    prior_edits = json_input["prior_edits"]
    last_edit = prior_edits[-1]
    last_edit_region = {
        "file_path": last_edit["file_path"],
        "lines": list(range(last_edit["currently_start_at_line"], last_edit["currently_start_at_line"] + len(last_edit["after"])))
    }

    diagnose_locations = LSP.acquire_diagnose(changed_files, repo_dir, last_edit_region)
    return LSP_location_to_predicted_snapshots("diagnose", diagnose_locations, json_input, MODELS, logger)
 
def LSP_location_to_predicted_snapshots(LSP_name, identified_locations, json_input, MODELS, logger):
    assert LSP_name in ["def&ref", "clone", "diagnose"]
    repo_dir = json_input["repo_dir"]
    language = json_input["language"]
    prior_edits = json_input["prior_edits"]
    edit_description = json_input["edit_description"]
    changed_files = json_input["changed_files"]
    
    # Convert locations to sliding windows
    sliding_windows_with_info = diagnostic_2_sliding_windows(identified_locations, repo_dir)
    sliding_windows = [sliding_window["code_window"] for sliding_window in sliding_windows_with_info]
    
    if sliding_windows == []:
        return None
    
    # Prepare prior edit hunks
    prior_edit_hunks = []
    for prior_edit in prior_edits:
        prior_edit_hunk = construct_edit_hunk(prior_edit, repo_dir, language, logger, expect_old_code=False)
        prior_edit_hunks.append(CodeWindow(prior_edit_hunk, "hunk"))
        
    # Convert sliding windows into Locator input dataset
    dataset = make_locator_dataset(sliding_windows, prior_edit_hunks, edit_description, MODELS["LOCATOR_TOKENIZER"], logger)
    dataloader = DataLoader(dataset, batch_size=16, shuffle=False)
    
    # Locator prediction
    predicted_labels, predicted_confidences = locator_predict(MODELS["LOCATOR"], MODELS["LOCATOR_TOKENIZER"], dataloader, flatten = False)
    
    # Convert predicted labels to label_predictions
    label_predictions = {}
    for relative_file_path in changed_files:
        absolute_file_path = os.path.join(repo_dir, relative_file_path)
        with open(absolute_file_path, "r") as f:
            file_content = f.readlines()
        label_predictions[relative_file_path] = {
            "inline_predictions": ["<keep>"] * len(file_content),
            "inline_confidences": [1.0] * len(file_content),
            "inter_predictions": ["<null>"] * (len(file_content) + 1),
            "inter_confidences": [1.0] * (len(file_content) + 1),
            "inline_service": ["normal"] * len(file_content),
            "inter_service": ["normal"] * (len(file_content) + 1)
        }
    find_location = False
    for window_info, predicted_label, predicted_confidence in zip(sliding_windows_with_info, predicted_labels, predicted_confidences):
        file_path = window_info["file_path"]
        start_line = window_info["start_line_idx"]
        end_line = start_line + len(window_info["code_window"])
        if file_path not in changed_files:
            continue
        
        predicted_inter_labels = [label for i, label in enumerate(predicted_label) if i % 2 == 0]
        predicted_inline_labels = [label for i, label in enumerate(predicted_label) if i % 2 == 1]
        predicted_inter_confidences = [confidence for i, confidence in enumerate(predicted_confidence) if i % 2 == 0]
        predicted_inline_confidences = [confidence for i, confidence in enumerate(predicted_confidence) if i % 2 == 1]
        
        for label_idx, line_idx in enumerate(range(start_line, end_line)):
            label_predictions[file_path]["inline_predictions"][line_idx] = predicted_inline_labels[label_idx]
            label_predictions[file_path]["inline_confidences"][line_idx] = predicted_inline_confidences[label_idx]
            if predicted_inline_labels[label_idx] != "<keep>":
                # print(f"{file_path}:{line_idx} have label: {predicted_inline_labels[label_idx]}")
                label_predictions[file_path]["inline_service"][line_idx] = LSP_name
                find_location = True
            
        for label_idx, line_idx in enumerate(range(start_line, end_line + 1)):
            label_predictions[file_path]["inter_predictions"][line_idx] = predicted_inter_labels[label_idx]
            label_predictions[file_path]["inter_confidences"][line_idx] = predicted_inter_confidences[label_idx]
            if predicted_inter_labels[label_idx] != "<null>":
                label_predictions[file_path]["inter_service"][line_idx] = LSP_name
                find_location = True
    
    if not find_location:
        return None
    
    predicted_snapshots = edit_location_2_snapshots(label_predictions, repo_dir, prior_edit_hunks, edit_description, language, MODELS["GENERATOR"], MODELS["GENERATOR_TOKENIZER"], logger)
    
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[SUT] Edit recommendation snapshots via {LSP_name} + locator + generator are saved to debug/TRACE_{LSP_name}_recommendation_snapshots.json")
        os.makedirs("debug", exist_ok=True)
        with open(f"debug/TRACE_{LSP_name}_recommendation_snapshots.json", "w", encoding="utf-8") as f:
            json.dump(predicted_snapshots, f, indent=2)
    
    return predicted_snapshots
   
def convert_rename_edits_to_snapshot(content_lines, file_path, edits_in_file, logger):
    # Group edits by line number
    edits_by_line = defaultdict(list)
    for edit in edits_in_file:
        line_num = edit['range']['start']['line']
        edits_by_line[line_num].append(edit)
    
    # Sort edits within each line by character position (reverse order for safe replacement)
    for line_num in edits_by_line:
        edits_by_line[line_num].sort(key=lambda e: e['range']['start']['character'], reverse=True)
    
    sorted_lines = sorted(edits_by_line.keys())
    
    snapshot = []
    current_line = 0
    
    # Group consecutive edited lines
    i = 0
    while i < len(sorted_lines):
        line_num = sorted_lines[i]
        
        # Add unchanged lines before this edit
        if current_line < line_num:
            snapshot.append(content_lines[current_line:line_num])
        
        # Find consecutive edited lines
        consecutive_start = line_num
        consecutive_end = line_num
        while i + 1 < len(sorted_lines) and sorted_lines[i + 1] == consecutive_end + 1:
            i += 1
            consecutive_end = sorted_lines[i]
        
        # Build before and after for consecutive lines
        before_lines = []
        after_lines = []
        
        for ln in range(consecutive_start, consecutive_end + 1):
            line_content = content_lines[ln]
            before_lines.append(line_content)
            after_line = line_content
            
            for edit in edits_by_line[ln]:
                start_char = edit['range']['start']['character']
                end_char = edit['range']['end']['character']
                try:
                    assert line_content[start_char:end_char] == edit['oldText']
                except:
                    logger.error(f"[SUT] Mismatch in expected text for edit at {file_path}:{ln}, character {start_char}~{end_char} expected `{edit['oldText']}`, found `{line_content[start_char:end_char]}`")
                    raise AssertionError
                
                after_line = after_line[:start_char] + edit['newText'] + after_line[end_char:]
            
            after_lines.append(after_line)
        
        snapshot.append({
            'before': before_lines,
            'after': after_lines,
            'confidence': 1.0
        })
        
        current_line = consecutive_end + 1
        i += 1
    
    # Add remaining unchanged lines
    if current_line < len(content_lines):
        snapshot.append(content_lines[current_line:])
    
    return snapshot
