import os
import time
import json
import torch
import logging
import argparse

import torch.nn as nn
from datetime import datetime
from dotenv import load_dotenv
from rank_bm25 import BM25Okapi
from .code_window import CodeWindow
from .utils import select_prior_edits
from torch.utils.data import DataLoader, TensorDataset
from transformers import T5Config, T5ForConditionalGeneration, RobertaTokenizer

CURRENT_PATH = os.path.abspath(os.path.dirname(__file__))
PROJ_ROOT = os.path.abspath(os.path.join(CURRENT_PATH, "../../"))
load_dotenv(dotenv_path=os.path.join(PROJ_ROOT, ".env"))

LOCATOR_MODEL_PATH = os.getenv("LOCATOR_MODEL_PATH")
DEVICE = os.getenv("DEVICE")

logger = logging.getLogger("TRACE.Locator")

class TRACELocator(nn.Module):
    """
        Build Seqence-to-Sequence.
        
        Parameters:

        * `encoder`- encoder. e.g. roberta
        * `config`- configuration of encoder model. 
        * `mask_id`- the id of mask token. e.g. 50264
    """
    def __init__(self, encoder, config, 
                 inline_mask_id=None, inter_mask_id=None, 
                 keep_token_id=None, delete_token_id=None, replace_token_id=None, 
                 null_token_id=None, insert_token_id=None, block_split_token_id=None):
        super().__init__()
        self.encoder = encoder
        self.config=config
        self.model_type = "codet5"
        self.register_buffer("bias", torch.tril(torch.ones(2048, 2048)))
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        self.lsm = nn.LogSoftmax(dim=-1)
        self.tie_weights()
        
        self.inline_mask_id=inline_mask_id
        self.inter_mask_id=inter_mask_id
        self.keep_token_id=keep_token_id
        self.delete_token_id=delete_token_id
        self.replace_token_id=replace_token_id
        self.null_token_id=null_token_id
        self.insert_token_id=insert_token_id
        self.block_split_token_id=block_split_token_id
        self.label_weight = torch.ones(config.vocab_size) * 1e-3
        self.criterion = nn.CrossEntropyLoss(ignore_index=-1, weight=self.label_weight)
        
    def _tie_or_clone_weights(self, first_module, second_module):
        """ Tie or clone module weights depending of weither we are using TorchScript or not
        """
        if self.config.torchscript:
            first_module.weight = nn.Parameter(second_module.weight.clone())
        else:
            first_module.weight = second_module.weight
                  
    def tie_weights(self):
        """ Make sure we are sharing the input and output embeddings.
            Export to TorchScript can't handle parameter sharing so we are cloning them instead.
        """
        if self.model_type == "codet5":
            # T5 encoder has different embedding module
            self._tie_or_clone_weights(self.lm_head,
                                    self.encoder.embed_tokens)
        else:
            self._tie_or_clone_weights(self.lm_head,
                                   self.encoder.embeddings.word_embeddings)  
                                   
    def forward(self, source_ids=None, source_mask=None, target_ids=None, train=True):   
        outputs = self.encoder(source_ids, attention_mask=source_mask)
        encoder_output = outputs[0].permute([1,0,2]).contiguous()
        hidden_states = torch.tanh(self.dense(encoder_output)).permute([1,0,2]).contiguous()
        lm_logits = self.lm_head(hidden_states).contiguous()
        if train:
            # Flatten the tokens
            active_loss = ((source_ids == self.inter_mask_id) | (source_ids == self.inline_mask_id)).contiguous().view(-1) # find which tokens are masked
            labels = target_ids.contiguous().view(-1)[active_loss] # get the labels of the masked tokens
            filtered_logits = lm_logits.contiguous().view(-1, self.config.vocab_size)[active_loss] # get the logits of the masked tokens

            loss = self.criterion(filtered_logits, labels)
            outputs = loss,loss*active_loss.sum(),active_loss.sum()
            return outputs
        else:
            return lm_logits
      
