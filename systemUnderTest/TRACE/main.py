import json

from .Invoker import *
from .Locator import *
from .Generator import *
from .TRACE import TRACE

# Global constants
LSP = None
MODELS = {
    "INVOKER": None,
    "LOCATOR": None,
    "GENERATOR": None,
    "INVOKER_TOKENIZER": None,
    "LOCATOR_TOKENIZER": None,
    "GENERATOR_TOKENIZER": None
}

def main(json_input: dict):
    if json_input["status"] == "init":
        return setup(json_input)
    elif json_input["status"] == "suggestion(location+content)":
        return subsequent_edit_recommendation(json_input)
    elif json_input["status"] == "suggestion(content)":
        return generate_edit_solution(json_input)
    elif json_input["status"] == "end":
        return end(json_input)
    
    
def setup(json_input: dict):
    """
    Setup TRACE environment here, including: 
        - LSP server;
        - ML models;
        - any other necessary components.
        
    Args: 
        json_input (dict): The input JSON containing necessary information, including keys: ['id', 'language', 'project_name', 'status', 'repo_dir', 'prior_edits', 'edit_description']
        
    Returns:
        None
    """
    global LSP, MODELS, EXIST_DIAGNOSE_MSG
    
    logger = json_input.pop("logger")
    logger.debug(f"[SUT:TRACE] Setup json input: \n{json.dumps(json_input, indent=2)}")
    language = json_input["language"]
    repo_dir = json_input["repo_dir"]
    changed_files = json_input["changed_files"]
    
    # Setup LSP server
    if language == "python":
        from libs.LSPs.py_lsp import PyLanguageServer
        LSP = PyLanguageServer(logger=logger)
    elif language == "java":
        from libs.LSPs.java_lsp import JavaLanguageServer
        LSP = JavaLanguageServer(logger=logger)
    elif language == "go":
        from libs.LSPs.go_lsp import GoLanguageServer
        LSP = GoLanguageServer(logger=logger)
    elif language in ["javascript", "typescript"]:
        from libs.LSPs.jsts_lsp import TsLanguageServer
        LSP = TsLanguageServer(language, logger=logger)
    
    # Initialize LSP with the repository directory
    max_retries = 3
    retry_delay = 10  # seconds
    for attempt in range(max_retries):
        try:
            LSP.initialize(repo_dir)
            logger.info(f"[SUT:TRACE] LSP server ({language}) for project {repo_dir} initialized successfully.")
            break
        except Exception as e:
            logger.error(f"[SUT:TRACE] Failed to start LSP server for {language} on attempt {attempt + 1}: {e}")
            logger.info(f"[SUT:TRACE] Retrying to start LSP server in {retry_delay} seconds ...")
            time.sleep(retry_delay)
            if attempt == max_retries - 1:
                raise e
    
    LSP.open_in_batch(changed_files)
    # Obtain the initial diagnose messages that should be ignored
    LSP.acquire_diagnose(changed_files, {})
    
    # Setup neural models
    if MODELS["INVOKER"] is None:
        MODELS["INVOKER"], MODELS["INVOKER_TOKENIZER"] = load_invoker()
        logger.info("[SUT:TRACE] Invoker model loaded successfully.")
    
    if MODELS["LOCATOR"] is None:
        MODELS["LOCATOR"], MODELS["LOCATOR_TOKENIZER"] = load_locator()
        logger.info("[SUT:TRACE] Locator model loaded successfully.")
    
    if MODELS["GENERATOR"] is None:
        MODELS["GENERATOR"], MODELS["GENERATOR_TOKENIZER"] = load_generator()
        logger.info("[SUT:TRACE] Generator model loaded successfully.")
    
    
