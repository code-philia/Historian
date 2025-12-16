import os
import time
import json
import torch
import logging

from dotenv import load_dotenv
from rank_bm25 import BM25Okapi
from .code_window import CodeWindow
from torch.utils.data import TensorDataset, DataLoader
from .enriched_semantic import construct_edit_hunk
from .Locator import combine_consecutive_locations
from .utils import select_prior_edits
from transformers import T5Config, T5ForConditionalGeneration, RobertaTokenizer

CURRENT_PATH = os.path.abspath(os.path.dirname(__file__))
PROJ_ROOT = os.path.abspath(os.path.join(CURRENT_PATH, "../../"))
load_dotenv(dotenv_path=os.path.join(PROJ_ROOT, ".env"))

GENERATOR_MODEL_PATH = os.getenv("GENERATOR_MODEL_PATH")
DEVICE = os.getenv("DEVICE")

logger = logging.getLogger("TRACE.Generator")

def load_generator():
    config_class, model_class, tokenizer_class = (T5Config, T5ForConditionalGeneration, RobertaTokenizer)
    
    config = config_class.from_pretrained("salesforce/codet5-base")
    generator_tokenizer = tokenizer_class.from_pretrained("salesforce/codet5-base")
    generator = model_class.from_pretrained("salesforce/codet5-base")
    new_special_tokens = ["<inter-mask>",
                          "<code_window>", "</code_window>", 
                          "<prompt>", "</prompt>", 
                          "<prior_edits>", "</prior_edits>",
                          "<edit>", "</edit>",
                          "<keep>", "<replace>", "<delete>",
                          "<null>", "<insert>", "<block-split>",
                          "<replace-by>", "</replace-by>",
                          "<feedback>", "</feedback>"]
    generator_tokenizer.add_tokens(new_special_tokens, special_tokens=True)
    generator.encoder.resize_token_embeddings(len(generator_tokenizer))
    config.vocab_size = len(generator_tokenizer)
    
    generator.load_state_dict(
        torch.load(
            os.path.join(PROJ_ROOT, GENERATOR_MODEL_PATH),
            weights_only=True
        )
    )
    
    generator.to(DEVICE)
    generator.eval()
    return generator, generator_tokenizer

def generator_inference(input_dataset, generator, generator_tokenizer):
    global DEVICE

    dataloader = DataLoader(input_dataset, batch_size=8, shuffle=False)
    all_edit_solutions = []

    beam_size = 1
    for batch in dataloader:
        batch = tuple(t.to(DEVICE) for t in batch)

        source_ids = batch[0]
        source_mask = source_ids.ne(generator_tokenizer.pad_token_id)
        with torch.no_grad():
            preds = generator.generate(source_ids,
                                    attention_mask=source_mask,
                                    use_cache=True,
                                    num_beams=beam_size,
                                    max_length=512,
                                    num_return_sequences=beam_size)
            preds = preds.reshape(source_ids.size(0), beam_size, -1)
            preds = preds.cpu().numpy()
            for idx in range(preds.shape[0]):
                item_edit_solutions = []
                for candidate in preds[idx]:
                    item_edit_solutions.append(generator_tokenizer.decode(candidate, skip_special_tokens=True,clean_up_tokenization_spaces=False))
                all_edit_solutions.append(item_edit_solutions)

    return all_edit_solutions
   
def formalize_single_generator_input(sliding_window: CodeWindow, prompt: str, lsp_service: str, prior_edits: list[CodeWindow], tokenizer) -> str:
    source_seq = f"<feedback>{lsp_service}</feedback>"
    source_seq += sliding_window.formalize_as_generator_target_window(beautify=False, label_num=6)
    # prepare the prompt region
    # truncate prompt if it encode to more than 64 tokens
    encoded_prompt = tokenizer.encode(prompt, add_special_tokens=False, max_length=64, truncation=True)
    truncated_prompt = tokenizer.decode(encoded_prompt)
    source_seq += f"<prompt>{truncated_prompt}</prompt><prior_edits>"
    common_seq_len = len(tokenizer.encode(source_seq, add_special_tokens=False))
    # prepare the prior edits region
    for prior_edit in prior_edits:
        prior_edit_seq = prior_edit.formalize_as_prior_edit(beautify=False, label_num=6)
        prior_edit_seq_len = len(tokenizer.encode(prior_edit_seq, add_special_tokens=False))
        # Allow the last prior edit to be truncated (Otherwise waste input spaces)
        source_seq += prior_edit_seq
        common_seq_len += prior_edit_seq_len
        if common_seq_len + prior_edit_seq_len > 512 - 3: # start of sequence token, end of sequence token and </prior_edits> token
            break
    source_seq += "</prior_edits>"
    
    return source_seq

