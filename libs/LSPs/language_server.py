import os
import re
import json
import time
import threading
import functools
import subprocess
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

def timeout_decorator(timeout, timeout_return=None):
    """
    Decorator to add a timeout to any function.
    Returns `timeout_return` when the function exceeds the timeout.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result_container = {}
            exception_container = {}

            def target():
                try:
                    result_container['result'] = func(*args, **kwargs)
                except Exception as e:
                    exception_container['exception'] = e

            thread = threading.Thread(target=target)
            thread.daemon = True  # Set the thread as a daemon thread
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                raise TimeoutError(f"Function '{func.__name__}' exceeded timeout of {timeout} seconds.")

            if 'exception' in exception_container:
                raise exception_container['exception']

            return result_container.get('result')
        return wrapper
    return decorator

class LanguageServer(ABC):
    def __init__(self, language_id: str, server_command: List[str], log: bool = False, logger = None):
        """
        Initialize the language server process.
        """
        self.language_id = language_id
        self.request_id: int = 1
        self.log: bool = log
        self.logger = logger
        self.workspace_file_version: Dict[str, int] = {}
        self.workspace_dir: Optional[str] = None
        try:
            self.process = subprocess.Popen(
                server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
        except Exception as e:
            logger.error(f"[LSP] Failed to start language server process: {e}")
            raise Exception

    def initialize(self, workspace_dir: str, wait_time: float = 0.5):
        self.workspace_dir = workspace_dir
        request_id = self._send_request(
            "initialize",
            params={
                "processId": None,
                "workspaceFolders": [
                    {
                        "uri": f"file://{self.workspace_dir}",
                        "name": "Workspace 0"
                    }
                ],
                "capabilities": self._get_capabilities()
            }
        )
        messages = self._get_messages(request_id=request_id, message_num=1, wait_time=wait_time)
        self._send_notification("initialized")
        return messages

    def __str__(self) -> str:
        """
        Return a string representation showing the language server and workspace.
        """
        workspace_info = "not initialized" if self.workspace_dir is None else self.workspace_dir

        return f"LanguageServer(language={self.language_id}, workspace={workspace_info})"

    def __repr__(self) -> str:
        """
        Return a detailed string representation for debugging.
        """
        return self.__str__()

    def _get_capabilities(self) -> Dict:
        """
        Get the capabilities for the language server.
        Should be overridden by subclasses if needed.
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
                }
            },
            "diagnostics": {
                "dynamicRegistration": True
            }
        }

    def did_open(self, file_path):
        with open(file_path, 'r') as f:
            file_content = f.read()

        self._send_notification(
            "textDocument/didOpen",
            params={
                "textDocument": {
                    "uri": f"file://{file_path}",
                    "languageId": self.language_id,
                    "version": 1,
                    "text": file_content
                }
            }
        )
        self.workspace_file_version[file_path] = 1
    
    def did_change(self, file_path: str):
        # 读取整个文件内容
        with open(file_path, 'r') as f:
            content = f.read()

        file_version = self.workspace_file_version.get(file_path, 0)
        self._send_notification(
            "textDocument/didChange",
            params={
                "textDocument": {
                    "uri": f"file://{file_path}",
                    "version": file_version + 1
                },
                "contentChanges": [
                    {
                        "text": content
                    }
                ]
            }
        )
        self.workspace_file_version[file_path] = file_version + 1
    
    def open_in_batch(self, file_paths: List[str]):
        for file_path in file_paths:
            self.did_open(os.path.join(self.workspace_dir, file_path))
            
    def rename(self, file_path: str, position: dict[str, int], new_name: str, wait_time: float = 0.5):
        if self.workspace_file_version.get(file_path, 0) == 0:
            self.did_open(file_path)
        else:
            self.did_change(file_path)

        request_id = self._send_request(
            "textDocument/rename",
            params={
                "textDocument": {
                    "uri": f"file://{file_path}"
                },
                "position": position,
                "newName": new_name
            }
        )
        # Don't use message_num when waiting for specific request_id
        messages = self._get_messages(request_id=request_id, wait_time=wait_time)
        return messages
    
    def references(self, file_path, position, wait_time: float = 0.5, include_declaration: bool = True):
        if self.workspace_file_version.get(file_path, 0) == 0:
            self.did_open(file_path)
        else:
            self.did_change(file_path)
        
        request_id = self._send_request(
            "textDocument/references",
            params={
                "textDocument": {
                    "uri": f"file://{file_path}"
                },
                "position": position,
                "context": {
                    "includeDeclaration": include_declaration
                }
            }
        )
        messages = self._get_messages(request_id=request_id, message_num=1, wait_time=wait_time)
        return messages
    
    def definitions(self, file_path, position, wait_time: float = 0.5):
        if self.workspace_file_version.get(file_path, 0) == 0:
            self.did_open(file_path)
        else:
            self.did_change(file_path)
            
        request_id = self._send_request(
            "textDocument/definition",
            params={
                "textDocument": {
                    "uri": f"file://{file_path}"
                },
                "position": position
            }
        )
        messages = self._get_messages(request_id=request_id, message_num=1, wait_time=wait_time)
        return messages
    
    def diagnostics(self, file_path, wait_time: float = 0.5):
        if self.workspace_file_version.get(file_path, 0) == 0:
            self.did_open(file_path)
        else:
            self.did_change(file_path)

        # diagnose request must specify file_path, otherwise may return diagnostics of other files that are previously send by server and left in the message queue
        messages = self._get_messages(
            expect_method="textDocument/publishDiagnostics",
            message_num=1,
            wait_time=wait_time,
            expected_file_path=file_path
        )
        return messages

    def close(self):
        request_id = self._send_request("shutdown")
        close_message = self._get_messages(request_id=request_id, message_num=1, wait_time=0.5)
        self._send_notification("exit")
        self.process.terminate()
        self.process.wait()
        if self.logger is not None:
            self.logger.info("[LSP] Language Server closed.")
        
        return close_message

    def get_all_file_paths(self, workspace_path: str) -> List[str]:
        file_paths = []
        for root, _, files in os.walk(workspace_path):
            for file in files:
                file_paths.append(os.path.join(root, file))
        return file_paths

    def _read_by_brace_matching(self, timeout: float = 0.1) -> Optional[str]:
        """
        Read a complete JSON message by matching the braces.

        Args:
            timeout: Timeout for select (seconds)

        Returns:
            Optional[str]: Return a complete JSON message string, or None if timeout
        """
        buffer = ""
        brace_count = 0
        inside_str = False
        escaped = False  # 处理转义字符

        while True:
            char = self.process.stdout.read(1)

            # Handle EOF or empty read
            if not char:
                raise RuntimeError("Unexpected EOF while reading JSON message from language server")

            if buffer == "":
                if char != "{":
                    raise RuntimeError(f"Expected '{{' at start of JSON message, got '{char}'")

            buffer += char

            if escaped:
                escaped = False
                continue

            if char == '\\':
                escaped = True
            elif char == '"':
                inside_str = not inside_str
            elif not inside_str:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1

            if brace_count == 0:
                return buffer
        
    @timeout_decorator(timeout=5, timeout_return=[])
    def _get_messages(self, request_id: Optional[int] = None, expect_method: Optional[str] = None, message_num: Optional[int] = None, wait_time: Optional[float] = None, return_all: bool = False, expected_file_path: Optional[str] = None) -> List[Dict]:
        """
        Retrieve messages from the server based on specified conditions:
        - request_id: Stop when a specific request ID is received.
        - expect_method: Stop when a specific method is received.
        - message_num: Stop when a specific number of messages are received.
        - wait_time: Stop after the specified amount of time (in seconds).
        If both parameters are set, the function will stop when either condition is met.

        Args:
            request_id (Optional[int]): Request ID of the message to retrieve.
            expect_method (Optional[str]): Method of the message to retrieve.
            message_num (Optional[int]): Number of DESIRED messages to retrieve.
            wait_time (Optional[float]): Time in seconds allowed to wait for messages.
            return_all (bool): If False (default), only return desired messages.
                              If True, return all messages including logs.
            expected_file_path (Optional[str]): For diagnostics, the file path to match.

        Returns:
            List[Dict]: A list of received JSON-RPC messages.
        """
        buffer = ""
        start_time = time.time()

        all_messages = []
        desired_messages = []

        while True:
            line = self.process.stdout.readline()
            if not line:  # Exit if no more output is available
                break
            buffer += line
            match = re.search(r"Content-Length: (\d+)", buffer)
            if match:
                self.process.stdout.readline()  # Skip the blank line
                message = self._read_by_brace_matching()
                try:
                    json_message = json.loads(message.strip())

                    # Log the message if logging is enabled
                    if self.log:
                        print(f"[RECEIVED] {json.dumps(json_message, indent=2, ensure_ascii=False)}")

                    # Check if message matches criteria
                    is_desired = self._matches_criteria(
                        json_message, request_id, expect_method, expected_file_path
                    )

                    # Categorize and store messages
                    all_messages.append(json_message)
                    if is_desired:
                        desired_messages.append(json_message)

                        # If we found enough desired messages, return
                        if message_num is None or len(desired_messages) >= message_num:
                            return all_messages if return_all else desired_messages

                except json.JSONDecodeError as e:
                    raise Exception(f"JSON Parse Error: {e}, Original Message: {message}")
                buffer = ""  # Reset buffer after processing a message

            if wait_time is not None and (time.time() - start_time) >= wait_time:
                return all_messages if return_all else desired_messages

        return all_messages if return_all else desired_messages
    
    def _matches_criteria(self, json_message: Dict, request_id: Optional[int] = None,
                          expect_method: Optional[str] = None,
                          expected_file_path: Optional[str] = None) -> bool:
        """
        Pure function to check if a message matches the specified criteria.
        No side effects - only returns True/False.

        Args:
            json_message: The JSON-RPC message to check
            request_id: Check if message has this request ID
            expect_method: Check if message has this method name
            expected_file_path: For diagnostics, the file path to match against URI and version

        Returns:
            bool: True if message matches criteria, False otherwise
        """
        # Check request_id match
        if request_id is not None:
            if "id" in json_message and json_message["id"] == request_id:
                return True
            return False

        # Check expect_method match
        if expect_method is not None:
            if "method" not in json_message or json_message["method"] != expect_method:
                return False

            # Special handling for publishDiagnostics: check URI and version
            if expect_method == "textDocument/publishDiagnostics" and expected_file_path is not None:
                params = json_message.get("params", {})

                # Check URI matches the expected file
                expected_uri = f"file://{expected_file_path}"
                msg_uri = params.get("uri", "")
                if msg_uri != expected_uri:
                    return False

                # Check version matches client version
                msg_version = params.get("version", None)
                client_version = self.workspace_file_version.get(expected_file_path, None)

                # Only enforce version check if both LSP and client provide version info
                if msg_version is not None and client_version is not None:
                    if msg_version != client_version:
                        return False

            return True

        # Accept all messages when no criteria specified
        return True

    def _send_to_process(self, message: str):
        # Check if process is still running
        if self.process.poll() is not None:
            # Process has exited, read stderr to get error information
            stderr_output = self.process.stderr.read()
            raise RuntimeError(
                f"Language server process has exited with code {self.process.returncode}. "
                f"stderr: {stderr_output}"
            )

        try:
            # LSP protocol requires CRLF (\r\n) line endings on all platforms
            # Reference: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
            self.process.stdin.write(f"Content-Length: {len(message)}\r\n\r\n{message}")
            self.process.stdin.flush()
        except BrokenPipeError as e:
            # If we get a broken pipe, the process likely crashed
            stderr_output = self.process.stderr.read()
            raise RuntimeError(
                f"Failed to send message to language server (BrokenPipeError). "
                f"Process exit code: {self.process.poll()}, stderr: {stderr_output}"
            ) from e
    
    def _should_process_file(self, file_path: str) -> bool:
        """
        Check if this LSP server should process the given file.
        Default implementation checks common file extensions.
        Subclasses can override for more sophisticated checks.

        Args:
            file_path: Path to the file to check

        Returns:
            bool: True if this LSP should process the file
        """
        # Default extension mappings
        extension_map = {
            'python': ['.py', '.pyi'],
            'javascript': ['.js', '.jsx', '.mjs', '.cjs'],
            'typescript': ['.ts', '.tsx'],
            'java': ['.java'],
            'go': ['.go'],
        }

        language_extensions = extension_map.get(self.language_id, [])
        if not language_extensions:
            # If language not in map, accept all files (conservative)
            return True

        # Check if file has a supported extension
        return any(file_path.endswith(ext) for ext in language_extensions)
   
    def _create_message(self, method: str, params: dict = None, is_request: bool = True) -> str:
        message_data = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if is_request:
            message_data["id"] = self.request_id
            self.request_id += 1
        if params:
            message_data["params"] = params

        return message_data

    def _send_notification(self, method: str, params: dict = None):
        notification = self._create_message(method, params, is_request=False)
        notification = json.dumps(notification)
        self._send_to_process(notification)

    def _send_request(self, method: str, params: dict = None):
        request = self._create_message(method, params, is_request=True)
        request_id = request["id"]
        request_json = json.dumps(request)
        self._send_to_process(request_json)
        return request_id
    
    @abstractmethod
    def _parse_rename_response(self, response, edits):
        """
        Parse the response of rename request and update the edits
        Implemented in subclasses
        """
        pass
    
    @abstractmethod
    def _filter_diagnostics(self, diagnostics, last_edit_at_range, init_diagnose_msg):
        """
        Filter out the diagnostics that are not very helpful
        If not implemented, return the original diagnostics
        """
        pass
    
    def acquire_diagnose(self, files_to_diagnose, last_edit_region):
        diagnostics = []
        for file_path in files_to_diagnose:
            abs_file_path = os.path.join(self.workspace_dir, file_path)

            # Skip files that don't match this LSP's language
            if not self._should_process_file(file_path):
                if self.logger:
                    self.logger.debug(f"[LSP] Skipping {file_path} - not supported by {self.language_id} LSP")
                continue

            try:
                response = self.diagnostics(abs_file_path, wait_time=2.0)
            except TimeoutError as e:
                # Timeout is not necessarily an error - file might be valid but LSP is slow
                if self.logger:
                    self.logger.warning(f"[LSP] Timeout acquiring diagnostics for {file_path}: {e}")
                continue
            except Exception as e:
                # Log and skip this file, but continue with others
                if self.logger:
                    self.logger.error(f"[LSP] Error acquiring diagnostics for {file_path}: {e}")
                continue
            
            if response == []:
                continue
            else:
                response = response[0]
                
            if (response is None or \
                "params" not in response or \
                response["params"] is None or \
                "diagnostics" not in response["params"] or \
                response["params"]["diagnostics"] is None or \
                len(response["params"]["diagnostics"]) == 0
            ):
                continue
            if response["params"]["uri"][7:] != abs_file_path:
                continue
            for diagnostic in response["params"]["diagnostics"]:
                diagnostic["file_path"] = file_path
                diagnostic["abs_file_path"] = abs_file_path
            diagnostics.extend(response["params"]["diagnostics"])
            
        if hasattr(self, "init_diagnose_msg"):
            diagnostics = self._filter_diagnostics(diagnostics, last_edit_region, self.init_diagnose_msg)
        else:
            diagnostics = self._filter_diagnostics(diagnostics, None, [])
        if diagnostics is None or len(diagnostics) == 0:
            self.init_diagnose_msg = []
        else:
            msgs = []
            for diagnose in diagnostics:
                msgs.append(diagnose["message"])
            self.init_diagnose_msg = msgs
        
        return diagnostics