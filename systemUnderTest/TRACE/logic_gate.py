from .is_rename import is_rename_edit
from .is_defref import is_defref_edit
from .is_clone import is_clone_edit

def logic_gate(prev_edit_hunks: list, lang: str):
    code_before = "".join(prev_edit_hunks[-1]["before"])
    code_after = "".join(prev_edit_hunks[-1]["after"])

    rename_result = is_rename_edit(code_before, code_after, lang)
    if rename_result is not False:
        return "rename", rename_result

    refdef_result = is_defref_edit(code_before, code_after, lang)
    if refdef_result is not False:
        return "def&ref", refdef_result
    
    clone_result = is_clone_edit(prev_edit_hunks)
    if clone_result is not False:
        if clone_result is None:
            raise ValueError("clone_result is None")
        return "clone", clone_result
    else:
        return "normal", None

def merge_overlapping(pairs):
   groups = []
   visited = set()
   
   for i in range(len(pairs)):
       if i in visited:
           continue
           
       current_group = set()
       stack = [i]
       
       while stack:
           current = stack.pop()
           if current in visited:
               continue
               
           visited.add(current)
           current_group.update(pairs[current])
           
           for j in range(len(pairs)):
               if j not in visited and (pairs[j][0] in current_group or pairs[j][1] in current_group):
                   stack.append(j)
                   
       groups.append(sorted(current_group))
       
   return groups
    
def get_edit_type_in_batch(edits: list, lang: str):
    for edit in edits:
        code_before = "".join(edit["before"])
        code_after = "".join(edit["after"])
        rename_result = is_rename_edit(code_before, code_after, lang)
        if rename_result is not False:
            edit["edit_type"] = "rename"
            edit["edit_info"] = rename_result
            edit["edit_info"]["propagatable_edit_idx"] = [edit["idx"]]
            continue
        
        refdef_result = is_defref_edit(code_before, code_after, lang)
        if refdef_result is not False:
            edit["edit_type"] = "def&ref"
            edit["edit_info"] = refdef_result
            edit["edit_info"]["propagatable_edit_idx"] = [edit["idx"]]
            continue
        
        else:
            edit["edit_type"] = "normal"
    
    # add extra information: propagatable_edit_idx
    # propagatable_edit_idx: the idx of the edit that can be propagated to the target edit
    # e.g.: sharing the same rename, def&ref, clone
    
    # First deal with rename
    rename_patterns = {}
    for idx1, edit1 in enumerate(edits[:-1]):
        if edit1["edit_type"] != "rename":
            continue
        rename_map1 = edit1["edit_info"]["map"]
        for idx2, edit2 in enumerate(edits[idx1+1:], start=idx1+1):
            if edit2["edit_type"] != "rename":
                continue
            rename_map2 = edit2["edit_info"]["map"]
            common_map = rename_map1.items() & rename_map2.items()
            for old_name, new_name in common_map:
                if f"{old_name}->{new_name}" not in rename_patterns:
                    rename_patterns[f"{old_name}->{new_name}"] = []
                rename_patterns[f"{old_name}->{new_name}"].extend([idx1, idx2])
         
    for edit_idx, edit in enumerate(edits):
        if edit["edit_type"] != "rename":
            continue
        for map_pattern, same_pattern_edit_idxs in rename_patterns.items():
            if edit_idx in same_pattern_edit_idxs:
                edit["edit_info"]["propagatable_edit_idx"].extend(same_pattern_edit_idxs)
        
        edit["edit_info"]["propagatable_edit_idx"] = list(set(edit["edit_info"]["propagatable_edit_idx"]))
    
    # Then deal with def&ref
    ref_def_pairs = []
    for idx1, edit1 in enumerate(edits[:-1]):
        if edit1["edit_type"] != "def&ref":
            continue
        func_name1 = edit1["edit_info"]["name"]
        for idx2, edit2 in enumerate(edits[idx1+1:], start=idx1+1):
            if edit2["edit_type"] != "def&ref":
                continue
            func_name2 = edit2["edit_info"]["name"]
            if func_name1 == func_name2:
                ref_def_pairs.append((idx1, idx2))
    
    ref_def_groups = merge_overlapping(ref_def_pairs)
    for group in ref_def_groups:
        for idx in group:
            edits[idx]["edit_info"]["propagatable_edit_idx"].extend(group)
            edits[idx]["edit_info"]["propagatable_edit_idx"] = list(set(edits[idx]["edit_info"]["propagatable_edit_idx"]))

    # Finally deal with clone
    clone_pairs = []
    for idx, tgt_edit in enumerate(edits):
        tgt_edit_code_before = "".join(tgt_edit["before"])
        if tgt_edit_code_before.strip() == "":
            continue
        
        for other_edit in edits[idx+1:]:
            other_edit_code_before = "".join(other_edit["before"])
            if other_edit_code_before.strip() == "":
                continue
            
            if is_clone_edit([tgt_edit, other_edit]) and \
            tgt_edit["edit_type"] in ["normal", "clone"] and \
            other_edit["edit_type"] in ["normal", "clone"]:
                tgt_edit["edit_type"] = "clone"
                other_edit["edit_type"] = "clone"
                clone_pairs.append((other_edit["idx"], tgt_edit["idx"]))
                continue
    
    clone_groups = merge_overlapping(clone_pairs)
    for group in clone_groups:
        for idx in group:
            edits[idx]["edit_info"] = {
                "propagatable_edit_idx": group
            }
    
    # Final check
    for edit_idx, edit in enumerate(edits):
        if edit["edit_type"] != "normal":
            assert len(edit["edit_info"]["propagatable_edit_idx"]) > 0
    return edits