def subsequent_edit_recommendation(json_input: dict):
    """
    Provide subsequent edit recommendation (both location and content of the edit) based on the given context.
    
    Args:
        json_input (dict): The input JSON containing necessary information, including keys: ['logger', 'id', 'language', 'project_name', 'status', 'repo_dir', 'prior_edits', 'edit_description']
    Returns:
        predict_snapshots (dict): The predicted snapshots, with relative file path as keys, and snapshot as content.
        The snapshot (list[list[str]|dict]): A list containing unchanged lines of code (list[str]) and edits (dict). The edit dict contains keys: ['before', 'after', 'confidence'].
    """
    
    global LSP, MODELS
    logger = json_input.pop("logger")
    logger.debug(f"[SUT:TRACE] Subsequent edit recommendation json input: {json.dumps(json_input, indent=2)}")
    repo_dir = json_input["repo_dir"]
    changed_files = json_input["changed_files"]
    prior_edits = json_input["prior_edits"]
    edit_description = json_input["edit_description"]
    language = json_input["language"]
    
    # Update LSP with the latest changed files
    for file_path in changed_files:
        abs_file_path = os.path.normpath(os.path.join(repo_dir, file_path))
        LSP.did_change(abs_file_path)
        
    # Convert prior edits from dict to CodeWindow objects
    prior_edit_hunks = []
    for prior_edit in prior_edits:
        prior_edit_hunk = construct_edit_hunk(prior_edit, repo_dir, language, logger, expect_old_code=False)
        prior_edit_hunks.append(CodeWindow(prior_edit_hunk, "hunk"))
    
    # First, let invoker decide whether to invoke tools or not
    TRACE_predictions = TRACE(json_input, MODELS, LSP, logger)
    if TRACE_predictions is not None and len(TRACE_predictions) > 0:
        return TRACE_predictions
    
    # If not, use traditional project scanning methods to suggest subsequent edit
    label_predictions = {}
    for file_path in tqdm(
        changed_files,
        desc = f"{datetime.now().strftime('%Y/%m/%d %H:%M:%S')} - INFO     - __main__ -   [SUT:TRACE] Scanning files for next edit location"
    ):
        abs_file_path = os.path.normpath(os.path.join(repo_dir, file_path))
        with open(abs_file_path, "r", encoding="utf-8") as f:
            content = f.readlines()
            
        # Split content into windows of 10 lines of code
        sliding_windows = split_file_into_windows(content, MODELS["LOCATOR_TOKENIZER"])
        # Make locator dataset
        input_dataset = make_locator_dataset(sliding_windows, prior_edit_hunks, edit_description, MODELS["LOCATOR_TOKENIZER"], logger)
        dataloader = DataLoader(input_dataset, batch_size=16, shuffle=False)

        # Locator inference
        locator_results = locator_predict(MODELS["LOCATOR"], MODELS["LOCATOR_TOKENIZER"], dataloader, flatten=True)
        
        try:
            assert len(locator_results["inline_predictions"]) == len(content)
        except:
            logger.error(f"[SUT:TRACE] Locator predictions length {len(locator_results['inline_predictions'])} does not match with file content length {len(content)} for file {file_path}.")
            raise AssertionError
        
        label_predictions[file_path] = locator_results
    
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[SUT:TRACE] Locator predictions are saved to debug/TRACE_locator_outputs.json.")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_locator_outputs.json", "w", encoding="utf-8") as f:
            json.dump(label_predictions, f, indent=2)
    
    # For each predicted region, generate edit content
    predicted_snapshots = edit_location_2_snapshots(label_predictions, repo_dir, prior_edit_hunks, edit_description, language, MODELS["GENERATOR"], MODELS["GENERATOR_TOKENIZER"], logger)
    
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[SUT:TRACE] Subsequent edit recommendation snapshots are saved to debug/TRACE_subsequent_edit_recommendation_snapshots.json")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_subsequent_edit_recommendation_snapshots.json", "w", encoding="utf-8") as f:
            json.dump(predicted_snapshots, f, indent=2)
            
    return predicted_snapshots

