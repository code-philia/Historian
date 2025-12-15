import os
import json
import time
from .language_server import LanguageServer

class PyLanguageServer(LanguageServer):
    def __init__(self, log: bool = False, logger=None):
        language_id = "python"
        server_command = ['pyright-langserver', '--stdio']
        
        super().__init__(language_id, server_command, log, logger=logger)
        
    def _parse_rename_response(self, response, edits, old_name, new_name):
        """
        Parse the response of rename request and update the edits
        
        Args:
            response: the response of rename request
            edits: the locations identified by lsp
            old_name: the old name of the identifier, not used in py lsp, preserved for compatibility
            new_name: the new name of the identifier, not used in py lsp, preserved for compatibility
        """
        for changes in response[0]["result"]["documentChanges"]:
            file_path = changes["textDocument"]["uri"][7:]
            if file_path not in edits:
                edits[file_path] = []
            for edit in changes["edits"]:
                edit["oldText"] = old_name
            edits[file_path].extend(changes["edits"])
        return edits
    
    def _filter_diagnostics(self, diagnostics, last_edit_region, init_diagnose_msg):
        """
        Filter out non-serious diagnostics, all diagnostics please refer to https://github.com/microsoft/pyright/blob/main/docs/configuration.md
        """
        white_list_diagnostics = [
            "reportUnusedImport",
            "reportUnusedClass",
            "reportUnusedFunction",
            "reportUnusedVariable",
            "reportDuplicateImport",
            "reportRedeclaration",
            "reportUndefinedVariable"
        ]
        filtered_diagnostics = []
        if diagnostics == []:
            return filtered_diagnostics
        
        for diagnostic in diagnostics:
            if not diagnostic["file_path"].endswith(".py"):
                continue
            if diagnostic["message"] in init_diagnose_msg:
                # If this diagnose already exists when the project is initialized, then this diagnose is not caused by user editing, no need to address
                continue
            if "code" not in diagnostic:
                continue
            if diagnostic["code"] in white_list_diagnostics:
                if last_edit_region and diagnostic["file_path"] == last_edit_region["file_path"] and diagnostic["range"]["start"]["line"] in last_edit_region["lines"]:
                    continue
                filtered_diagnostics.append(diagnostic)
        return filtered_diagnostics
    
    def close(self):
        # delete the temp data
        time.sleep(0.2)
        return super().close()
    
if __name__ == "__main__":
    current_path = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.join(current_path, "projects/python_project")
    file_path = os.path.join(workspace, "src/main.py")
    
    server = PyLanguageServer(log=False)
    
    print(f">>>>>>>> Check initialize:")
    result = server.initialize(workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check rename:")
    result = server.rename(file_path, {"line": 7, "character": 27}, "add")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check references:")
    result = server.references(file_path, {"line": 4, "character": 12})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check diagnostics:")
    result = server.diagnostics(file_path, wait_time=2)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check close:")
    result = server.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))