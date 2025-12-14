import os
from rapidfuzz import fuzz

def find_line_numbers(start_char_pos, end_char_pos, document_in_lines):
    line_idx = []  # List to store the line numbers containing the start and end characters
    current_char_count = 0  # Current total character count, used to determine character position
    
    for index, line in enumerate(document_in_lines):
        line_length = len(line)
        next_char_count = current_char_count + line_length  # Total character count at the next position
        
        if start_char_pos < next_char_count and end_char_pos > current_char_count:
            start = max(start_char_pos, current_char_count)
            end = min(next_char_count, next_char_count)

            # Calculate the size of the intersection
            intersection_length = max(0, end - start + 1)
            if intersection_length / len(line) > 0.75:
                line_idx.append(index)
        
        current_char_count = next_char_count  # Update the current total character count
    
    return line_idx#[1:-1]

def partial_scs(query, document, threshold, left, right):
    result = fuzz.partial_ratio_alignment(query, document, score_cutoff=threshold)
    if result is None or (result.src_end - result.src_start) / len(query) < 0.75:
        return []
    start_char = left + result.dest_start
    end_char = left + result.dest_end
    segments = [{
        'score': result.score,
        'start_char': start_char,
        'end_char': end_char
    }]
    left_segments = partial_scs(query, document[left : start_char], threshold, left=left, right=start_char)
    right_segments = partial_scs(query, document[end_char : right], threshold, left=end_char, right=right)
    return left_segments + segments + right_segments
  
def find_similar_code_segment(query, original_document_lines, threshold=80):
    """
    Func:
        Find all similar code segments in the document
    Args:
        query: str, the code segment to search
        document: str, the document to search in
        threshold: int, the similarity threshold
    Returns:
        found_segments: list, a list of found segments
                        {
                            "score": int, the similarity score,
                            "matched_lines": list, a list of line numbers where the code is found, indexed from 0
                        }
    """
    if len(query.strip()) < 15:
        return []
    
    found_segments = []
    document = "".join(original_document_lines)

    char_segments = partial_scs(query, document, threshold, left=0, right=len(document))
    for segment in char_segments:
        found_line_range = find_line_numbers(segment['start_char'], segment['end_char'], original_document_lines)
        if found_line_range == []:
            continue
        found_segments.append({
            "score": segment['score'],
            "matched_lines": found_line_range
        })
    return found_segments

def find_clone_in_project(query: str, changed_files, repo_dir, threshold=80, lsp_style=False):
    """
    Func:
        Find all similar code segments in the project
    Args:
        commit: the commit object
        query: str, the code segment to search
        threshold: int, the similarity threshold
        lsp_style: bool, whether to return the LSP style
    Returns:
        found_clones: list, a list of found segments
    """
    found_clones = []

    for file_path in changed_files:
        with open(os.path.join(repo_dir, file_path), "r") as f:
            document_lines = f.readlines()
        found_segments = find_similar_code_segment(query, document_lines, threshold)
        if found_segments != []:
            for segment in found_segments:
                assert segment["matched_lines"] != []
                
                if segment["score"] < threshold:
                    continue
                if not lsp_style:
                    found_clones.append({
                        "file_path": file_path,
                        "score": segment["score"],
                        "matched_lines": segment["matched_lines"]
                    })
                else:
                    found_clones.append({
                        "file_path": file_path,
                        "score": segment["score"],
                        "range": {
                            "start": {
                                "line": segment["matched_lines"][0],
                                "character": 0
                            },
                            "end": {
                                "line": segment["matched_lines"][-1],
                                "character": 0
                            }
                        }
                    })

    return found_clones

def is_clone_edit(prior_edits: list):
    """
    Func: 
        Check if the current edit is a clone edit
    Args:
        prior_edits: list, a list of prior edits
    Returns:
        bool | str: False if not a clone edit, otherwise the code before the current edit
    """
    if len(prior_edits) < 2:
        return False
    
    tgt_edit_code_before = "".join(prior_edits[-1]["before"])
    if tgt_edit_code_before.strip() == "":
        return False
    
    other_edit_code_before = "".join(prior_edits[-2]["before"])
    if other_edit_code_before.strip() == "":
        return False
    
    if fuzz.ratio(tgt_edit_code_before, other_edit_code_before) > 90:
        return tgt_edit_code_before

    return False