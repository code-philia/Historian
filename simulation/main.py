import os
import copy
import json
import random
import asyncio
import inspect
import logging

from .utils import *
from .commit import Commit
from dotenv import load_dotenv

current_path = os.path.abspath(os.path.dirname(__file__))
root_path = os.path.abspath(os.path.join(current_path, "../"))
load_dotenv(dotenv_path=os.path.join(root_path, ".env"))
REPOS_DIR = os.getenv("REPOS_DIR") # this directory should be the absolute path to the repository directory inside backend host
OUTPUT_DIR = os.getenv("OUTPUT_DIR")
SUT = os.getenv("SUT")
FLOW_ANALYSIS_ENABLED = os.getenv("FLOW_ANALYSIS", "False").lower() in ("true", "1", "t", "y", "yes")
os.makedirs(REPOS_DIR, exist_ok=True)

# Global variables
COMMIT = None

logging.basicConfig(format = '%(asctime)s - %(levelname)-8s - %(name)-20s -   %(message)s',
                    datefmt = '%Y/%m/%d %H:%M:%S',
                    # level = logging.DEBUG)
                    level = logging.INFO)
logger = logging.getLogger("FRAMEWORK.main")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

def call_sut_main(sut_module, json_input):
    """
    Unified function to call SUT.main whether it's async or sync.

    Args:
        sut_module: The imported SUT module
        json_input: The input dictionary to pass to main

    Returns:
        The result from SUT.main
    """
    if inspect.iscoroutinefunction(sut_module.main):
        # It's an async function, run it with asyncio
        return asyncio.run(sut_module.main(json_input))
    else:
        # It's a regular function, call it normally
        return sut_module.main(json_input)

