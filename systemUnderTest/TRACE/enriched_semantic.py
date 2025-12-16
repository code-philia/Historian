import os
import platform
import logging

from .utils import parse
from rapidfuzz import fuzz
from tree_sitter import Language, Parser

logger = logging.getLogger("TRACE.enriched_semantic")

def merge_matched_position(common_positions):
    """
    Func:
        Given the matched replace blocks, merge the overlapped blocks
    Args:
        common_positions: list, the list of matched replace blocks
    Return:
        merged_positions: list, the list of merged replace blocks
    """
    def is_consecutive(numbers):
        if len(numbers) < 2:
            return True  # list with 0 or 1 element is considered consecutive

        for i in range(1, len(numbers)):
            if numbers[i] != numbers[i - 1] + 1:
                return False
        return True
    
    positions = [(position["before_at_line"], position["after_at_line"]) for position in common_positions]
    
    merged_positions = [positions[0]]
    for position in positions[1:]:
        to_merge_position_group_idx = []
        for mp_idx, mp in enumerate(merged_positions):
            if len(set(position[0]).intersection(set(mp[0]))) != 0 or len(set(position[1]).intersection(set(mp[1]))) != 0:
                to_merge_position_group_idx.append(mp_idx)
        if to_merge_position_group_idx == []:
            merged_positions.append((position[0], position[1]))
            continue
        to_merge_position = [merged_positions[idx] for idx in to_merge_position_group_idx] + [(position[0], position[1])]
        merged_old_position = list(set([line for lines in to_merge_position for line in lines[0]]))
        sorted_old_position = sorted(merged_old_position)
        merged_new_position = list(set([line for lines in to_merge_position for line in lines[1]]))
        sorted_new_position = sorted(merged_new_position)
        # if idx in sorted_old_position & sorted_new_position is continuous, then merge them
        if is_consecutive(sorted_old_position) and is_consecutive(sorted_new_position):
            merged_positions = [mp for idx, mp in enumerate(merged_positions) if idx not in to_merge_position_group_idx]
            merged_positions.append((sorted_old_position, sorted_new_position))
        else:
            return None # if the merged positions are not continuous, we believe the quality of the matched positions is not good, so we return None

    return merged_positions

def get_symbol_info(node, code_window):
    symbol_list = []
    if len(node.children) == 0:
        if node.type not in [",", ":", ".", ";", "(", ")", "[", "]", "{", "}"]:
            return [
                {
                    "text": node.text.decode("utf-8"),
                    "type": node.type,
                    "at_line": node.start_point[0],
                    "at_column": node.start_point[1]
                }
            ]
        elif code_window[node.start_point[0]].strip() == node.type:
            # when it is trivial, but takes the whole line, we should consider it
            return [
                {
                    "text": node.text.decode("utf-8"),
                    "type": node.type,
                    "at_line": node.start_point[0],
                    "at_column": node.start_point[1]
                }
            ]
        else:
            return []
    else:
        for child in node.children:
            symbol_list.extend(get_symbol_info(child, code_window))
    return symbol_list

def lcs(list1, list2):
    # Get the length of lists
    m, n = len(list1), len(list2)
    
    # Create a (m+1) x (n+1) 2D array initialized with 0
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    
    # Dynamic programming to calculate LCS
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if list1[i - 1]['text'] == list2[j - 1]['text'] and list1[i - 1]['type'] == list2[j - 1]['type']:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    
    # Backtrack to find LCS and merge elements
    merged_list = []
    i, j = m, n
    while i > 0 and j > 0:
        if list1[i - 1]['text'] == list2[j - 1]['text'] and list1[i - 1]['type'] == list2[j - 1]['type']:
            merged_element = {
                'text': list1[i - 1]['text'],
                'type': list1[i - 1]['type'],
                'before_at_line': list1[i - 1].get('at_line'),
                'before_at_column': list1[i - 1].get('at_column'),
                'after_at_line': list2[j - 1].get('at_line'),
                'after_at_column': list2[j - 1].get('at_column')
            }
            merged_list.append(merged_element)
            i -= 1
            j -= 1
        elif list1[i - 1]['type'] == list2[j - 1]['type'] and list1[i - 1]['type'] == "comment" and fuzz.ratio(list1[i - 1]['text'], list2[j - 1]['text']) > 80:
            # comment node is not a single indentifier, hence allow loose criterion
            merged_element = {
                'text': list1[i - 1]['text'],
                'type': list1[i - 1]['type'],
                'before_at_line': list1[i - 1].get('at_line'),
                'before_at_column': list1[i - 1].get('at_column'),
                'after_at_line': list2[j - 1].get('at_line'),
                'after_at_column': list2[j - 1].get('at_column')
            }
            merged_list.append(merged_element)
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    
    # Reverse the LCS output
    merged_list.reverse()
    return merged_list

