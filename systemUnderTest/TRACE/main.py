from .Invoker import *

# Global constants
LSP = None
INVOKER, INVOKER_TOKENIZER = None, None
LOCATOR, LOCATOR_TOKENIZER = None, None
GENERATOR, GENERATOR_TOKENIZER = None, None

def main(json_input: dict):
    if json_input["status"] == "init":
        return setup(json_input)
    elif json_input["status"] == "suggestion":
        return subsequent_edit_recommendation(json_input)
    elif json_input["status"] == "end":
        return end(json_input)
    
def setup(json_input: dict):
    """
    Setup LSP server and models for TRACE.
    """
    global LSP
    global INVOKER, INVOKER_TOKENIZER
    global LOCATOR, LOCATOR_TOKENIZER
    global GENERATOR, GENERATOR_TOKENIZER
    
    language = json_input["language"]
    
    print(f"[DEBUG:SUT] Setup json input: {json.dumps(json_input, indent=2)}")
    language = json_input["language"]
    repo_dir = json_input["repo_dir"]
    
    # Setup LSP server
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
    
    
    # Setup neural models
    invoker, invoker_tokenizer = load_invoker(args, logger)
    
def subsequent_edit_recommendation(json_input: dict):
    pass

def end(json_input: dict):
    pass