def formalize_generator_dataset(sliding_windows: list[CodeWindow], prompt: str, lsp_services: list[str], prior_edits: list[CodeWindow], tokenizer):
    source_seqs = []
    for sliding_window, service in zip(sliding_windows, lsp_services):
        selected_prior_edits = select_prior_edits(sliding_window.before_edit_window(split_by_line=False), prior_edits, tokenizer)
        
        source_seq = formalize_single_generator_input(sliding_window, prompt, service, selected_prior_edits, tokenizer)
        source_seqs.append(source_seq)
        
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Generator input sequences are saved to debug/TRACE_generator_input_sequences.json")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_generator_input_sequences.json", "w", encoding="utf-8") as f:
            json.dump(source_seqs, f, indent=2)
            
    if source_seqs == []:
        return None
    
    encoded_source_seqs = tokenizer(source_seqs, padding="max_length", truncation=True, max_length=512)
    source_ids = torch.tensor(encoded_source_seqs["input_ids"], dtype=torch.long)
    dataset = TensorDataset(source_ids)
    
    return dataset

def edit_location_2_snapshots(label_predictions, repo_dir, prior_edit_hunks, edit_description, language, generator, generator_tokenizer):
    def group_and_sort_locations_by_file(edits):
        grouped = {}

        for item in edits:
            file_path = item["file_path"]
            if file_path not in grouped:
                grouped[file_path] = []
            grouped[file_path].append(item)

        # sort each file's items by the first line index
        for file_path, items in grouped.items():
            items.sort(key=lambda x: x["line_idxs"][0])

        return grouped
    
    
    locations = combine_consecutive_locations(label_predictions)
    if len(locations) == 0:
        return {}
    locations = group_and_sort_locations_by_file(locations)
    
    empty_snapshots = create_empty_snapshots(locations, repo_dir)
    
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Empty snapshots created for generator inference is saved to debug/TRACE_empty_snapshots.json")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_empty_snapshots.json", "w", encoding="utf-8") as f:
            json.dump(empty_snapshots, f, indent=2)
    
    # sliding window hunks are ranked by idx in empty snapshots
    # i-th sliding window hunk matches i-th service type        
    sliding_window_hunks, service_types = empty_snapshots_to_hunks(empty_snapshots)
    input_dataset = formalize_generator_dataset(sliding_window_hunks, edit_description, service_types, prior_edit_hunks, generator_tokenizer)
    
    # all_edit_solutions: list[list[str]], outer list is of length equal to number of sliding window hunks, inner list is of length beam size
    all_edit_solutions = generator_inference(input_dataset, generator, generator_tokenizer)
    
    # fill the edit solutions back to locations
    for file_path, snapshot in empty_snapshots.items():
        for window in snapshot:
            if isinstance(window, list):
                continue
            
            solutions = all_edit_solutions[window["idx"]]
            window["after"] = solutions[0].splitlines(keepends=True)
            
    # delete edit if after is identical to before, update the edit idx and rank for the rest of edits
    to_remove_edit_idxs = []
    confidences = []
    for file_path, snapshot in empty_snapshots.items():
        for widx, window in enumerate(snapshot):
            if isinstance(window, list):
                continue
            
            if window["before"] == window["after"]:
                to_remove_edit_idxs.append(window["idx"])
            else:
                confidences.append(window["confidence"])
                
    # rank confidence in descending order
    confidences = sorted(confidences, reverse=True)
    pred_snapshots = {}
    edit_idx = 0
    for file_path, snapshot in empty_snapshots.items():
        pred_snapshots[file_path] = []
        for window in snapshot:
            if isinstance(window, list):
                if len(pred_snapshots[file_path]) == 0 or not isinstance(pred_snapshots[file_path][-1], list):
                    pred_snapshots[file_path].append(window)
                else:
                    pred_snapshots[file_path][-1].extend(window)
                    
            else: # dict
                if window["idx"] in to_remove_edit_idxs:
                    assert isinstance(pred_snapshots[file_path][-1], list)
                    pred_snapshots[file_path][-1].extend(window["before"])
                else:
                    window["idx"] = edit_idx
                    edit_idx += 1
                    window["rank"] = confidences.index(window["confidence"])
                    pred_snapshots[file_path].append(window)
    
    return pred_snapshots

