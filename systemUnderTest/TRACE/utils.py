import os
import platform

from rank_bm25 import BM25Okapi
from .code_window import CodeWindow
from tree_sitter import Language, Parser
from transformers import RobertaTokenizer

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TREE_SITTER_PATH = os.path.normpath(os.path.join(CURRENT_DIR, "..", "..", "libs", "tree-sitter"))

def extract_edits(snapshot):
    edits = []
    line_idx = 0
    for window in snapshot:
        if isinstance(window, list):
            line_idx += len(window)
            continue

        # else if is dict
        edits.append(window)
        
    return edits

def merge_snapshot(A_snapshot, B_snapshot):
    """
    Merge edits for a single file, handling overlaps by selecting higher confidence.

    Structure of file_data: A list where:
    - Index 0: List of lines before first edit region
    - Index 1: Edit metadata dict (with 'line_idxs', 'confidence', 'before', 'after', etc.)
    - Index 2: List of lines after first edit region
    - (Pattern can repeat for multiple edits)

    Args:
        A_file_data: List from A_predictions for this file
        B_file_data: List from B_predictions for this file

    Returns:
        Merged list with non-overlapping edits
    """
    A_edits = extract_edits(A_snapshot)
    B_edits = extract_edits(B_snapshot)
    
    merged_edits = []
    A_idx, B_idx = 0, 0
    while A_idx < len(A_edits) and B_idx < len(B_edits):
        A_edit = A_edits[A_idx]
        B_edit = B_edits[B_idx]
        
        if A_edit["before"] == [] or B_edit["before"] == []:
            raise ValueError("not well handled yet")
        
        A_lines = set(A_edit['line_idxs'])
        B_lines = set(B_edit['line_idxs'])
        
        if A_lines.isdisjoint(B_lines):
            # No overlap, add both edits based on their idx
            if A_edit['line_idxs'][0] < B_edit['line_idxs'][0]:
                merged_edits.append(A_edit)
                A_idx += 1
            else:
                merged_edits.append(B_edit)
                B_idx += 1
        else:
            # if one is insert, and one is edit, and insert edit at line idx == replace edit line idx[0]
            if A_edit["before"] == [] and A_edit["line_idxs"][0] == B_edit["line_idxs"][0]:
                merged_edits.append(A_edit)
                merged_edits.append(B_edit)
                A_idx += 1
                B_idx += 1
                continue
            if B_edit["before"] == [] and B_edit["line_idxs"][0] == A_edit["line_idxs"][0]:
                merged_edits.append(B_edit)
                merged_edits.append(A_edit)
                A_idx += 1
                B_idx += 1
                continue
            # Overlap exists, choose higher confidence
            if A_edit['confidence'] >= B_edit['confidence']:
                merged_edits.append(A_edit)
            else:
                merged_edits.append(B_edit)
            A_idx += 1
            B_idx += 1  # move both pointers, as we resolved the conflict
            
    if A_idx < len(A_edits):
        merged_edits.extend(A_edits[A_idx:])
    if B_idx < len(B_edits):
        merged_edits.extend(B_edits[B_idx:])
        
    before_version = []
    for window in A_snapshot:
        if isinstance(window, list):
            before_version.extend(window)
        else:
            before_version.extend(window['before'])
            
    merged_snapshot = []
    for edit_idx, edit in enumerate(merged_edits):
        if edit_idx == 0:
            # Add the unchanged lines before first edit
            merged_snapshot.append(before_version[:edit['line_idxs'][0]])

        # Add the edit itself
        merged_snapshot.append(edit)
        # Add the unchanged lines between this edit and the next edit
        if edit_idx != len(merged_edits) - 1:
            next_edit = merged_edits[edit_idx + 1]
            unchanged_lines = before_version[edit['line_idxs'][-1]+1:next_edit['line_idxs'][0]]
            merged_snapshot.append(unchanged_lines)
        else:
            # Add the unchanged lines after the last edit
            unchanged_lines = before_version[edit['line_idxs'][-1]+1:]
            if unchanged_lines:
                merged_snapshot.append(unchanged_lines)
        
    return merged_snapshot

def merge_snapshots(A_snapshots, B_snapshots):
    """
    Merge two predicted snapshots.

    Rules:
    - If a file only exists in one prediction, include it as-is
    - If a file exists in both predictions:
        - Combine edits from both
        - If edits overlap (same line_idxs), select the one with higher confidence
        - If confidence is equal, prefer A_predictions

    Args:
        A_predictions: Dict of {file_path: [before_lines, edit_metadata, after_lines, ...]}
        B_predictions: Dict of {file_path: [before_lines, edit_metadata, after_lines, ...]}

    Returns:
        merged_predictions: Dict with same structure
    """
    merged_snapshots = {}

    # Handle empty cases
    if not A_snapshots:
        return B_snapshots
    if not B_snapshots:
        return A_snapshots

    # Get all file paths
    all_files = set(A_snapshots.keys()) | set(B_snapshots.keys())

    for file_path in all_files:
        # Case 1: File only in A
        if file_path not in B_snapshots:
            merged_snapshots[file_path] = A_snapshots[file_path]
            continue

        # Case 2: File only in B
        if file_path not in A_snapshots:
            merged_snapshots[file_path] = B_snapshots[file_path]
            continue

        # Case 3: File in both - need to merge edits
        merged_snapshots[file_path] = merge_snapshot(
            A_snapshots[file_path],
            B_snapshots[file_path]
        )

    return merged_snapshots
       