def main(json_input: dict):
    global COMMIT, FLOW_ANALYSIS_ENABLED
    
    commit_url = json_input["commit_url"]
    language = json_input["language"]
    system_under_test = json_input["system_under_test"]
    status = json_input["status"]

    if system_under_test == "TRACE":
        import systemUnderTest.TRACE.main as SUT
    elif system_under_test == "AgenticTRACE":
        import systemUnderTest.AgenticTRACE.main as SUT

    if status == "init":
        logger.info(f"------------------------ Phase 1: Initialization ------------------------")
        # Parse edit hunks from given commit URL
        COMMIT = Commit(commit_url, REPOS_DIR, system_under_test)

        # If this commit under this system under test has been simulated before, return the previous results
        if len(COMMIT.SUT_prediction_records) == len(COMMIT.get_edits()):
            logger.info("This commit has been simulated before. Returning the previous results.")
            response_message = COMMIT.SUT_prediction_records[len(COMMIT.replay_progress)]
            COMMIT.replay_progress.append(COMMIT.simulation_order[len(COMMIT.replay_progress)])
            return response_message
        
        # Select init edit and update edits status
        init_edit_idx = random.choice(COMMIT.allowed_next_edit_idxs)
        COMMIT.update_edit_status(init_edit_idx, "simulated", True)
        COMMIT.update_allowed_as_next()

        # Setup the system under test
        call_sut_main(
            SUT,
            {
                "id": COMMIT.commit_sha,
                "language": language,
                "project_name": COMMIT.project_name,
                "status": "init",
                "repo_dir": COMMIT.repo_dir,
                "prior_edits": COMMIT.get_prior_edits(), # Prior edit is the init edit
                "edit_description": COMMIT.commit_message,
                "changed_files": list(COMMIT.commit_snapshots.keys()),
            }
        )

        logger.info(f"Successfully set up {system_under_test} as System Under Test.")

        # Prepare the initial pred snapshot, where only contain the init edit
        logger.info(f"Selected Edit {init_edit_idx} as the init edit for simulation.")
        pred_snapshots = copy.deepcopy(COMMIT.get_next_edit_snapshots(init_edit_idx))
        
        # Update the project status with the new edit index
        current_version = COMMIT.get_current_version()
        sync_project(current_version, COMMIT.repo_dir)

        response_message =  {
            "pred_snapshots": pred_snapshots,
            "gdth_snapshots": COMMIT.commit_snapshots,
            "evaluations": {
                    "flow_pattern": {
                        "flow_keeping": [init_edit_idx],
                        "flow_jumping": [],
                        "flow_breaking": [],
                        "flow_reverting": []
                    },
                    "matched_locations": [],
                },
            "status": "init",
            "next_edit_snapshots": pred_snapshots,
        }
        COMMIT.SUT_prediction_records.append(response_message)
        return response_message

    elif status == "suggestion(location+content)":
        # Environment check:
        # -----------------------------------------------------------
        if COMMIT is None:
            raise ValueError("COMMIT is not initialized. Please run the init step first.")
        edits = COMMIT.get_edits()
        unsimulated_edits = [edit for edit in edits if edit["simulated"] == False]
        # Check 1: commit URL matches
        assert COMMIT.commit_url == json_input["commit_url"], "[ERROR:SIM] At src/simulation/main.py: main(), Commit URL mismatch. Please ensure the same commit is used for both init and suggestion steps."

        # If this commit under this system under test has been simulated before, return the previous results
        if len(COMMIT.SUT_prediction_records) == len(COMMIT.get_edits()):
            logger.info("This commit has been simulated before. Returning the previous results.")
            response_message = COMMIT.SUT_prediction_records[len(COMMIT.replay_progress)]
            COMMIT.replay_progress.append(COMMIT.simulation_order[len(COMMIT.replay_progress)])
            return response_message
        
        # Check 2: exist edits to simulate
        try:
            assert unsimulated_edits != []
        except AssertionError:
            logger.error("[SIM] At src/simulation/main.py: main(), No unsimulated edits found. Please check the commit snapshot.")
            raise Exception
        
        # Print current simulation status
        COMMIT.simulation_status()
        # -----------------------------------------------------------


        # Acquire subsequent edit recommendation from system under test
        # NOTE: pred_snapshots are a comparison between: 
        # NOTE: Current simulation status V.S. suggested edit version
        # NOTE: Not the commit base version V.S. suggested edit version
        # -----------------------------------------------------------
        logger.info(f"------------- Phase 2 - Step A: Request full recommendation -------------")
        json_input = {
            "id": COMMIT.commit_sha,
            "language": language,
            "project_name": COMMIT.project_name,
            "status": "suggestion(location+content)",
            "repo_dir": COMMIT.repo_dir,
            "prior_edits": COMMIT.get_prior_edits(),
            "edit_description": COMMIT.commit_message,
            "changed_files": list(COMMIT.commit_snapshots.keys()),
        }
        start = time.time()
        pred_snapshots = call_sut_main(SUT, json_input)
        end = time.time()
        time_cost = end - start
        logger.info(f"Time taken for end-to-end subsequent edit recommendation: {time_cost:.4f} seconds")
        pred_snapshots = indexing_edits_within_snapshots(pred_snapshots)
        # with open("debug.json", "w") as f:
        #     json.dump(pred_snapshots, f, indent=4)
        # -----------------------------------------------------------
        
        
        
        # Compare predicted snapshots with current ground-truth snapshots
        # -----------------------------------------------------------
        logger.info(f"------------ Phase 2 - Step B: Evaluate against ground truth ------------")
        # NOTE: COMMIT.get_not_simulated_edit_snapshots() returns the gold
        # NOTE: Current simulation status V.S commit head version
        # Match the predicted edits with the ground truth edits
        current_snapshots = copy.deepcopy(COMMIT.get_not_simulated_edit_snapshots()) # Deepcopy is necessary as edit status will have update
        pred_locations, gold_locations, matched_locations, pred_snapshots = match_suggestion_with_groundtruth(pred_snapshots, current_snapshots)
        
        # Evaluate the flow pattern of the suggested edits
        previously_applied_locations = copy.deepcopy(COMMIT.get_previously_applied_locations()) # Deepcopy is necessary as edit status will have update
        flow_pattern = evaluate_flow_pattern(pred_locations, matched_locations, previously_applied_locations)
        logger.info(f"Flow pattern: {json.dumps(flow_pattern)}")
        
        # Evaluate traditional metrics
        traditional_metrics = evaluate_traiditional_metrics(pred_locations, gold_locations, matched_locations)
        traditional_metrics["latency"] = time_cost
        logger.info(f"Traditional metrics: {json.dumps(traditional_metrics)}")
        # -----------------------------------------------------------
        
        
        # Select next edit, if none matched, request edit content generation
        # -----------------------------------------------------------
        new_edit_idx = None
        # Find the first flow matching edit, assign as the subsequent edit
        for edit in matched_locations:
            if edit["flowKeeping"]:
                new_edit_idx = edit["matchWith"]
                traditional_metrics["BLEU-4"] = edit["BLEU-4"]
                COMMIT.update_edit_status(edit["matchWith"], "simulated", True)
                next_edit_snapshots = copy.deepcopy(COMMIT.get_next_edit_snapshots(new_edit_idx))
                logger.info(f"Suggestion matches with Edit {edit['matchWith']}, apply to project")
                logger.info(f"BLEU-4 score: {edit['BLEU-4']:.2f}")
                break
            
        # Otherwise, randomly select one from allowed next edit idxs as the subsequent edit, and request for edit generation
        if new_edit_idx is None:
            logger.info(f"--------- Phase 2 - Step C: Request edit content recommendation ---------")
            edits = COMMIT.get_edits()
            allowed_next_edit_idxs = [edit["idx"] for edit in edits if edit["allowed_as_next"] == True]
            # Select randomly from allowed next edits if any
            if len(allowed_next_edit_idxs) > 0:
                new_edit_idx = random.choice(allowed_next_edit_idxs)
            else:
                new_edit_idx = random.choice([edit["idx"] for edit in edits if edit["simulated"] == False])
                
            target_edit = None
            for edit in edits:
                if edit["idx"] == new_edit_idx:
                    target_edit = edit
            logger.info(f"Select Edit {new_edit_idx} for edit generation and apply to project")
            logger.debug(f"Edit selected to apply: \n{json.dumps(target_edit, indent=2)}")
            try:
                assert target_edit is not None
            except:
                logger.error(f"Cannot find target edit for idx {new_edit_idx}.")
                raise AssertionError
                
            json_input = {
                "id": COMMIT.commit_sha,
                "language": language,
                "project_name": COMMIT.project_name,
                "status": "suggestion(content)",
                "repo_dir": COMMIT.repo_dir,
                "prior_edits": COMMIT.get_prior_edits(),
                "edit_description": COMMIT.commit_message,
                "changed_files": list(COMMIT.commit_snapshots.keys()),
                "target_edit": target_edit,
            }
            target_location_pred_snapshots = call_sut_main(SUT, json_input)
            next_edit_snapshots = copy.deepcopy(COMMIT.get_next_edit_snapshots(new_edit_idx))
            traditional_metrics["BLEU-4"] = calculate_bleu_between_snapshots(target_location_pred_snapshots, next_edit_snapshots)
            COMMIT.update_edit_status(new_edit_idx, "simulated", True)
            logger.info(f"Requested edit generation for Edit {new_edit_idx}, having BLEU-4 score {traditional_metrics['BLEU-4']:.2f}")
        # -----------------------------------------------------------
        
        
        # Maintain the project status for next simulation step
        # -----------------------------------------------------------
        logger.info(f"---------------- Phase 2 - Step D: Update project states ----------------")
        logger.info(f"Apply Edit {new_edit_idx} to the project repository for next simulation step.")
        # Update simulation progress for COMMIT
        COMMIT.update_allowed_as_next()
        # Update the project status with the new edit index
        current_version = COMMIT.get_current_version()
        sync_project(current_version, COMMIT.repo_dir)

        # If all edits have been simulated, return the final results
        edits = COMMIT.get_edits()
        unsimulated_edits = [edit for edit in edits if edit["simulated"] == False]
        # -----------------------------------------------------------
        
        
        # Prepare the response message
        # -----------------------------------------------------------
        if len(unsimulated_edits) == 0:
            logger.info(f"---------------------------- Phase 3: Report ----------------------------")
            logger.info("All edits have been simulated. Simulation completed.")
            # NOTE: The pred_snapshots are a comparison between:
            # NOTE: The current version V.S. suggested edit version
            # NOTE: The next_edit_snapshots are a comparsion between:
            # NOTE: The current version V.S. the next step version with next edit applied
            response_message =  {
                "pred_snapshots": pred_snapshots,
                "gdth_snapshots": COMMIT.commit_snapshots,
                "curr_gdth_snapshots": current_snapshots,
                "previously_applied_locations": previously_applied_locations,
                "evaluations": {
                    **flow_pattern, 
                    **traditional_metrics
                },
                "status": "done",
                "next_edit_snapshots": next_edit_snapshots,
                "partial_order_graph": COMMIT.get_partial_order_graph() if FLOW_ANALYSIS_ENABLED else None,
            }
            COMMIT.SUT_prediction_records.append(response_message)
            COMMIT.save_simulation_results()
        else:
            response_message =  {
                "pred_snapshots": pred_snapshots,
                "gdth_snapshots": COMMIT.commit_snapshots,
                "curr_gdth_snapshots": current_snapshots,
                "previously_applied_locations": previously_applied_locations,
                "evaluations": {
                    **flow_pattern,
                    **traditional_metrics
                },
                "status": "suggestion(location+content)",
                "next_edit_snapshots": next_edit_snapshots,
                "partial_order_graph": COMMIT.get_partial_order_graph() if FLOW_ANALYSIS_ENABLED else None,
            }
            COMMIT.SUT_prediction_records.append(response_message)
        # -----------------------------------------------------------
        
        
        return response_message

