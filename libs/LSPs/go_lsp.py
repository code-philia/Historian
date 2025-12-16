import os
import json
import subprocess
from .language_server import LanguageServer

class GoLanguageServer(LanguageServer):
    def __init__(self, log: bool = False, logger=None):
        language_id = "go"
        server_command = ["gopls", "serve"]
        env = os.environ.copy()
        env["GO111MODULE"] = "off"  # Turn off Modules mode
        env["GOPATH"] = env.get("GOPATH", os.path.expanduser("~/go"))  # Stick to default GOPATH
        env["GOFLAGS"] = "-mod=mod"  # let gopls analyze local files

        super().__init__(language_id, server_command, log, logger=logger)
        
    def _parse_rename_response(self, response, edits, old_name, new_name):
        """
        Parse the response of rename request and update the edits
        
        Args:
            response: the response of rename request
            edits: the locations identified by lsp
            old_name: the old name of the identifier, not used in go lsp, preserved for compatibility
            new_name: the new name of the identifier, not used in go lsp, preserved for compatibility
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
        Filter out non-serious diagnostics, all diagnostics please refer to https://pkg.go.dev/golang.org/x/tools/internal/typesinternal#ErrorCode
        """
        while_list_diagnostics = [
            "UnusedImport",
            "UnusedExpr",
            "UnusedVar",
            "UnusedLabel",
            "UnusedResults",
            "WrongTypeArgCount",
            "WrongArgCount",
            "DuplicateLabel",
            "WrongResultCount",
            "UndeclaredName",
            "UndeclaredImportedName",
            "MismatchedTypes",
            "TooManyValues",
            "DuplicateMethod",
            "DuplicateFieldAndMethod",
            "NoNewVar"
        ]
        filtered_diagnostics = []
        for diagnostic in diagnostics:
            if not diagnostic["file_path"].endswith(".go"):
                continue
            if diagnostic["message"] in init_diagnose_msg:
                # If this diagnose already exists when the project is initialized, then this diagnose is not caused by user editing, no need to address
                continue
            if diagnostic["code"] in while_list_diagnostics:
                if last_edit_region and diagnostic["range"]["start"]["line"] in last_edit_region["lines"] and diagnostic["file_path"] == last_edit_region["file_path"]:
                    continue
                filtered_diagnostics.append(diagnostic)
        return filtered_diagnostics
    
    def close(self):
        # clean cache data
        try:
            subprocess.run(
                ["go", "clean", "-modcache"],
                check=True, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            print(f"Error cleaning Go module cache: {e}")
            print(e.stderr.decode())
            
        return super().close()


if __name__ == "__main__":
    import os
    server = GoLanguageServer(log=False)
    current_path = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.join(current_path, "projects/go_project")
    file_path = os.path.join(workspace, "main.go")
    
    print(f">>>>>>>> Check initialize:")
    response = server.initialize([workspace])
    print(json.dumps(response, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check rename:")
    response = server.rename(file_path, {"line": 10, "character": 2}, "sum_func")
    print(json.dumps(response, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check references:")
    response = server.references(file_path, {"line": 10, "character": 15})
    print(json.dumps(response, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check diagnostics:")
    response = server.diagnostics(file_path)
    print(json.dumps(response, indent=2, ensure_ascii=False))
    
    print(f">>>>>>>> Check close:")
    response = server.close()
    print(json.dumps(response, indent=2, ensure_ascii=False))
