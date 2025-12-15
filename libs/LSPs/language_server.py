import os
import re
import json
import time
import subprocess
import select
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

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
                bufsize=0  # Binary mode (text=False is default)
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
        start_time = time.time()
        all_messages = []
        desired_messages = []

        # Default timeout for individual message reads
        default_message_timeout = 5.0

        while True:
            # Calculate remaining time
            if wait_time is not None:
                elapsed = time.time() - start_time
                remaining_time = wait_time - elapsed

                if remaining_time <= 0:
                    # Time's up, return what we have
                    return all_messages if return_all else desired_messages

                # Use the smaller of remaining time or default timeout
                read_timeout = min(remaining_time, default_message_timeout)
            else:
                read_timeout = default_message_timeout

            # Try to read a message
            try:
                json_message = self._read_single_message(timeout=read_timeout)

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

                    # If we found enough desired messages, return immediately
                    if message_num is None or len(desired_messages) >= message_num:
                        return all_messages if return_all else desired_messages

            except TimeoutError:
                # Timeout while reading a message
                # If we have wait_time set, this is expected behavior
                # Return what we collected so far
                return all_messages if return_all else desired_messages

            except (RuntimeError, json.JSONDecodeError) as e:
                # Fatal error - LSP server sent malformed data or crashed
                if self.logger:
                    self.logger.error(f"[LSP] Error reading message: {e}")
                # Return what we have collected so far
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

    def _read_exact_bytes(self, num_bytes: int, timeout: float = 5.0) -> bytes:
        """
        Read exactly num_bytes from stdout with timeout.

        Args:
            num_bytes: Exact number of bytes to read
            timeout: Timeout in seconds

        Returns:
            bytes: Exactly num_bytes of data

        Raises:
            TimeoutError: If timeout is exceeded
            RuntimeError: If EOF is reached before reading num_bytes
        """
        buffer = b''
        deadline = time.time() + timeout

        while len(buffer) < num_bytes:
            remaining_time = deadline - time.time()
            if remaining_time <= 0:
                raise TimeoutError(
                    f"Timeout while reading {num_bytes} bytes. "
                    f"Only got {len(buffer)} bytes after {timeout}s"
                )

            # Use select to wait for data with timeout
            ready, _, _ = select.select([self.process.stdout], [], [], remaining_time)

            if not ready:
                raise TimeoutError(
                    f"Timeout while reading {num_bytes} bytes. "
                    f"Only got {len(buffer)} bytes after {timeout}s"
                )

            # Read remaining bytes
            remaining = num_bytes - len(buffer)
            chunk = self.process.stdout.read(remaining)

            if not chunk:
                raise RuntimeError(
                    f"Unexpected EOF while reading {num_bytes} bytes. "
                    f"Only got {len(buffer)} bytes"
                )

            buffer += chunk

        return buffer

    def _read_line(self, timeout: float = 5.0) -> bytes:
        """
        Read a line from stdout with timeout.
        Returns the line including the newline character(s) as bytes.

        Args:
            timeout: Timeout in seconds

        Returns:
            bytes: A line of bytes including newline

        Raises:
            TimeoutError: If timeout is exceeded
            RuntimeError: If EOF is reached
        """
        buffer = b''
        deadline = time.time() + timeout

        while True:
            remaining_time = deadline - time.time()
            if remaining_time <= 0:
                raise TimeoutError(
                    f"Timeout while reading line after {timeout}s. "
                    f"Partial content: {buffer[:100]}"
                )

            # Use select to wait for data
            ready, _, _ = select.select([self.process.stdout], [], [], remaining_time)

            if not ready:
                raise TimeoutError(
                    f"Timeout while reading line after {timeout}s. "
                    f"Partial content: {buffer[:100]}"
                )

            char = self.process.stdout.read(1)

            if not char:
                if buffer:
                    # Got EOF with partial line
                    raise RuntimeError(
                        f"Unexpected EOF while reading line. "
                        f"Partial content: {buffer}"
                    )
                else:
                    # Got EOF at start
                    raise RuntimeError("Unexpected EOF from language server")

            buffer += char

            # Check for line ending (handle both \n and \r\n)
            if buffer.endswith(b'\n'):
                return buffer

    def _read_single_message(self, timeout: float = 5.0) -> Dict:
        """
        Read a single complete LSP message with proper timeout handling.

        This method ensures:
        1. Headers are read line by line until blank line
        2. Content-Length is parsed from headers
        3. Exactly Content-Length bytes are read (no more, no less)
        4. Message is decoded as UTF-8 and parsed as JSON

        Args:
            timeout: Total timeout for reading the entire message

        Returns:
            Dict: Parsed JSON-RPC message

        Raises:
            TimeoutError: If reading exceeds timeout
            RuntimeError: If LSP server sends malformed message
            json.JSONDecodeError: If message body is not valid JSON
        """
        start_time = time.time()

        # Step 1: Read headers until we hit a blank line
        headers = {}
        while True:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                raise TimeoutError(f"Timeout reading message headers after {timeout}s")

            line = self._read_line(timeout=remaining_time)

            # Blank line indicates end of headers
            if line == b'\r\n' or line == b'\n':
                break

            # Parse header (format: "Key: Value\r\n")
            # Decode to string for parsing
            line_str = line.rstrip(b'\r\n').decode('utf-8')
            if ':' in line_str:
                key, value = line_str.split(':', 1)
                headers[key.strip()] = value.strip()

        # Step 2: Extract Content-Length
        if 'Content-Length' not in headers:
            raise RuntimeError(
                f"LSP message missing Content-Length header. Got headers: {headers}"
            )

        try:
            content_length = int(headers['Content-Length'])
        except ValueError as e:
            raise RuntimeError(
                f"Invalid Content-Length value: {headers['Content-Length']}"
            ) from e

        if content_length < 0:
            raise RuntimeError(f"Negative Content-Length: {content_length}")

        if content_length > 100 * 1024 * 1024:  # 100MB sanity check
            raise RuntimeError(
                f"Content-Length too large: {content_length} bytes. "
                f"Possible protocol error."
            )

        # Step 3: Read exactly content_length bytes
        remaining_time = timeout - (time.time() - start_time)
        if remaining_time <= 0:
            raise TimeoutError(f"Timeout before reading message body after {timeout}s")

        message_bytes = self._read_exact_bytes(content_length, timeout=remaining_time)

        # Verify we got exactly the right amount
        if len(message_bytes) != content_length:
            raise RuntimeError(
                f"Expected {content_length} bytes but got {len(message_bytes)} bytes"
            )

        # Step 4: Decode and parse JSON
        try:
            message_str = message_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            raise RuntimeError(
                f"Failed to decode message as UTF-8. "
                f"First 100 bytes: {message_bytes[:100]}"
            ) from e

        try:
            json_message = json.loads(message_str)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Failed to parse LSP message as JSON: {e.msg}. "
                f"Message preview: {message_str[:200]}",
                e.doc,
                e.pos
            )

        return json_message

    def _send_to_process(self, message: str):
        """
        Send a message to the LSP server process.

        IMPORTANT: Content-Length MUST be the byte count, not character count!
        This is critical for messages containing non-ASCII characters.

        Args:
            message: JSON-RPC message as a string

        Raises:
            RuntimeError: If process has exited or pipe is broken
        """
        # Check if process is still running
        if self.process.poll() is not None:
            # Process has exited, read stderr to get error information
            stderr_bytes = self.process.stderr.read()
            stderr_output = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
            raise RuntimeError(
                f"Language server process has exited with code {self.process.returncode}. "
                f"stderr: {stderr_output}"
            )

        try:
            # LSP protocol requires:
            # 1. Content-Length header with BYTE count (not character count!)
            # 2. CRLF (\r\n) line endings on all platforms
            # Reference: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/

            # Encode message to bytes
            message_bytes = message.encode('utf-8')
            byte_length = len(message_bytes)

            # Construct the full LSP message with proper header (as bytes)
            header = f"Content-Length: {byte_length}\r\n\r\n".encode('utf-8')
            full_message = header + message_bytes

            self.process.stdin.write(full_message)
            self.process.stdin.flush()

        except BrokenPipeError as e:
            # If we get a broken pipe, the process likely crashed
            stderr_bytes = self.process.stderr.read()
            stderr_output = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
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
                raise e
            
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