def load_locator():
    global PROJ_ROOT, LOCATOR_MODEL_PATH, DEVICE
    config_class, model_class, tokenizer_class = T5Config, T5ForConditionalGeneration, RobertaTokenizer
    locator_config = config_class.from_pretrained('salesforce/codet5-large')
    locator_tokenizer = tokenizer_class.from_pretrained('salesforce/codet5-large')
    encoder = model_class.from_pretrained('salesforce/codet5-large').encoder

    # add special tokens
    new_special_tokens = ["<inter-mask>",
                        "<code_window>", "</code_window>", 
                        "<prompt>", "</prompt>", 
                        "<prior_edits>", "</prior_edits>",
                        "<edit>", "</edit>",
                        "<keep>", "<replace>", "<delete>",
                        "<null>", "<insert>", "<block-split>",
                        "</insert>","<replace-by>", "</replace-by>"]
    locator_tokenizer.add_tokens(new_special_tokens, special_tokens=True)
    encoder.resize_token_embeddings(len(locator_tokenizer))
    locator_config.vocab_size = len(locator_tokenizer)
    
    locator=TRACELocator(encoder=encoder,config=locator_config,
                    inline_mask_id=locator_tokenizer.mask_token_id,
                    inter_mask_id=locator_tokenizer.convert_tokens_to_ids("<inter-mask>"),
                    keep_token_id=locator_tokenizer.convert_tokens_to_ids("<keep>"),
                    delete_token_id=locator_tokenizer.convert_tokens_to_ids("<delete>"),
                    replace_token_id=locator_tokenizer.convert_tokens_to_ids("<replace>"),
                    null_token_id=locator_tokenizer.convert_tokens_to_ids("<null>"),
                    insert_token_id=locator_tokenizer.convert_tokens_to_ids("<insert>"),
                    block_split_token_id=locator_tokenizer.convert_tokens_to_ids("<block-split>"))
    
    locator.load_state_dict(
        torch.load(
            os.path.join(PROJ_ROOT, LOCATOR_MODEL_PATH), 
            map_location = DEVICE,
            weights_only = True
        ), 
        strict = False
    )
    locator.eval()
    locator.to(DEVICE)
    
    return locator,locator_tokenizer
    
def make_locator_dataset(sliding_windows, prior_edit_hunks, edit_description, locator_tokenizer):
    """
    This function help each sliding window to find syntactically similar prior edits, then form the input sequence for locator model.

    Args:
        sliding_windows (list[list[str]]): the sliding windows of code
        prior_edit_hunks (list[CodeWindow]): the prior edit hunks
        edit_description (str): the commit message or edit description
        locator_tokenizer (RobertaTokenizer): the tokenizer for locator model
    """
    source_seqs = []
    for sliding_window in sliding_windows:
        selected_prior_edit_hunks = select_prior_edits("".join(sliding_window), prior_edit_hunks, locator_tokenizer)
            
        source_seq = formalize_locator_input(sliding_window, edit_description, selected_prior_edit_hunks, locator_tokenizer)
        source_seqs.append(source_seq)
        
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Locator input sequences are saved to debug/TRACE_locator_input_sequences.json")
        os.makedirs("debug", exist_ok=True)
        with open("debug/TRACE_locator_input_sequences.json", "w", encoding="utf-8") as f:
            json.dump(source_seqs, f, indent=2)
        
    encoded_source_seq = locator_tokenizer(source_seqs, padding="max_length", truncation=True, max_length=512)
    
    source_ids = torch.tensor(encoded_source_seq["input_ids"])
    source_mask = torch.tensor(encoded_source_seq["attention_mask"])
    dataset = TensorDataset(source_ids, source_mask)

    return dataset

def formalize_locator_input(sliding_window: list[str], prompt: str, prior_edits: list[CodeWindow], 
                                    tokenizer: RobertaTokenizer) -> str:
    """
    Func:
        Given a sliding window, prior edits, and prompt, form the input sequence for locator

    Args:
        sliding_window: list[str], one sliding window of code
        prompt: str, the commit message
        prior_edits: list[CodeWindow], the prior edit hunks selected
        tokenizer: RobertaTokenizer, the tokenizer of locator model

    Returns:
        source_seq: str, the input sequence for locator
    """
    source_seq = "<code_window><inter-mask>"
    for line_of_code in sliding_window:
        source_seq += f"<mask>{line_of_code}<inter-mask>"

    source_seq += f"<prompt>{prompt}</prompt><prior_edits>"
    source_seq_len = len(tokenizer.encode(source_seq, add_special_tokens=False))
    
    # prepare the prior edits region
    for prior_edit in prior_edits:
        prior_edit_seq = prior_edit.formalize_as_prior_edit(beautify=False, label_num=6)
        prior_edit_seq_len = len(tokenizer.encode(prior_edit_seq, add_special_tokens=False))
        # Allow the last prior edit to be truncated (Otherwise waste input spaces)
        source_seq += prior_edit_seq
        source_seq_len += prior_edit_seq_len
        if source_seq_len + prior_edit_seq_len > 512 - 3: # start of sequence token, end of sequence token and </prior_edits> token
            break
    source_seq += "</prior_edits>"
    
    return source_seq