def sync_project(current_version, repo_dir):
    for rel_file_path, file_content in current_version.items():
        file_path = os.path.join(repo_dir, rel_file_path)
        with open(file_path, "w") as f:
            f.write("".join(file_content))

def match_suggestion_with_groundtruth(pred_snapshots, gdth_snapshots):
    pred_replace_locations, pred_insert_locations = snapshot_2_locations(pred_snapshots)
    gdth_replace_locations, gdth_insert_locations = snapshot_2_locations(gdth_snapshots)
    
    pred_locations = pred_replace_locations + pred_insert_locations
    for loc in pred_locations:
        try:
            assert "confidence" in loc and "suggestionRank" in loc
        except:
            logger.error("Predicted locations must contain 'confidence' and 'suggestionRank' fields.")
            raise AssertionError
    for loc in gdth_replace_locations + gdth_insert_locations:
        try:
            assert "idx" in loc and "allowed_as_next" in loc
        except:
            logger.error("Ground truth locations must contain 'idx' and 'allowed_as_next' fields.")
            raise AssertionError
        
    # match suggestions with ground truth edits
    matched_locations = []
    for pred_loc in pred_locations:
        pred_at_lines = pred_loc["atLines"]
        if pred_loc["editType"] == "replace":
            for gdth_loc in gdth_replace_locations:
                if gdth_loc["file_path"] != pred_loc["file_path"]:
                    continue
                gdth_at_lines = gdth_loc["atLines"]
                # Criteria for a match: line overlap > 50% and BLEU score of after content > 50
                if overlap_percentage(pred_at_lines, gdth_at_lines) > 0.5 and get_bleu(pred_loc["after"], gdth_loc["after"]) > 50:
                    matched_locations.append({
                        "atLines": pred_at_lines,
                        "editType": pred_loc["editType"],
                        "confidence": pred_loc["confidence"],
                        "suggestionRank": pred_loc["suggestionRank"],
                        "predIdx": pred_loc["idx"],
                        "matchWith": gdth_loc["idx"],
                        "flowKeeping": True if gdth_loc["allowed_as_next"] else False,
                        "BLEU-4": get_bleu(pred_loc["after"], gdth_loc["after"]),
                    })
                    for file_path, snapshots in pred_snapshots.items():
                        for window in snapshots:
                            if isinstance(window, list):
                                continue
                            if window["idx"] == pred_loc["idx"]:
                                window["matchWith"] = gdth_loc["idx"]
                                window["flowKeeping"] = True if gdth_loc["allowed_as_next"] else False
                                break
                    break
        else:
            for gdth_loc in gdth_insert_locations:
                if gdth_loc["file_path"] != pred_loc["file_path"]:
                    continue
                gdth_at_lines = gdth_loc["atLines"]
                if pred_at_lines == gdth_at_lines and get_bleu(pred_loc["after"], gdth_loc["after"]) > 50:
                    matched_locations.append({
                        "atLines": pred_at_lines,
                        "editType": pred_loc["editType"],
                        "confidence": pred_loc["confidence"],
                        "suggestionRank": pred_loc["suggestionRank"],
                        "predIdx": pred_loc["idx"],
                        "matchWith": gdth_loc["idx"],
                        "flowKeeping": True if gdth_loc["allowed_as_next"] else False,
                        "BLEU-4": get_bleu(pred_loc["after"], gdth_loc["after"]),
                    })
                    for file_path, snapshots in pred_snapshots.items():
                        for window in snapshots:
                            if isinstance(window, list):
                                continue
                            if window["idx"] == pred_loc["idx"]:
                                window["matchWith"] = gdth_loc["idx"]
                                window["flowKeeping"] = True if gdth_loc["allowed_as_next"] else False
                                break
                    break
    
    gold_locations = gdth_replace_locations + gdth_insert_locations
    allowed_locations = [loc for loc in gold_locations if loc["allowed_as_next"]]
    
    return pred_locations, gold_locations, matched_locations, pred_snapshots


