import os
import dotenv
import asyncio
import json

from openai import AsyncOpenAI
from . import navigator  # Import as module to access its globals
from .navigator import *  # Keep this for importing functions
from .utils import construct_agent_input
from agents import (Agent, ModelSettings, function_tool, Runner, 
                   set_default_openai_client, set_tracing_disabled,
                   set_default_openai_api)

dotenv.load_dotenv("../../.env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

custom_client = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
set_default_openai_client(custom_client)
set_tracing_disabled(True)
set_default_openai_api("chat_completions")

# Global constants
LSP = None
AGENT = None 
LOCATOR, LOCATOR_TOKENIZER = None, None
GENERATOR, GENERATOR_TOKENIZER = None, None

async def main(json_input: dict):
    if json_input["status"] == "init":
        return setup(json_input)
    elif json_input["status"] == "suggestion":
        return await subsequent_edit_recommendation(json_input)
    elif json_input["status"] == "end":
        return end(json_input)
    
def setup(json_input: dict):
    """
    Setup LSP server for agentic TRACE.
    """
    global LSP, AGENT
    global LOCATOR, LOCATOR_TOKENIZER
    global GENERATOR, GENERATOR_TOKENIZER
    
    print(f"[DEBUG:SUT] Setup json input: {json.dumps(json_input, indent=2)}")
    language = json_input["language"]
    repo_dir = json_input["repo_dir"]
    
    if language == "python":
        from libs.LSPs.py_lsp import PyLanguageServer
        LSP = PyLanguageServer()
    elif language == "java":
        from libs.LSPs.java_lsp import JavaLanguageServer
        LSP = JavaLanguageServer()
    elif language == "go":
        from libs.LSPs.go_lsp import GoLanguageServer
        LSP = GoLanguageServer()
    elif language in ["javascript", "typescript"]:
        from libs.LSPs.jsts_lsp import TsLanguageServer
        LSP = TsLanguageServer(language)
        
    # files_to_change = [os.path.join(repo_dir, file_path) for file_path in commit.changed_files]

    # Initialize LSP
    max_retries = 5
    retry_delay = 10 
    for attempt in range(max_retries):
        try:
            LSP.initialize(repo_dir)
            break  # If no exception, break the loop
        except Exception as e:
            print(f"[ERROR:SUT] Error initializing LSP at attempt {attempt+1}: {e}")
            if attempt < max_retries:
                print(f"[MESSAGE:SUT] Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("[ERROR:SUT] Failed to initialize LSP after multiple attempts.")
                raise  # If all attempts failed, raise the exception

    # LSP.open_in_batch(files_to_change)
    # # Obtain the initial diagnose messages that can be ignored
    # init_diagnose = LSP.process_diagnose(commit, models, args, return_diagnose=True)
    # args.init_diagnose_msg = LSP.extract_diagnose_msg(init_diagnose)
        
    AGENT = init_agent()
    

async def subsequent_edit_recommendation(json_input: dict):
    """
    Recommend subsequent edits based on prior edits.
    """
    # Declare global constants
    global LSP, AGENT
    global LOCATOR, LOCATOR_TOKENIZER
    global GENERATOR, GENERATOR_TOKENIZER

    # Declare global constrants for navigator module
    navigator.LSP = LSP
    navigator.LOCATOR = LOCATOR
    navigator.LOCATOR_TOKENIZER = LOCATOR_TOKENIZER
    navigator.GENERATOR = GENERATOR
    navigator.GENERATOR_TOKENIZER = GENERATOR_TOKENIZER
    
    # Update global variables in navigator module via information sent from simulation framework
    navigator.language = json_input["language"]
    navigator.repo_dir = json_input["repo_dir"]
    navigator.prior_edit_seqs = json_input["prior_edits"]
    navigator.current_location_of_prior_edits = json_input["current_location_of_prior_edits"]

    # rename(0, "get_active_streams", "get_linkable_streams")
    
    
    agent_input = construct_agent_input(json_input)
    print(f"[DEBUG:SUT] Constructed agent input: {agent_input}")
    # result = await Runner.run(AGENT, agent_input, max_turns=30)
    final_result = None

    async for event in Runner.run(
        AGENT,
        agent_input,
        max_turns=30,
        stream=True
    ):
        print("EVENT:", event)

        # 检测最终回答
        if event.get("type") == "assistant_message" and "tool_call" not in event:
            final_result = event["content"]

    # 使用最终结果
    print("FINAL RESULT:", final_result)
    
    # print("="*40)
    # print("NEW ITEMS:")
    # for i, r in enumerate(result.new_items):
    #     print(f"\n--- Item {i} ---")
    #     print(json.dumps(r.model_dump(), indent=2, default=str))

    # print("="*40)
    # print("RAW RESPONSES:")
    # for i, r in enumerate(result.raw_responses):
    #     print(f"\n--- Response {i} ---")
    #     print(json.dumps(r.model_dump(), indent=2, default=str))
    # print("="*40)
    