def locator_predict(locator, locator_tokenizer, dataloader, flatten=True):
    """
    Predict the edit operations for each line of code
    
    Args:
        locator: the locator model
        locator_tokenizer: the tokenizer of locator model
        dataloader: the dataloader of locator model

    Returns:
        all_preds: list[list[str]], the predicted edit operations for each line of code
        all_confidences: list[list[float]], the confidence scores for each predicted edit operation
    """
    global DEVICE
    
    all_preds = []
    all_confidences = []
    
    
    insert_threshold = 0.90
    replace_threshold = 0.5
    delete_threshold = 0.97
    block_split_threshold = 0.97
    
    for batch in dataloader:
        batch = tuple(t.to(DEVICE) for t in batch)
        source_ids,source_mask = batch
        with torch.no_grad():
            torch.cuda.synchronize()
            lm_logits = locator(source_ids=source_ids,source_mask=source_mask, train=False)
            lm_logits = torch.nn.functional.softmax(lm_logits, dim=-1)
            torch.cuda.synchronize()
            
        # extract masked edit operations
        for i in range(lm_logits.shape[0]): # for sample within batch
                batch_preds = []
                batch_confidences = []
                for j in range(lm_logits.shape[1]): # for every token
                    if source_ids[i][j] == locator.inline_mask_id or source_ids[i][j] == locator.inter_mask_id: # if is masked
                        pred_label = locator_tokenizer.decode(torch.argmax(lm_logits[i][j]),clean_up_tokenization_spaces=False)
                        if not pred_label.startswith("<") or not pred_label.endswith(">"):
                            pred_label = f"<{pred_label}>"
                        confidence = torch.max(lm_logits[i][j]).item() # Get the confidence value (0-1)
                        if pred_label == "<insert>" and confidence < insert_threshold: # debug
                            pred_label = "<null>"
                            confidence = lm_logits[i][j][locator_tokenizer.convert_tokens_to_ids("<null>")].item()
                        elif pred_label == "<replace>" and confidence < replace_threshold: # debug
                            pred_label = "<keep>"
                            confidence = lm_logits[i][j][locator_tokenizer.convert_tokens_to_ids("<keep>")].item()
                        elif pred_label == "<delete>" and confidence < delete_threshold: # debug
                            pred_label = "<keep>"
                            confidence = lm_logits[i][j][locator_tokenizer.convert_tokens_to_ids("<keep>")].item()
                        elif pred_label == "<block-split>" and confidence < block_split_threshold: #debug
                            pred_label = "<null>"
                            confidence = lm_logits[i][j][locator_tokenizer.convert_tokens_to_ids("<null>")].item()
                        batch_preds.append(pred_label)
                        batch_confidences.append(confidence)
                all_preds.append(batch_preds)
                all_confidences.append(batch_confidences)
    
    # all_preds and all_confidences are list[list[str]] and list[list[float]]
    if flatten:
        # We need to: 
        # 1. Flatten to list[str] and list[float]
        # 2. Resolve the conflict between the first & last inter-line label between 2 adjacent code windows
        all_inter_predictions = []
        all_inline_predictions = []
        all_inter_confidences = []
        all_inline_confidences = []
        for preds, confidence in zip(all_preds, all_confidences):
            inter_preds = [preds[i] for i in range(0, len(preds), 2)]
            inline_preds = [preds[i] for i in range(1, len(preds), 2)]
            
            inter_conf = [confidence[i] for i in range(0, len(confidence), 2)]
            inline_conf = [confidence[i] for i in range(1, len(confidence), 2)]
            
            all_inline_predictions.extend(inline_preds)
            all_inline_confidences.extend(inline_conf)
            
            if len(all_inter_predictions) != 0:
                # compare the last in all_inter_labels and the first in inter_preds
                if all_inter_confidences[-1] <= inter_conf[0]:
                    # pop the last of all_inter_labels and extend the new
                    all_inter_predictions.pop()
                    all_inter_confidences.pop()
                    all_inter_predictions.extend(inter_preds)
                    all_inter_confidences.extend(inter_conf)
                else:
                    # pop the first of inter_preds and extend the new
                    inter_preds.pop(0)
                    inter_conf.pop(0)
                    all_inter_predictions.extend(inter_preds)
                    all_inter_confidences.extend(inter_conf)
            else:
                all_inter_predictions.extend(inter_preds)
                all_inter_confidences.extend(inter_conf)
        assert len(all_inter_predictions) == len(all_inter_confidences)
        assert len(all_inline_predictions) == len(all_inline_confidences)
        assert len(all_inter_predictions) - 1 == len(all_inline_predictions)

        return {
            "inline_predictions": all_inline_predictions,
            "inline_confidences": all_inline_confidences,
            "inter_predictions": all_inter_predictions,
            "inter_confidences": all_inter_confidences,
            "inline_service": ["normal"] * len(all_inline_predictions),
            "inter_service": ["normal"] * (len(all_inter_predictions) + 1)
        }
    else:
        return all_preds, all_confidences
    
    return all_preds, all_confidences

