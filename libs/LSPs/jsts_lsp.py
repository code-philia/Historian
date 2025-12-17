import os
import json
import time

from typing import Dict
from .language_server import LanguageServer

class TsLanguageServer(LanguageServer):
    def __init__(self, language_id: str, log: bool = False):
        server_command = ["typescript-language-server", "--stdio"]
        super().__init__(language_id, server_command, log)
    
    def _get_capabilities(self) -> Dict:
        """
        Override the default capabilities to support code diagnostics
        """
        return {
            "textDocument": {
                "references": {"dynamicRegistration": True},
                "codeAction": {
                    "codeActionLiteralSupport": {
                        "codeActionKind": {
                            "valueSet": ["", "quickfix", "refactor", "refactor.extract", "refactor.inline",
                                       "refactor.rewrite", "source", "source.organizeImports"]
                        }
                    }
                },
                "synchronization": {
                    "dynamicRegistration": True,
                    "didSave": True
                },
                "publishDiagnostics": {
                    "relatedInformation": True,
                    "versionSupport": True
                }
            },
            "diagnostics": {
                "dynamicRegistration": True
            }
        }

    def diagnostics(self, file_path, wait_time: float = 3):
        """
        Override the default diagnostics method, typescript-language-server send response for each opened file.
        """
        if self.workspace_file_version.get(file_path, 0) == 0:
            self.did_open(file_path)
        else:
            self.did_change(file_path)

        # TypeScript Language Server sends diagnostics for ALL opened files
        # We collect all of them, but filter for the specific file we care about
        expected_response_num = len(self.workspace_file_version)
        all_messages = self._get_messages(
            expect_method="textDocument/publishDiagnostics",
            message_num=expected_response_num,
            wait_time=wait_time,
            return_all=True  # Get all messages first
        )

        # Filter for the specific file we requested, matching URI and version
        expected_uri = f"file://{file_path}"
        client_version = self.workspace_file_version.get(file_path, None)

        filtered_messages = []
        for msg in all_messages:
            if msg.get("method") == "textDocument/publishDiagnostics":
                params = msg.get("params", {})
                msg_uri = params.get("uri", "")
                msg_version = params.get("version", None)

                # Match URI
                if msg_uri == expected_uri:
                    # Check version if both are available
                    if client_version is not None and msg_version is not None:
                        if msg_version == client_version:
                            filtered_messages.append(msg)
                        # else: skip this message, version mismatch
                    else:
                        # If version info is not available, just match by URI
                        filtered_messages.append(msg)

        return filtered_messages if filtered_messages else all_messages

    def _parse_rename_response(self, response, edits, old_name, new_name):
        """
        Parse the response of rename request and update the edits
        
        Args:
            response: the response of rename request
            edits: the locations identified by lsp
            old_name: the old name of the identifier, not used in ts lsp, preserved for compatibility
            new_name: the new name of the identifier, not used in ts lsp, preserved for compatibility
        """
        for file_path, changes in response[0]["result"]["changes"].items():
            file_path = file_path[7:]
            if file_path not in edits:
                edits[file_path] = []
            for edit in changes:
                edit["oldText"] = old_name
            edits[file_path].extend(changes)
        return edits
    
    def _filter_diagnostics(self, diagnostics, locations_to_ignore, init_diagnose_msg):
        """
        Filter the diagnostics by the last edit at range, more diagnostics please refer to: https://typescript.tv/errors/
        """
        with open("LSPs/typescript-language-server/typescript_diagnose_code.json", "r") as f:
            diagnose_codes = json.load(f)
            
        filtered_diagnostics = []
        for diagnostic in diagnostics:
            if not diagnostic["file_path"].endswith(".js") and \
            not diagnostic["file_path"].endswith(".ts") and \
            not diagnostic["file_path"].endswith(".tsx") and \
            not diagnostic["file_path"].endswith(".jsx"):
                continue
            if diagnostic["message"] in init_diagnose_msg:
                # If this diagnose already exists when the project is initialized, then this diagnose is not caused by user editing, no need to address
                continue
            if str(diagnostic["code"]) not in diagnose_codes:
                # well, we can't guarantee we have collected all errors ...
                continue
            if diagnose_codes[str(diagnostic["code"])]["whitelisted"]:
                should_ignore = False
                for location in locations_to_ignore:
                    if diagnostic["file_path"] == location["file_path"] and diagnostic["range"]["start"]["line"] in location["lines"]:
                        should_ignore = True
                        break
                if not should_ignore:
                    filtered_diagnostics.append(diagnostic)
        
        return filtered_diagnostics
    
if __name__ == "__main__":
    current_path = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.join(current_path, "projects/js_project")
    file_path = os.path.join(workspace, "src/app.js")
    
    server = TsLanguageServer("javascript", log=False)
    
    print(f">>>>>>>> Check initialize:")
    result = server.initialize(workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Get the list of all file paths in the workspace
    file_paths = server.get_all_file_paths(workspace)
    server.open_in_batch(file_paths)
    time.sleep(0.5)  # Wait for the server to process all opened files
    
    print(f">>>>>>>> Check rename:")
    result = server.rename(file_path, {"line": 7, "character": 20}, "UserName")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check references:")
    result = server.references(file_path, {"line": 8, "character": 8})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check diagnostics:")
    result = server.diagnostics(file_path, wait_time=2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check hover:")
    result = server.hover(file_path, {"line": 9, "character": 22})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check close:")
    result = server.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    """
    Test Js Language Server on ts_project
    """
    current_path = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.join(current_path, "projects/ts_project")
    file_path = os.path.join(workspace, "src/main.ts")
    
    server = TsLanguageServer("typescript", log=False)
    
    print(f">>>>>>>> Check initialize:")
    result = server.initialize(workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Get the list of all file paths in the workspace
    file_paths = server.get_all_file_paths(workspace)
    server.open_in_batch(file_paths)
    time.sleep(0.5)  # Wait for the server to process all opened files
    
    print(f">>>>>>>> Check rename:")
    result = server.rename(file_path, {"line": 12, "character": 17}, "ReversedString")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check references:")
    result = server.references(file_path, {"line": 12, "character": 24})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check diagnostics:")
    result = server.diagnostics(file_path, wait_time=2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check hover:")
    result = server.hover(file_path, {"line": 12, "character": 26})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check close:")
    result = server.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))