def create_empty_snapshots(locations, repo_dir):
    empty_snapshots = {}
    
    idx = 0
    for file_path, locs in locations.items():
        abs_file_path = os.path.join(repo_dir, file_path)
        
        with open(abs_file_path, 'r') as f:
            content = f.readlines()
            
        empty_snapshots[file_path] = []
        for loc_idx, loc in enumerate(locs):
            if loc_idx == 0:
                empty_snapshots[file_path].append(content[:loc["line_idxs"][0]])
            
            loc["idx"] = idx
            idx += 1
            if loc["type"] == "both":
                loc["before"] = content[loc["line_idxs"][0]:loc["line_idxs"][-1]+1]
                empty_snapshots[file_path].append(loc)
            else:
                loc["before"] = []
                empty_snapshots[file_path].append(loc)
                
            if loc_idx != len(locs) - 1: # if this location is not the last one
                next_loc = locs[loc_idx + 1]
                unchanged_range = content[loc["line_idxs"][-1]+1:next_loc["line_idxs"][0]]
                empty_snapshots[file_path].append(unchanged_range)
            else: # this is the last location
                # make sure there are still some lines after this location
                if content[loc["line_idxs"][-1]+1:]:
                    empty_snapshots[file_path].append(content[loc["line_idxs"][-1]+1:]) 
            
    return empty_snapshots

def empty_snapshots_to_hunks(empty_snapshots):
    hunks = []
    raw_code_windows = []
    
    to_edit_location_num = 0
    for file_path, snapshot in empty_snapshots.items():
        for widx, window in enumerate(snapshot):
            if isinstance(window, list):
                continue
            
            to_edit_location_num += 1
            prefix = snapshot[widx - 1] if widx - 1 >= 0 else []
            suffix = snapshot[widx + 1] if widx + 1 < len(snapshot) else []
            
            prefix = prefix[max(-3, -len(prefix)):]
            suffix = suffix[:min(3, len(suffix))]
                
            if window["type"] == "both":
                core_inline_labels = window["zipped_labels"][1::2]
                core_inter_labels = window["zipped_labels"][0::2]
                
                raw_code_windows.append({
                    "idx": window["idx"],
                    "code_window": prefix + window["before"] + suffix,
                    "inline_labels": ["<keep>"] * len(prefix) + core_inline_labels + ["<keep>"] * len(suffix),
                    "inter_labels": ["<null>"] * len(prefix) + core_inter_labels + ["<null>"] * len(suffix),
                    "service_type": window["lsp_service"]
                })
                
            else:
                core_inter_labels = window["zipped_labels"]
                raw_code_windows.append({
                    "idx": window["idx"],
                    "code_window": prefix + window["before"] + suffix,
                    "inline_labels": ["<keep>"] * len(prefix + suffix),
                    "inter_labels": ["<null>"] * len(prefix) + core_inter_labels + ["<null>"] * len(suffix),
                    "service_type": window["lsp_service"]
                })
                
    # rank raw code window by idx in ascending order
    raw_code_windows = sorted(raw_code_windows, key=lambda x: x["idx"])
    hunks = [CodeWindow(window, "sliding_window") for window in raw_code_windows]
    service_types = [window["service_type"] for window in raw_code_windows]
    
    assert len(hunks) == to_edit_location_num
    
    return hunks, service_types