def split_file_into_windows(content, tokenizer):
    """
    Split the file into windows.
    """
    sliding_windows = []
    start_line_idx = 0
    window_length = 10 # default window length is 10
    while True:
        if window_length == 0:
            # code at current start line is too long to fit into one window
            code = content[start_line_idx]
            # 1 token ~= 4 char, expect to have around 256 tokens
            truncated_code = code[:1024]
            sliding_windows.append([truncated_code])
            start_line_idx += 1
            window_length = 10
        window_length = min(window_length, len(content) - start_line_idx)
        if window_length <= 0:
            break
        current_window = content[start_line_idx:start_line_idx+window_length]
        # count token num
        current_window_str = "".join(current_window)
        current_token_num = len(tokenizer.tokenize(current_window_str))
        """
        Input must have enough space to store the special tokens for code window, including:
        2: <code_window>, </code_window>
        l: <inline-mask> for l lines of code
        l+1: <inter-mask> for l + 1 spaces between lines of code
        """
        redundancy = 2 + 2 * len(current_window) + 1
        if redundancy + current_token_num >= 512:
            # current window is too long, reduce the length and try again
            window_length -= 1 
            continue
        else:
            sliding_windows.append(current_window.copy())
            start_line_idx += window_length
            window_length = 10 # reset to default length
            
    return sliding_windows