def generate_edit_solution(json_input: dict):
    """
    Generate the content of the edit based on the given location.
    
    Args:
        json_input (dict): The input JSON containing necessary information, including keys: ['logger', 'id', 'language', 'project_name', 'status', 'repo_dir', 'prior_edits', 'edit_description', 'target_edit']
    Returns:
        predict_snapshots (dict): The predicted snapshots, with relative file path as keys, and snapshot as content.
        The snapshot (list[list[str]|dict]): A list containing unchanged lines of code (list[str]) or edit (dict). The edit dict contains keys: ['before', 'after', 'confidence'].
    """
    global MODELS
    
    logger = json_input.pop("logger")
    logger.debug(f"[SUT:TRACE] Edit content generation json input: \n{json.dumps(json_input, indent=2)}")
    target_edit = json_input["target_edit"]
    repo_dir = json_input["repo_dir"]
    language = json_input["language"]
    prior_edits = json_input["prior_edits"]
    edit_description = json_input["edit_description"]
    
    target_edit_hunk = construct_edit_hunk(target_edit, repo_dir, language, logger, expect_old_code=True)
    target_edit_hunk = CodeWindow(target_edit_hunk, "hunk")
    prior_edit_hunks = []
    for prior_edit in prior_edits:
        prior_edit_hunk = construct_edit_hunk(prior_edit, repo_dir, language, logger, expect_old_code=False)
        prior_edit_hunks.append(CodeWindow(prior_edit_hunk, "hunk"))
    
    service_type, service_info = logic_gate([target_edit], language)
    
    input_dataset = formalize_generator_dataset([target_edit_hunk], edit_description, [service_type], prior_edit_hunks, MODELS["GENERATOR_TOKENIZER"], logger)
    
    if input_dataset is None:
        return None
    
    edit_solution = generator_inference(input_dataset, MODELS["GENERATOR"], MODELS["GENERATOR_TOKENIZER"])[0][0]
    logger.debug(f"[SUT:TRACE] Generated edit solutions: \n{edit_solution}")
    
    # convert edit solution to snapshot format
    pred_snapshots = {}
    abs_file_path = os.path.normpath(os.path.join(repo_dir, target_edit["file_path"]))
    with open(abs_file_path, "r", encoding="utf-8") as f:
        code_lines = f.readlines()
     
    target_edit_start_line_idx = target_edit["currently_start_at_line"]   
    target_edit_end_line_idx = target_edit["currently_start_at_line"] + len(target_edit["before"])
    
    segments = []
    if target_edit_start_line_idx > 0:
        segments.append(code_lines[:target_edit_start_line_idx])
            
    if target_edit_hunk.edit_type == "insert":
        segments.append({
            "before": [],
            "after": edit_solution.splitlines(keepends=True),
            "confidence": 1.0
        })
    else:
        segments.append({
            "before": code_lines[target_edit_start_line_idx:target_edit_end_line_idx],
            "after": edit_solution.splitlines(keepends=True),
            "confidence": 1.0
        })
        
    if target_edit_end_line_idx < len(code_lines):
        segments.append(code_lines[target_edit_end_line_idx:])
    pred_snapshots[target_edit["file_path"]] = segments
    
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[SUT:TRACE] Generator predicted snapshots are saved to debug/TRACE_generator_predicted_snapshots_for_single_edit.json.")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_generator_predicted_snapshots_for_single_edit.json", "w", encoding="utf-8") as f:
            json.dump(pred_snapshots, f, indent=2)
    
    return pred_snapshots
    

def end(json_input: dict):
    """
    End the session and clean up resources if necessary.
    
    Args:
        json_input (dict): The input JSON containing necessary information, including keys: ['logger', 'id', 'language', 'project_name', 'status', 'repo_dir', 'prior_edits', 'current_location_of_prior_edits', 'edit_description']
    Returns:
        None
    """
    global LSP
    
    logger = json_input.pop("logger")
    try:
        LSP.close()
    except:
        logger.error("[SUT:TRACE] Failed to close LSP server.")
        
    logger.info("[SUT:TRACE] Session ended and resources cleaned up.")