def finer_grain_window(before: list[str], after: list[str], lang: str) -> dict:
    def return_delete_insert_blocks(before,after):
        blocks = [
            {
                "block_type": "delete",
                "before": before,
                "after": []
            }
        ]
        if "".join(after).strip() != "":
            blocks.append({
                "block_type": "insert",
                "before": [],
                "after": after
            })
        return blocks
    
    new_window = []
    before_str = "".join(before)
    after_str = "".join(after)
    before_tree = parse(before_str, lang)
    after_tree = parse(after_str, lang)
    
    before_symbols = get_symbol_info(before_tree.root_node, before)
    after_symbols = get_symbol_info(after_tree.root_node, after)
    matched_symbols = lcs(before_symbols, after_symbols)
    to_pop_idx = []
    for m_idx, m in enumerate(matched_symbols):
        if m["before_at_line"] >= len(before) or \
           m["before_at_line"] < 0 or \
           m["after_at_line"] >= len(after) or \
           m["after_at_line"] < 0:
            to_pop_idx.append(m_idx)
    matched_symbols = [matched_symbols[m_idx] for m_idx in range(len(matched_symbols)) if m_idx not in to_pop_idx]
    
    if matched_symbols == []:
        return return_delete_insert_blocks(before, after)
    for ms in matched_symbols:
        ms["before_at_line"] = [ms["before_at_line"]]
        ms["after_at_line"] = [ms["after_at_line"]]
        
    merged_positions = merge_matched_position(matched_symbols)
    if merged_positions is None:
        return return_delete_insert_blocks(before, after)
    filtered_merged_positions = [merged_positions[0]]
    for match_pos_idx, match_pos in enumerate(merged_positions[1:]):
        match_pos_idx += 1
        if match_pos[0][0] > filtered_merged_positions[-1][0][-1] and \
        match_pos[1][0] > filtered_merged_positions[-1][1][-1]:
            filtered_merged_positions.append(match_pos)
    
    if len(filtered_merged_positions) == 0:
        return return_delete_insert_blocks(before, after)
    skip_emtpy_insertion = 0
    for match_pos_idx, match_pos in enumerate(filtered_merged_positions):
        if match_pos_idx == 0:
            prev_old_end_line_idx = -1
            prev_new_end_line_idx = -1
        else:
            prev_old_end_line_idx = filtered_merged_positions[match_pos_idx-1][0][-1]
            prev_new_end_line_idx = filtered_merged_positions[match_pos_idx-1][1][-1]
        # take care of unmatched positions before this matched position
        if prev_old_end_line_idx + 1 < match_pos[0][0] and prev_new_end_line_idx + 1 < match_pos[1][0]:
            new_window.append({
                "block_type": "delete",
                "before": before[prev_old_end_line_idx+1:match_pos[0][0]],
                "after": []
            })
            if "".join(after[prev_new_end_line_idx+1:match_pos[1][0]]).strip() != "":
                new_window.append({
                    "block_type": "insert",
                    "before": [],
                    "after": after[prev_new_end_line_idx+1:match_pos[1][0]]
                })
            else:
                skip_emtpy_insertion += 1
        elif prev_old_end_line_idx + 1 < match_pos[0][0]:
            new_window.append({
                "block_type": "delete",
                "before": before[prev_old_end_line_idx+1:match_pos[0][0]],
                "after": []
            })
        elif prev_new_end_line_idx + 1 < match_pos[1][0]:
            if "".join(after[prev_new_end_line_idx+1:match_pos[1][0]]).strip() != "":
                new_window.append({
                    "block_type": "insert",
                    "before": [],
                    "after": after[prev_new_end_line_idx+1:match_pos[1][0]]
                })
            else:
                skip_emtpy_insertion += 1
        # take care of matched positions
        new_window.append({
            "block_type": "modify",
            "before": before[match_pos[0][0]:match_pos[0][-1]+1],
            "after": after[match_pos[1][0]:match_pos[1][-1]+1]
        })
        # take care of unmatched positions after last matched position
        if match_pos_idx == len(filtered_merged_positions) - 1:   
            if match_pos[0][-1] != len(before) - 1 and match_pos[1][-1] != len(after) - 1:
                new_window.append({
                    "block_type": "delete",
                    "before": before[match_pos[0][-1]+1:],
                    "after": []
                })
                if "".join(after[match_pos[1][-1]+1:]).strip() != "":
                    new_window.append({
                        "block_type": "insert",
                        "before": [],
                        "after": after[match_pos[1][-1]+1:]
                    })
                else:
                    skip_emtpy_insertion += 1
            elif match_pos[0][-1] != len(before) - 1:
                new_window.append({
                    "block_type": "delete",
                    "before": before[match_pos[0][-1]+1:],
                    "after": []
                })
            elif match_pos[1][-1] != len(after) - 1:
                if "".join(after[match_pos[1][-1]+1:]).strip() != "":
                    new_window.append({
                        "block_type": "insert",
                        "before": [],
                        "after": after[match_pos[1][-1]+1:]
                    })
                else:
                    skip_emtpy_insertion += 1

    totoal_block_before = 0
    totoal_block_after = 0
    for block in new_window:
        totoal_block_before += len(block["before"])
        totoal_block_after += len(block["after"])
        assert not (block["before"] == [] and block["after"] == [])
    try:
        assert totoal_block_before == len(before)
        assert totoal_block_after + skip_emtpy_insertion == len(after)
    except:
        raise AssertionError
    
    return new_window