def evaluate_flow_pattern(pred_locations, matched_locations, previously_applied_locations):
    flow_pattern = {
        "flow_keeping": [],
        "flow_jumping": [],
        "flow_breaking": [],
        "flow_reverting": []
    }
    
    # first classify the flow-keeping, jumping
    for match_loc in matched_locations:
        if match_loc["flowKeeping"]:
            flow_pattern["flow_keeping"].append(match_loc["predIdx"])
        else:
            flow_pattern["flow_jumping"].append(match_loc["predIdx"])
            
    # then classify the flow-reverting and flow-breaking
    for pred_loc in pred_locations:
        # Filter out predicted hunks already classified as flow-keeping and flow-breaking
        if pred_loc["idx"] in flow_pattern["flow_keeping"] + flow_pattern["flow_jumping"]:
            continue
        pred_at_lines = pred_loc["atLines"]
        is_flow_reverting = False
        for prev_edit_idx, prev_apply_loc in previously_applied_locations.items():
            if prev_apply_loc["file_path"] == pred_loc["file_path"] and overlap_percentage(pred_at_lines, prev_apply_loc["atLines"]) > 0.5:
                flow_pattern["flow_reverting"].append(pred_loc["idx"])
                is_flow_reverting = True
                break
        if not is_flow_reverting:
            flow_pattern["flow_breaking"].append(pred_loc["idx"])
    
    assert len(flow_pattern["flow_keeping"]) + len(flow_pattern["flow_jumping"]) + len(flow_pattern["flow_reverting"]) + len(flow_pattern["flow_breaking"]) == len(pred_locations)
    assert len(matched_locations) == len(flow_pattern["flow_keeping"]) + len(flow_pattern["flow_jumping"])
    
    return flow_pattern
   
   
