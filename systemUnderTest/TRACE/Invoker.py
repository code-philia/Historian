import torch
import logging
import argparse
import numpy as np

import torch.nn as nn
from commit import Commit
from logic_gate import logic_gate
from enriched_semantic import finer_grain_window
from transformers import RobertaConfig, RobertaModel, RobertaTokenizer

class Invoker(nn.Module):
    """
        Parameters:

        * `encoder`- encoder. e.g. roberta
        * `config`- configuration of encoder model
    """
    def __init__(self, encoder, config):
        super().__init__()
        self.encoder = encoder
        self.config=config
        self.register_buffer("bias", torch.tril(torch.ones(2048, 2048)))
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.lm_head = nn.Linear(config.hidden_size, 4, bias=True)
        
        self.criterion = nn.BCEWithLogitsLoss()
                                   
    def forward(self, source_ids=None, source_mask=None, labels=None, train=True):   
        outputs = self.encoder(source_ids, attention_mask=source_mask)
        encoder_output = outputs[0].permute([1,0,2]).contiguous()
        hidden_states = torch.tanh(self.dense(encoder_output)).permute([1,0,2]).contiguous()
        lm_logits = self.lm_head(hidden_states).contiguous()
        cls_logits = lm_logits[:,0,:]
        if train:
            loss = self.criterion(cls_logits, labels)
            return loss
        else:
            return cls_logits
       
def load_invoker(args, logger):
    MODEL_CLASSES = {'roberta': (RobertaConfig, RobertaModel, RobertaTokenizer)}
    
    config_class, model_class, tokenizer_class = MODEL_CLASSES["roberta"]
    config = config_class.from_pretrained("microsoft/codebert-base")
    tokenizer = tokenizer_class.from_pretrained("microsoft/codebert-base")
    
    # build expert model
    encoder = model_class.from_pretrained("microsoft/codebert-base")
    # add special tokens
    new_special_tokens = ["<last_edit>", "</last_edit>",
                          "<before>", "</before>",
                          "<after>", "</after>",
                          "<previous_edit>", 
                          "</previous_edit>",
                          "<variable_rename>", "<function_rename>", 
                          "<def&ref>", "<clone>"]
    tokenizer.add_tokens(new_special_tokens, special_tokens=True)
    encoder.resize_token_embeddings(len(tokenizer))
    config.vocab_size = len(tokenizer)
    invoker = Invoker(encoder, config)
    
    invoker.load_state_dict(torch.load(args.invoker_model_path))
    invoker.to(args.device)
    logger.info(f"Successfully loaded Invoker model from: {args.invoker_model_path}")
    return invoker, tokenizer

def ask_invoker(commit: Commit, models: dict, args: argparse.Namespace, logger: logging.Logger):
    """
    Func:
        Given a list of prior edit hunks, ask the expert to decide which LSP service to use
        
    Args:
        commit: the commit object
        models: dict of loaded models
        logger: the logger object
    
    Return:
        service: the service name
        service_info: the additional information to invoke LSP service
    """
    prior_edit_type, service_info = logic_gate(commit.prev_edits, commit.language)
    if prior_edit_type == "normal":
        service = "normal"
        logger.info("Last prior edit composition type prediction:")
        logger.info(f"Heuristic   logic : {service}")
        return service, service_info
    elif args.system == "TRACE-wo-Invoker":
        service = "all"
        logger.info("Last prior edit composition type prediction:")
        logger.info(f"Blindly  invoking : {service}")
        return service, service_info
    
    prior_edits = commit.prev_edits
    prior_edit_hunk_set = prior_edits[-min(3, len(prior_edits)):] 
    prior_edit_hunk_set.reverse()
    
    code_blocks = finer_grain_window(prior_edit_hunk_set[0]['before'], prior_edit_hunk_set[0]['after'], commit.language)
    input_seqs = []
    
    common_seq = ""
    for previous_edit in prior_edit_hunk_set[1:]:
        common_seq += "<previous_edit>"
        common_seq += f"<before>{''.join(previous_edit['before'])}</before>"
        common_seq += f"<after>{''.join(previous_edit['after'])}</after>"
        common_seq += "</previous_edit>"
    if prior_edit_type != "clone":
        for block in code_blocks:
            if block["before"] == [] or block["after"] == []:
                continue
            input_seq = f"<{prior_edit_type}><latest_edit>"
            input_seq += f"<before>{''.join(block['before'])}</before>"
            input_seq += f"<after>{''.join(block['after'])}</after>"
            input_seq += "</latest_edit>"
            input_seq += common_seq
            input_seqs.append(input_seq)
    else:
        input_seq = f"<{prior_edit_type}><latest_edit>"
        input_seq += f"<before>{''.join(prior_edit_hunk_set[0]['before'])}</before>"
        input_seq += f"<after>{''.join(prior_edit_hunk_set[0]['after'])}</after>"
        input_seq += "</latest_edit>"
        input_seq += common_seq
        input_seqs.append(input_seq)
    
    if input_seqs == []:
        logger.info("Last prior edit composition type prediction:")
        logger.info(f"Empty prior edits : normal")
        return "normal", None
    
    invoker, invoker_tokenizer = models["invoker"], models["invoker_tokenizer"]
    input = invoker_tokenizer(input_seqs, padding="max_length", truncation=True, max_length=512)
    source_ids = torch.tensor(input["input_ids"]).to(args.device)
    source_masks = torch.tensor(input["attention_mask"]).to(args.device)

    threshold = np.array([0.5, 0.5, 0.5, 0.5])
    with torch.no_grad():
        logits = invoker(source_ids=source_ids,source_mask=source_masks,labels=None, train=False)
        probability = torch.sigmoid(logits).detach().cpu().numpy()
        logger.info(f"Probability: {probability}")
        binary_predictions = (probability >= threshold).astype(int)
    
    for prediction in binary_predictions:
        if prediction[0] == 1: 
            service = "rename"
            service_confidence = max(probability[:,0])
            break
        elif prediction[1] == 1:
            service = "rename"
            service_confidence = max(probability[:,1])
            break
        elif prediction[2] == 1:
            service = "def&ref"
            service_confidence = max(probability[:,2])
            break
        elif prediction[3] == 1:
            service = "clone"
            service_confidence = max(probability[:,3])
            break
        else:
            service = "normal"
            service_confidence = None

    logger.info("Last prior edit composition type prediction:")
    logger.info(f"Heuristic   logic : {prior_edit_type}")
    logger.info(f"TRACE   Invoker : {service}")
    if service_confidence != None:
        logger.info(f"Invoker confidence: {service_confidence:.4f}")
    return service, service_info
    