def combine_consecutive_locations(location_predictions):
    """
    Combine the consecutive predicted locations into a single location
    
    Args:
        location_predictions: dict, each key is a file, each value is another dict, keys including: "inline_predictions","inline_confidences", "inter_predictions", "inter_confidences"
    
    Returns:
        combined_locations: list, each element is a dict
    """
    lsp_service_rank = { # the recommendation order
        'rename': 4,
        'def&use': 3,
        'clone': 2,
        'diagnose': 1,
        'normal': 0
    }
    combined_locations = []
    
    for file_path, file_predictions in location_predictions.items():
        inter_predictions = file_predictions["inter_predictions"]
        inter_confidences = file_predictions["inter_confidences"]
        inline_predictions = file_predictions["inline_predictions"]
        inline_confidences = file_predictions["inline_confidences"]
        inline_service = file_predictions["inline_service"]
        inter_service = file_predictions["inter_service"]
        
        # Assert the element in inter_predictions and inline_predictions are allowed
        assert set(inter_predictions).issubset({"<null>", "<block-split>", "<insert>"})
        assert set(inline_predictions).issubset({"<keep>", "<delete>", "<replace>"})
        
        # Zip inter-line predictions with inline predictions into a zipped single list of prediction
        zipped_predictions = [inter_predictions[0]]
        for inter, inline in zip(inter_predictions[1:], inline_predictions):
            zipped_predictions.append(inline)
            zipped_predictions.append(inter)
        zipped_pred_actionable_idxs = [i for i, action in enumerate(zipped_predictions) if action != "<keep>" and action != "<null>"]
        
        zipped_confidences = [inter_confidences[0]]
        for inter, inline in zip(inter_confidences[1:], inline_confidences):
            zipped_confidences.append(inline)
            zipped_confidences.append(inter)

        # Set confidence of un-actionable locations to 0, as we only rank actionable locations
        zipped_confidences = [confidence if i in zipped_pred_actionable_idxs else 0 for i, confidence in enumerate(zipped_confidences)]
        
        zipped_services = [inter_service[0]]
        for inter, inline in zip(inter_service[1:], inline_service):
            zipped_services.append(inline)
            zipped_services.append(inter)
        
        # TYPE 1: Extract inline consecutive edit locations
        # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
        inline_edit_location_groups = []
        location_group = []
        for line_idx, label in enumerate(inline_predictions):
            if label == "<keep>" and location_group != []:
                inline_edit_location_groups.append(location_group)
                location_group = []
            elif label != "<keep>":
                location_group.append(line_idx)
        if location_group != []:
            inline_edit_location_groups.append(location_group)
        
        # Add more information to the grouped locations
        for group in inline_edit_location_groups:
            start_line_idx = group[0]
            end_line_idx = group[-1]
            
            zipped_start_idx = start_line_idx * 2
            zipped_end_idx = end_line_idx * 2 + 2
            
            group_labels = zipped_predictions[zipped_start_idx:zipped_end_idx+1]

            # Calculate the average confidence of the group (ignore 0 confidence, those are un-actionable locations)
            group_confidences = zipped_confidences[zipped_start_idx:zipped_end_idx+1]
            avg_confidence = sum(group_confidences) / len([x for x in group_confidences if x != 0])

            # Find out which lsp service is used to locate this group
            group_services = zipped_services[zipped_start_idx:zipped_end_idx+1]
            lsp_service = list(set(group_services).difference({"normal"}))
            if len(lsp_service) == 0:
                lsp_service = "normal"
            elif len(lsp_service) == 1:
                lsp_service = lsp_service[0]
            else:
                lsp_service = " + ".join(lsp_service)
                # raise Exception(f"More than 1 lsp service is used to locate this group: {lsp_service}")
            
            combined_locations.append({
                "file_path": file_path,
                "line_idxs": group,
                "zipped_labels": group_labels,
                "confidence": 1 if lsp_service != "normal" else avg_confidence,
                "type": "both",
                "lsp_service": lsp_service
            })
            
            
        grouped_idx = [item for sublist in inline_edit_location_groups for item in sublist] # flatten the list, a list with lines labelled to edit
        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        
        # TYPE 2: Extract inter-line edit locations (inter-line edits either a part of inline consecutive group or a standalone inter-line edit)
        # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
        for inter_idx, inter_label in enumerate(inter_predictions):
            if inter_label == "<insert>":
                before_inline_idx = None if inter_idx == 0 else inter_idx - 1
                after_inline_idx = None if inter_idx == len(inter_predictions) - 1 else inter_idx
                if (before_inline_idx is not None and before_inline_idx in grouped_idx) or \
                (after_inline_idx is not None and after_inline_idx in grouped_idx):
                    # in this case we dont need to add this insert into the grouped locations, as it is already included in the inline consecutive group
                    continue
                else:
                    # This is a standalone inter-line edit
                    combined_locations.append({
                        "file_path": file_path,
                        "line_idxs": [inter_idx],
                        "zipped_labels": [inter_label],
                        "confidence": inter_confidences[inter_idx],
                        "type": "inter",
                        "lsp_service": inter_service[inter_idx]
                    })

        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    
    # rank predicted consectuive locations by their confidence
    combined_locations = sorted(
        combined_locations,
        key=lambda x:(
            x["confidence"],
            lsp_service_rank.get(x["lsp_service"], 0)
        ),
        reverse=True
    )
    prev_conf = 1
    rank = 1
    for idx, location in enumerate(combined_locations):
        if location["confidence"] == prev_conf:
            location["rank"] = rank
        elif location["confidence"] < prev_conf:
            if idx != 0:
                rank += 1
            location["rank"] = rank
            prev_conf = location["confidence"]
        else:
            raise ValueError("Confidence values are not in expected order.")
        
    return combined_locations
    
# def convert_locations_to_code_windows(consecutive_edit_locations: dict, label_predictions: dict, repo_dir: str, logger):
#     generator_samples = {}
#     for file_path, location_lists in consecutive_edit_locations.items():
#         label_prediction = label_predictions[file_path]
#         generator_samples[file_path] = []
        
#         for location_idx, location in enumerate(location_lists):
            
        