def evaluate_traiditional_metrics(pred_locations, gold_locations, matched_locations):
    traditional_metrics = {}
    for ith in ["1", "3", "5", "10"]:
        key = f"tp@{ith}"
        traditional_metrics[key] = None
    
    for metric in ["precision", "recall", "f1_score", "tp", "fp", "fn"]:
        key = f"{metric}@all"
        traditional_metrics[key] = None
                
    # all
    ## precision
    num_flow_keeping = len([loc for loc in matched_locations if loc["flowKeeping"]])
    traditional_metrics["precision@all"] = num_flow_keeping / len(pred_locations) if len(pred_locations) > 0 else 0
    ## recall
    allowed_locations = [loc for loc in gold_locations if loc["allowed_as_next"]]
    traditional_metrics["recall@all"] = num_flow_keeping / len(allowed_locations) if len(allowed_locations) > 0 else 0
    ## f1_score
    traditional_metrics["f1_score@all"] = 2 * traditional_metrics["precision@all"] * traditional_metrics["recall@all"] / (traditional_metrics["precision@all"] + traditional_metrics["recall@all"]) if (traditional_metrics["precision@all"] + traditional_metrics["recall@all"]) > 0 else 0
    ## tp, fp, fn
    traditional_metrics["tp@all"] = num_flow_keeping
    traditional_metrics["fp@all"] = len(pred_locations) - num_flow_keeping
    traditional_metrics["fn@all"] = len(allowed_locations) - num_flow_keeping
    
    if len(pred_locations) > 0 and pred_locations[0]["confidence"] is None:
        logger.warning("Current SUT does not provide confidence scores or suggestion ranks for suggested edits. Top-k metrics cannot be calculated.")
        return
    
    # kth tp
    for k in ["1", "3", "5", "10"]:
        k_int = int(k)
        traditional_metrics[f"tp@{k}"] = len([loc for loc in matched_locations if loc["flowKeeping"] and loc["suggestionRank"] < k_int])
    
    return traditional_metrics