def parse(code: str, language: str):
    global TREE_SITTER_PATH
    assert language in ["go", "javascript", "typescript", "python", "java"]
    system = platform.system().lower()
    if system == "darwin":
        build_dir = os.path.join(TREE_SITTER_PATH, "macos_build")
    elif system == "linux":
        build_dir = os.path.join(TREE_SITTER_PATH, "linux_build")
    elif system == "windows":
        build_dir = os.path.join(TREE_SITTER_PATH, "windows_build")
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    so_path = os.path.join(build_dir, "my-languages.so")

    if not os.path.exists(so_path):
        Language.build_library(
            # Store the library in the `build` directory
            so_path,

            # Include one or more languages
            [
                os.path.join(TREE_SITTER_PATH, "tree-sitter-go"),
                os.path.join(TREE_SITTER_PATH, "tree-sitter-javascript"),
                os.path.join(TREE_SITTER_PATH, "tree-sitter-typescript/typescript"),
                os.path.join(TREE_SITTER_PATH, "tree-sitter-python"),
                os.path.join(TREE_SITTER_PATH, "tree-sitter-java"),
            ]
        )
    parser = Parser()
    parser.set_language(Language(so_path, language))
    tree = parser.parse(bytes(code, "utf8"))
    return tree

def select_prior_edits(query: str, prev_edit_hunks: list[CodeWindow], tokenizer: RobertaTokenizer) -> list[CodeWindow]:
    """
    Func: 
        Given a target hunk and a list of other hunks, select the prior edits from the other hunks
    Args:
        query (str): the target hunk.before_edit_window(split_by_line=False)
        prev_edit_hunks: list[CodeWindow], the other hunks
        tokenizer: RobertaTokenizer, the tokenizer
    Return:
        prior_edits: list[CodeWindow], the prior edits
    """
    choosen_hunk_ids = [hunk.idx for hunk in prev_edit_hunks] # index to hunk id
    tokenized_corpus = [tokenizer.tokenize("".join(hunk.before_edit_region()+hunk.after_edit_region())) for hunk in prev_edit_hunks]
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = tokenizer.tokenize(query)
    retrieval_code = bm25.get_top_n(tokenized_query, tokenized_corpus, n=3) 
    retrieved_index = [tokenized_corpus.index(i) for i in retrieval_code] # get index in choosen_hunk_ids
    prior_edit_id = [choosen_hunk_ids[idx] for idx in retrieved_index] # get corresponding hunk id
    prior_edits = []
    for id in prior_edit_id: # preserve the order
        prior_edits.append([hunk for hunk in prev_edit_hunks if hunk.idx == id][0])
    
    return prior_edits

def diagnostic_2_sliding_windows(diagnostics, repo_dir):
    """
    Func:
        Convert lsp diagnostics to sliding windows
    Input:
        diagnostics: list of dict:
            {
                "source": "pylint",
                "range": {
                    "start": {
                        "line": 21,
                        "character": 0
                    },
                    "end": {
                        "line": 21,
                        "character": 10
                    }
                },
                "message": "[unused-import] Unused import re",
                "severity": 2,
                "code": "W0611",
                "tags": [1],
                "file_path": "airflow/providers/amazon/aws/hooks/sagemaker.py"
            }
        repo_dir (str): directory of the repo
    """
    sliding_windows = []
    for diagnostic in diagnostics:
        sliding_window = {}
        absolute_file_path = os.path.join(repo_dir, diagnostic["file_path"])
        with open(absolute_file_path, "r") as f:
            file_content = f.readlines()
            
        start_line_idx = max(0, diagnostic["range"]["start"]["line"] - 3)
        end_line_idx = min(len(file_content), diagnostic["range"]["end"]["line"] + 5)
        
        try:
            assert len(file_content) >= end_line_idx > start_line_idx
        except:
            with open("debug/debug_diagnosic_2_sliding_windows.json", "w") as f:
                import json
                json.dump({
                    "diagnositc": diagnostic,
                    "repo_dir": repo_dir,
                }, f, indent=4)
            raise ValueError("Invalid line indices for sliding window extraction.")
        sliding_window["code_window"] = file_content[start_line_idx:end_line_idx]
        sliding_window["file_path"] = diagnostic["file_path"]
        sliding_window["start_line_idx"] = start_line_idx
        sliding_window["file_lines"] = len(file_content)
        sliding_windows.append(sliding_window)
    
    return sliding_windows