def construct_edit_hunk(edit: dict, repo_dir: str, lang: str, expect_old_code):
    """
    Convert a git hunk into an enriched hunk
    
    Args:
        edit: dict, the edit to be converted
            "idx": int,
            "before": list of str, []
            "after": list of str, []
        repo_dir: str, the root directory (absolute) of the repository
        lang: str, the language of the file

    Returns:
        hunk: dict, the enriched hunk
    """
    abs_file_path = os.path.normpath(os.path.join(repo_dir, edit["file_path"]))
    edit_start_line_idx = edit["currently_start_at_line"]
    if expect_old_code:
        edit_end_line_idx = edit["currently_start_at_line"] + len(edit["before"])
    else:
        edit_end_line_idx = edit["currently_start_at_line"] + len(edit["after"])
    with open(abs_file_path, "r", encoding="utf-8") as f:
        code_lines = f.readlines()
        
    code_pre = code_lines[:edit_start_line_idx]
    code = code_lines[edit_start_line_idx: edit_end_line_idx]
    try:
        if expect_old_code:
            assert code == edit["before"]
        else:
            assert code == edit["after"]
    except:
        if expect_old_code:
            logger.error(f"The edit before content does not match with the file content at {abs_file_path}:{edit_start_line_idx}-{edit_end_line_idx}.\nExpected code:\n{''.join(edit['before'])}\nActual code:\n{''.join(code)}")
        else:
            logger.error(f"The edit after content does not match with the file content at {abs_file_path}:{edit_start_line_idx}-{edit_end_line_idx}.\nExpected code:\n{''.join(edit['after'])}\nActual code:\n{''.join(code)}")
        raise AssertionError
    code_suf = code_lines[edit_end_line_idx:]
    
    code_pre = code_pre if len(code_pre)<3 else code_pre[-3:]
    code_suf = code_suf if len(code_suf)<3 else code_suf[:3]
    
    if edit["before"] == [] and edit["after"] != []: # insert type
        hunk = {
            "idx": edit["idx"],
            "type": "insert",
            "code_window": code_pre + edit["before"] + code_suf,
            "inline_labels": ["keep"]*len(code_pre)+ ["keep"]*len(code_suf),
            "inter_labels": ["null"] * len(code_pre) + ["insert"] + ["null"] * len(code_suf),
            "before_edit": edit['before'],
            "after_edit": edit['after'],
            "edit_start_line_idx": max(0,edit_start_line_idx),
            "prefix_len": len(code_pre),
            "suffix_len": len(code_suf)
        }
        
    elif edit["before"] != [] and edit["after"] == []: # delete type
        hunk = {
            "idx": edit["idx"],
            "type": "delete",
            "code_window": code_pre + edit["before"] + code_suf,
            "inline_labels": ["keep"]*len(code_pre)+ ["delete"]* len(edit["before"]) + ["keep"]*len(code_suf),
            "inter_labels": ["null"] * (len(code_pre) + len(edit["before"]) + len(code_suf) + 1),
            "before_edit": edit['before'],
            "after_edit": edit['after'],
            "edit_start_line_idx": max(0,edit_start_line_idx),
            "prefix_len": len(code_pre),
            "suffix_len": len(code_suf)
        }
    
    else:
        code_blocks = finer_grain_window(edit["before"], edit["after"], lang)
        
        inline_labels = ["keep"] * len(code_pre)
        inter_labels = ["null"] * len(code_pre)
        inter = "null"
        for block in code_blocks:
            if block["block_type"] == "insert":
                inter = "insert"
            elif block["block_type"] == "delete":
                if inter == "block-split":
                    inter_labels.append("null")
                else: # only allow insert or null
                    inter_labels.append(inter)
                inter = "null"
                inline_labels += ["delete"] * len(block["before"])
                inter_labels += ["null"] * (len(block["before"]) - 1)
            elif block["block_type"] == "modify":
                inter_labels.append(inter)
                inter = "block-split"
                inline_labels += ["replace"] * len(block["before"])
                inter_labels += ["null"] * (len(block["before"]) - 1)
        if inter == "block-split":
            inter_labels.append("null")
        else:
            inter_labels.append(inter)
            
        inline_labels += ["keep"] * len(code_suf)
        inter_labels += ["null"] * len(code_suf)
        assert len(inline_labels) + 1 == len(inter_labels)
        hunk = {
            "idx": edit["idx"],
            "type": "replace",
            "code_window": code_pre + code_blocks + code_suf,
            "inline_labels": inline_labels,
            "inter_labels": inter_labels,
            "before_edit": edit['before'],
            "after_edit": edit['after'],
            "edit_start_line_idx": max(0,edit_start_line_idx),
            "prefix_len": len(code_pre),
            "suffix_len": len(code_suf)
        }
        
    return hunk
  