def calculate_bleu_between_snapshots(pred_snapshots, gdth_snapshots):
    """
    Assume each snapshot contains only one edit hunk. This function evaluates the BLEU-4 between them.
    If the location does not match, return 0. Otherwise, calculate the BLEU-4 score between the after contents.
    """
    for file_path, pred_snapshot in pred_snapshots.items():
        gold_snapshot = gdth_snapshots[file_path]
        try:
            assert len(pred_snapshot) == len(gold_snapshot)
        except:
            logger.error("Snapshot length mismatch when calculating BLEU-4.")
            with open("debug/calculate_bleu_between_snapshots.json", "w") as f:
                import json
                json.dump({
                    "pred_snapshot": pred_snapshot,
                    "gold_snapshot": gold_snapshot
                }, f, indent=4)
            raise AssertionError
        for pred_window, gold_window in zip(pred_snapshot, gold_snapshot):
            try:
                assert type(pred_window) == type(gold_window)
            except:
                logger.error("Snapshot window type mismatch when calculating BLEU-4.")
                raise AssertionError
            if isinstance(pred_window, list):
                if pred_window != gold_window:
                    return 0.0
            else:
                pred_after = pred_window["after"]
                gold_after = gold_window["after"]
                return get_bleu(pred_after, gold_after)


def rq3_origin(url, sut, language):
    init_input = {
        "commit_url": url,
        "language": language,
        "system_under_test": sut,
        "status": "init"
    }
    response = main(init_input)
    edit_num = 0
    for file_path, snapshot in response["gdth_snapshots"].items():
        for window in snapshot:
            if isinstance(window, dict):
                edit_num += 1

    for i in range(edit_num - 1):
        input = {
            "commit_url": url,
            "language": language,
            "system_under_test": sut,
            "status": "suggestion(location+content)"
        }
        main(input)


def rq3_flow_keeper(url, sut):
    # Find the simulation output of commit url
    commit_sha = url.split("/")[-1][:10]
    project_name = url.split("/")[-3]
    if os.path.exists(os.path.join(OUTPUT_DIR, f"{project_name}-{commit_sha}-{sut}-simulation-results-flow-keeper.json")):
        return None
    assert os.path.exists(os.path.join(OUTPUT_DIR, f"{project_name}-{commit_sha}-{sut}-simulation-results.json"))
    with open(os.path.join(OUTPUT_DIR, f"{project_name}-{commit_sha}-{sut}-simulation-results.json"), "r") as f:
        data = json.load(f)

    simulation_order = data["simulation_order"]
    gdth_snapshots = data["commit_snapshots"]
    edits = []
    for file_path, snapshot in gdth_snapshots.items():
        for window in snapshot:
            if isinstance(window, dict):
                edits.append(window)
    prior_edits = []
    for edit_idx in simulation_order:
        assert edits[edit_idx]["idx"] == edit_idx
        prior_edits.append(edits[edit_idx])

    all_evaluations = []
    for simulation_round, simulation_response in enumerate(data["SUT_prediction_records"][1:], start=1):
        pred_snapshots = simulation_response["pred_snapshots"]
        current_snapshots = simulation_response["curr_gdth_snapshots"]
        previously_applied_locations = simulation_response["previously_applied_locations"]
        reranked_pred_snapshots = rerank(pred_snapshots, prior_edits[:simulation_round])

        flow_pattern, traditional_metrics, matched_locations, _ = evaluate(reranked_pred_snapshots, current_snapshots, previously_applied_locations)

        assert traditional_metrics["fn"] >= simulation_response["evaluations"]["fn"], f"Have original {simulation_response['evaluations']['fn']} fns, but get {traditional_metrics['fn']} fns"
        evaluations = {
            "flow_pattern": flow_pattern,
            "matched_locations": matched_locations,
            "precision": traditional_metrics["precision"],
            "recall": traditional_metrics["recall"],
            "f1_score": traditional_metrics["f1_score"],
            "tp": traditional_metrics["tp"],
            "fp": traditional_metrics["fp"],
            "fn": traditional_metrics["fn"],
        }
        all_evaluations.append(evaluations)

    with open(os.path.join(OUTPUT_DIR, f"{project_name}-{commit_sha}-{sut}-simulation-results-flow-keeper.json"), "w") as f:
        json.dump(all_evaluations, f, indent=4)


if __name__ == "__main__":
    random.seed(42)
    with open("simulation/testset.json", "r") as f:
        test_urls = json.load(f)

    simulated_urls = 0
    for language, urls in test_urls.items():
        for url_info in urls:
            logger.info(f"Simulate commit: {url_info['commit_url']}")
            rq3_origin(url_info["commit_url"], SUT, language)
            simulated_urls += 1
            
