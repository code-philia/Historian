import os
import re
import time
import requests
import platform
import subprocess

from dotenv import load_dotenv
from collections import defaultdict
from tree_sitter import Language, Parser
from .bleu import direct_computeMaps, bleuFromMaps

curr_file_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(curr_file_dir, "../.env"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

def clone_repo(user_name: str, project_name: str, target_dir: str):
    """
    Clone the repository to local
    
    Args:
        user_name: str, the user name of the repository
        project_name: str, the name of the repository
        target_dir: str, the target directory to clone the repository
    Returns:
        None
    """
    command = f"git clone https://github.com/{user_name}/{project_name}.git {target_dir}/{project_name}"
    subprocess.run(command, shell=True)

def detect_extension(file_names: list[str]):
    # Use os.path.basename to get the file name
    for file_name in file_names:
        filename = os.path.basename(file_name)
        # Use splitext to split file name and extension
        file_name_elements = filename.split('.')
        if len(file_name_elements) == 2:
            extension = '.'+file_name_elements[-1]
        else:
            extension =  '.'+'.'.join(file_name_elements[-2:])
        white_list = ['.go', '.js', '.java', '.py', '.ts', '.tsx']
        if extension not in white_list:
            return True
    return False

def convert_diff_section_to_snapshot(file_w_diff: str):
    diff_content = file_w_diff.splitlines(keepends=True)
    snapshot = []
    consecutive_code = []
    under_edit = False
    edits = []
    for line in diff_content:
        if line.startswith(" ") and under_edit == False:
            consecutive_code.append(line[1:])
        elif line.startswith(" ") and under_edit == True:
            under_edit = False
            if edit["type"] == "replace" and edit["after"] == []:
                edit["type"] = "delete"
            snapshot.append(edit.copy())
            consecutive_code.append(line[1:]) 
        elif line.startswith("-") and under_edit == False:
            under_edit = True
            if consecutive_code != []:
                snapshot.append(consecutive_code.copy())
            consecutive_code = []
            edit = {
                "type": "replace",
                "before": [],
                "after": []
            }
            edit["before"].append(line[1:])
        elif line.startswith("+") and under_edit == False:
            under_edit = True
            if consecutive_code != []:
                snapshot.append(consecutive_code.copy())
            consecutive_code = []
            edit = {
                "type": "insert",
                "before": [],
                "after": []
            }
            edit["after"].append(line[1:])
        elif line.startswith("+") and under_edit == True:
            edit["after"].append(line[1:])
        elif line.startswith("-") and under_edit == True:
            edit["before"].append(line[1:])
    if under_edit == True:
        if edit["type"] == "replace" and edit["after"] == []:
            edit["type"] = "delete"
        snapshot.append(edit.copy())
    if under_edit == False:
        snapshot.append(consecutive_code.copy())
    
    for window in snapshot:
        if type(window) == dict:
            edits.append(window)
    return snapshot, edits

def check_language(file_path: str):
    # Use os.path.splitext to get the file extension
    _, extension = os.path.splitext(file_path)
    if extension == '.go':
        return 'go'
    elif extension == '.js':
        return 'javascript'
    elif extension == '.java':
        return 'java'
    elif extension == '.py':
        return 'python'
    elif extension == '.ts' or extension == '.tsx':
        return 'typescript'
    else:
        return None

def find_code_structure(code, line_index, language):
    # Initialize Tree-sitter parser and set language
    parser = get_parser(language)

    # Parse code to generate syntax tree
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    # ============ Python ============
    def get_declaration_text_py(node):
        declaration = ""
        name = None 
        
        if node.type == node_types['class']:
            for child in node.children:
                if child.type == "class":
                    declaration += "class "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "argument_list":
                    declaration += child.text.decode("utf-8")
                elif child.type == ":":
                    declaration += child.text.decode("utf-8")
            return declaration, name
        elif node.type == node_types['function']:
            for child in node.children:
                if child.type == "def":
                    declaration += "def "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == ":":
                    declaration += child.text.decode("utf-8")
                elif child.type == "->":
                    declaration += " -> "
                elif child.type == "type":
                    declaration += child.text.decode("utf-8")
            return declaration, name
        return None, None
    
    def get_call_info_py(node):
        call_info = ""
        function_name = None
        
        if node.type == "call":
            for child in node.children:
                if child.type == "identifier":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "attribute":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "argument_list":
                    call_info += child.text.decode("utf-8")
                    break
        
        return (function_name, call_info) if function_name else (None, None)

    def find_argument_in_call_py(node, target_line):
        if node.type != "call":
            return None
            
        for child in node.children:
            if child.type == "argument_list":
                for arg_child in child.children:
                    if (arg_child.start_point[0] <= target_line <= arg_child.end_point[0] and 
                        arg_child.type not in [",", "(", ")"]):
                        
                        if arg_child.type == "keyword_argument":
                            for kw_child in arg_child.children:
                                if kw_child.type == "identifier":
                                    return f"{kw_child.text.decode('utf-8')}=..."
                        else:
                            arg_text = arg_child.text.decode('utf-8')
                            if len(arg_text) > 30:
                                return f"{arg_text[:30]}..."
                            return arg_text
        return None

    # ============ Go ============
    def get_declaration_text_go(node):
        declaration = ""
        name = None
        
        if node.type == node_types['function']:
            for child in node.children:
                if child.type == "func":
                    declaration += "func "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "parameter_list":
                    declaration += child.text.decode("utf-8")
                elif child.type == "type_identifier":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == "pointer_type":
                    declaration += " " + child.text.decode("utf-8")
            return declaration, name

        elif node.type == node_types['class']:
            for child in node.children:
                if child.type == "type":
                    declaration += "type "
                elif child.type == "type_spec":
                    for grandchild in child.children:
                        if grandchild.type == "type_identifier":
                            declaration += grandchild.text.decode("utf-8")
                            name = grandchild.text.decode("utf-8")
                        elif grandchild.type == "struct_type":
                            declaration += " struct"
                        elif grandchild.type == "interface_type":
                            declaration += " interface"
            return declaration, name

        elif node.type == node_types['method']:
            for child in node.children:
                if child.type == "func":
                    declaration += "func "
                elif child.type == "parameter_list":
                    declaration += child.text.decode("utf-8") + " "
                elif child.type == "field_identifier":
                    name = child.text.decode("utf-8")
                    declaration += name
                elif child.type == "type_identifier":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == "pointer_type":
                    declaration += " " + child.text.decode("utf-8")
            return declaration, name

        return None, None
    
    def get_call_info_go(node):
        call_info = ""
        function_name = None
        
        if node.type == "call_expression":
            for child in node.children:
                if child.type == "identifier":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "selector_expression":
                    # Method call like obj.Method()
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "argument_list":
                    call_info += child.text.decode("utf-8")
                    break
        
        return (function_name, call_info) if function_name else (None, None)
    
    def find_argument_in_call_go(node, target_line):
        if node.type != "call_expression":
            return None
            
        for child in node.children:
            if child.type == "argument_list":
                for arg_child in child.children:
                    if (arg_child.start_point[0] <= target_line <= arg_child.end_point[0] and 
                        arg_child.type not in [",", "(", ")"]):
                        arg_text = arg_child.text.decode('utf-8')
                        if len(arg_text) > 30:
                            return f"{arg_text[:30]}..."
                        return arg_text
        return None

    # ============ Java ============
    def get_declaration_text_java(node):
        declaration = ""
        name = None

        if node.type == node_types['class']:
            for child in node.children:
                if child.type == "modifiers":
                    for grandchild in child.children:
                        if grandchild.text.decode("utf-8").startswith("@"):
                            continue
                        declaration += grandchild.text.decode("utf-8") + " "
                elif child.type == "class":
                    declaration += "class "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "superclass":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == "super_interfaces":
                    declaration += " implements "
                    for grandchild in child.children:
                        if grandchild.type == "type_list":
                            declaration += grandchild.text.decode("utf-8")
                elif child.type == "{":
                    declaration += " {"
            return declaration, name

        elif node.type == node_types['function']:
            for child in node.children:
                if child.type == "modifiers":
                    for grandchild in child.children:
                        if grandchild.text.decode("utf-8").startswith("@"):
                            continue
                        declaration += grandchild.text.decode("utf-8") + " "
                elif child.type in ["type_identifier", "void_type", "generic_type", 
                                    "array_type", "integral_type", "floating_point_type",
                                    "boolean_type"]:
                    declaration += child.text.decode("utf-8") + " "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "throws":
                    declaration += " throws "
                    for grandchild in child.children:
                        if grandchild.type == "type_identifier":
                            declaration += grandchild.text.decode("utf-8") + ", "
                    declaration = declaration.rstrip(", ")
                elif child.type == "{":
                    declaration += " {"
            return declaration, name

        return None, None
    
    def get_call_info_java(node):
        call_info = ""
        function_name = None
        
        if node.type == "method_invocation":
            for child in node.children:
                if child.type == "identifier":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "field_access":
                    # Chained call
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "argument_list":
                    call_info += child.text.decode("utf-8")
                    break
        
        return (function_name, call_info) if function_name else (None, None)
    
    def find_argument_in_call_java(node, target_line):
        if node.type != "method_invocation":
            return None
            
        for child in node.children:
            if child.type == "argument_list":
                for arg_child in child.children:
                    if (arg_child.start_point[0] <= target_line <= arg_child.end_point[0] and 
                        arg_child.type not in [",", "(", ")"]):
                        arg_text = arg_child.text.decode('utf-8')
                        if len(arg_text) > 30:
                            return f"{arg_text[:30]}..."
                        return arg_text
        return None

    # ============ JavaScript ============
    def get_declaration_text_js(node):
        declaration = ""
        name = None
        
        if node.type == node_types['class']:
            for child in node.children:
                if child.type == "class":
                    declaration += "class "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "class_heritage":
                    declaration += " " + child.text.decode("utf-8")
            return declaration, name

        elif node.type == node_types['function']:
            for child in node.children:
                if child.type == "async":
                    declaration += "async "
                elif child.type == "function":
                    declaration += "function "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
            return declaration, name
        
        elif node.type == node_types['method']:
            for child in node.children:
                if child.type == "async":
                    declaration += "async "
                elif child.type == "property_identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
            return declaration, name
        
        # Arrow function (variable_declarator contains arrow_function)
        elif node.type == "arrow_function":
            # Arrow function itself has no name, need to get it from parent node
            for child in node.children:
                if child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "identifier":
                    # Single parameter arrow function
                    declaration += "(" + child.text.decode("utf-8") + ")"
            declaration += " =>"
            return declaration, name

        return None, None
    
    def get_call_info_js(node):
        call_info = ""
        function_name = None
        
        if node.type == "call_expression":
            for child in node.children:
                if child.type == "identifier":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "member_expression":
                    # Method call like obj.method()
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "arguments":
                    call_info += child.text.decode("utf-8")
                    break
        
        return (function_name, call_info) if function_name else (None, None)
    
    def find_argument_in_call_js(node, target_line):
        if node.type != "call_expression":
            return None
            
        for child in node.children:
            if child.type == "arguments":
                for arg_child in child.children:
                    if (arg_child.start_point[0] <= target_line <= arg_child.end_point[0] and 
                        arg_child.type not in [",", "(", ")"]):
                        arg_text = arg_child.text.decode('utf-8')
                        if len(arg_text) > 30:
                            return f"{arg_text[:30]}..."
                        return arg_text
        return None

    # ============ TypeScript ============
    def get_declaration_text_ts(node):
        declaration = ""
        name = None
        
        if node.type == node_types['class']:
            for child in node.children:
                if child.type == "class":
                    declaration += "class "
                elif child.type == "type_identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "class_heritage":
                    declaration += " " + child.text.decode("utf-8")
            return declaration, name
                
        elif node.type == node_types['function']:
            for child in node.children:
                if child.type == "async":
                    declaration += "async "
                elif child.type == "function":
                    declaration += "function "
                elif child.type == "identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "type_annotation":
                    declaration += child.text.decode("utf-8")
            return declaration, name
                
        elif node.type == node_types['method']:
            for child in node.children:
                if child.type == "async":
                    declaration += "async "
                elif child.type == "accessibility_modifier":
                    declaration += child.text.decode("utf-8") + " "
                elif child.type == "property_identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "type_annotation":
                    declaration += child.text.decode("utf-8")
            return declaration, name
        
        # Interface declaration
        elif node.type == "interface_declaration":
            for child in node.children:
                if child.type == "interface":
                    declaration += "interface "
                elif child.type == "type_identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == "extends_type_clause":
                    declaration += " " + child.text.decode("utf-8")
            return declaration, name
        
        # Type alias
        elif node.type == "type_alias_declaration":
            for child in node.children:
                if child.type == "type":
                    declaration += "type "
                elif child.type == "type_identifier":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_parameters":
                    declaration += child.text.decode("utf-8")
            return declaration, name
            
        return None, None
    
    def get_call_info_ts(node):
        call_info = ""
        function_name = None
        
        if node.type == "call_expression":
            for child in node.children:
                if child.type == "identifier":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "member_expression":
                    function_name = child.text.decode("utf-8")
                    call_info = function_name
                elif child.type == "arguments":
                    call_info += child.text.decode("utf-8")
                    break
        
        return (function_name, call_info) if function_name else (None, None)
    
    def find_argument_in_call_ts(node, target_line):
        if node.type != "call_expression":
            return None
            
        for child in node.children:
            if child.type == "arguments":
                for arg_child in child.children:
                    if (arg_child.start_point[0] <= target_line <= arg_child.end_point[0] and 
                        arg_child.type not in [",", "(", ")"]):
                        arg_text = arg_child.text.decode('utf-8')
                        if len(arg_text) > 30:
                            return f"{arg_text[:30]}..."
                        return arg_text
        return None

    # ============ Language Configuration ============
    language_nodes = {
        "python": {
            "class": "class_definition",
            "function": "function_definition", 
            "call": "call",
            "get_signature_fn": get_declaration_text_py,
            "get_call_info_fn": get_call_info_py,
            "find_argument_fn": find_argument_in_call_py
        },
        "go": {
            "class": "type_declaration",
            "function": "function_declaration",
            "method": "method_declaration",
            "call": "call_expression",
            "get_signature_fn": get_declaration_text_go,
            "get_call_info_fn": get_call_info_go,
            "find_argument_fn": find_argument_in_call_go
        },
        "java": {
            "class": "class_declaration", 
            "function": "method_declaration",
            "call": "method_invocation",
            "get_signature_fn": get_declaration_text_java,
            "get_call_info_fn": get_call_info_java,
            "find_argument_fn": find_argument_in_call_java
        },
        "javascript": {
            "class": "class_declaration",
            "function": "function_declaration", 
            "method": "method_definition",
            "arrow_function": "arrow_function",
            "call": "call_expression",
            "get_signature_fn": get_declaration_text_js,
            "get_call_info_fn": get_call_info_js,
            "find_argument_fn": find_argument_in_call_js
        },
        "typescript": {
            "class": "class_declaration",
            "function": "function_declaration",
            "method": "method_definition",
            "interface": "interface_declaration",
            "type_alias": "type_alias_declaration",
            "call": "call_expression",
            "get_signature_fn": get_declaration_text_ts,
            "get_call_info_fn": get_call_info_ts,
            "find_argument_fn": find_argument_in_call_ts
        },
    }

    node_types = language_nodes[language]

    # Traverse syntax tree to find the structure path corresponding to the line number
    def traverse(node, current_structure=[]):
        if node.start_point[0] <= line_index <= node.end_point[0]:
            # Class definition
            if node.type == node_types['class']:
                class_declaration, class_name = node_types["get_signature_fn"](node)
                if class_declaration:
                    current_structure.append({
                        "type": "class",
                        "name": class_name,
                        "signature": class_declaration,
                        "at_line": node.start_point[0]
                    })

            # Function definition
            elif node.type == node_types['function']:
                function_declaration, function_name = node_types["get_signature_fn"](node)
                if function_declaration:
                    current_structure.append({
                        "type": "function",
                        "name": function_name,
                        "signature": function_declaration,
                        "at_line": node.start_point[0]
                    })

            # Method definition
            elif node_types.get('method') and node.type == node_types['method']:
                method_declaration, method_name = node_types["get_signature_fn"](node)
                if method_declaration:
                    current_structure.append({
                        "type": "method",
                        "name": method_name,
                        "signature": method_declaration,
                        "at_line": node.start_point[0]
                    })

            # Function call
            elif node_types.get('call') and node.type == node_types['call']:
                if "get_call_info_fn" in node_types:
                    function_name, call_signature = node_types["get_call_info_fn"](node)
                    if function_name:
                        call_entry = {
                            "type": "call",
                            "name": function_name,
                            "signature": call_signature,
                            "at_line": node.start_point[0]
                        }
                        
                        # Find argument information
                        if "find_argument_fn" in node_types:
                            argument_info = node_types["find_argument_fn"](node, line_index)
                            if argument_info:
                                call_entry["argument"] = argument_info
                        
                        current_structure.append(call_entry)

            # TypeScript interface
            elif node_types.get('interface') and node.type == node_types['interface']:
                interface_declaration, interface_name = node_types["get_signature_fn"](node)
                if interface_declaration:
                    current_structure.append({
                        "type": "interface",
                        "name": interface_name,
                        "signature": interface_declaration,
                        "at_line": node.start_point[0]
                    })
            
            # TypeScript type alias
            elif node_types.get('type_alias') and node.type == node_types['type_alias']:
                type_declaration, type_name = node_types["get_signature_fn"](node)
                if type_declaration:
                    current_structure.append({
                        "type": "type_alias",
                        "name": type_name,
                        "signature": type_declaration,
                        "at_line": node.start_point[0]
                    })

            # JavaScript arrow function
            elif node_types.get('arrow_function') and node.type == node_types['arrow_function']:
                arrow_declaration, _ = node_types["get_signature_fn"](node)
                if arrow_declaration:
                    # Try to get variable name from parent node
                    arrow_name = None
                    if node.parent and node.parent.type == "variable_declarator":
                        for sibling in node.parent.children:
                            if sibling.type == "identifier":
                                arrow_name = sibling.text.decode("utf-8")
                                break
                    
                    current_structure.append({
                        "type": "arrow_function",
                        "name": arrow_name,
                        "signature": (arrow_name + " = " if arrow_name else "") + arrow_declaration,
                        "at_line": node.start_point[0]
                    })

            # Recursively check child nodes
            for child in node.children:
                result = traverse(child, current_structure)
                if result:
                    return result

            return current_structure

        return []

    structure_path = traverse(root_node)
    return structure_path

def get_parser(language):
    assert language in ["python", "go", "java", "javascript", "typescript"]
    system = platform.system().lower()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tree_sitter_dir = os.path.normpath(os.path.join(base_dir, "../libs/tree-sitter"))
    if system == "darwin":
        build_file_path = os.path.join(tree_sitter_dir, "macos_build/my-languages.so")
    elif system == "linux":
        build_file_path = os.path.join(tree_sitter_dir, "linux_build/my-languages.so")
    elif system == "windows":
        build_file_path = os.path.join(tree_sitter_dir, "windows_build/my-languages.dll")
    try:
        LANGUAGES = Language(build_file_path, language)
    except:
        # build so
        Language.build_library(
            build_file_path,
            [
                os.path.join(tree_sitter_dir, "tree-sitter-python"),
                os.path.join(tree_sitter_dir, "tree-sitter-go"),
                os.path.join(tree_sitter_dir, "tree-sitter-java"),
                os.path.join(tree_sitter_dir, "tree-sitter-javascript"),
                os.path.join(tree_sitter_dir, "tree-sitter-typescript")
            ]
        )
        LANGUAGES = Language(build_file_path, language)
    
    parser = Parser()
    parser.set_language(LANGUAGES)
    return parser

def parse(code, language):
    parser = get_parser(language)
    tree = parser.parse(bytes(code, "utf8"))
    return tree

def extract_hunks(commit_url: str, REPOS_PATH: str) -> tuple:
    """
    Given commit url, extract edit hunks from the commit, with its file path and code logic path
    
    Args:
        commit_url: str, the url of the commit
        
    Returns:
        commit_message: str, the message of the commit
        commit_snapshots: dict, key is file path, value is list of snapshot of the file
    """
    commit_sha = commit_url.split("/")[-1]
    project_name = commit_url.split("/")[-3]
    user_name = commit_url.split("/")[-4]
    repo_path = os.path.join(REPOS_PATH, project_name)

    # if not exist, clone to local
    os.makedirs(REPOS_PATH, exist_ok=True)
    if not os.path.exists(repo_path):
        clone_repo(user_name, project_name, REPOS_PATH)
    
    command = f"git -C {repo_path} show {commit_sha} --pretty=%B --no-patch"
    try:
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except:
        raise ValueError(f'1 {commit_url} Error: Error in retrieving commit message')
    commit_message = result.stdout.strip()

    command = f"git -C {repo_path} checkout --force {commit_sha}^"
    try:
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except:
        raise ValueError(f'2 {commit_url} Error: Error in git checkout')
    
    command = f'git -C {repo_path} diff -U10000000 {commit_sha}^ {commit_sha}'
    try:
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except:
        raise ValueError(f'1 {commit_url} Error: Error in git diff')
    git_diff_str = result.stdout
    
    file_name_matches = re.finditer(r'diff --git a/(.+) b/(.+)', git_diff_str)
    file_names = []
    for match in file_name_matches:
        before_filename = match.group(1)
        after_filename = match.group(2)
        try:
            assert before_filename == after_filename
        except:
            raise ValueError(f"{commit_url} Error: Contain edit changes file name: {before_filename} -> {after_filename}")
        file_names.append(before_filename)
    
    if detect_extension(file_names):
        raise ValueError(f'{commit_url} Error: Contain edit on non-source files')
    
    # Split into diff section, 1 section = 1 file
    diff_sections = re.findall(r'diff --git[^\n]*\n.*?(?=\ndiff --git|$)', git_diff_str, re.DOTALL)
    all_edit_num = 0
    commit_snapshots = {}
    for i, section in enumerate(diff_sections):
        # Parse file name (w/ path), make sure edit don't change file name
        file_name_match = re.match(r'diff --git a/(.+) b/(.+)', section)
        if file_name_match:
            file_name = file_name_match.group(1)
        else:
            raise ValueError(f"5 {commit_url} Error: file name contain non-ascii char")
        
        # Get the diff of the whole file
        # (if -U{number} is set large enough, a file should contain only 1 @@ -xx,xx +xx,xx @@)
        # we can only make snapshot based on the diff of the whole file
        match = re.search(r'@@[^\n]*\n(.+)', section, re.DOTALL)
        if not match:
            raise ValueError(f"4 {commit_url} Error: Edit fail to match @@ -xx,xx +xx,xx @@")
        # Match content after the @@ line
        after_at_symbol_content = match.group(1)
        # form snapshot: each element:
        # type 1: list of line of code, unchanged
        # type 2: dict of edit, have key: "type", "before", "after"
        snapshot, _ = convert_diff_section_to_snapshot(after_at_symbol_content)
        
        # count line index
        parent_version_line_index = 0
        child_version_line_index = 0
        for window in snapshot:
            if type(window) is list:
                parent_version_line_index += len(window)
                child_version_line_index += len(window)
            else:
                window["parent_version_range"] = {
                    "start": parent_version_line_index,
                    "end": parent_version_line_index + len(window["before"])
                }
                window["child_version_range"] = {
                    "start": child_version_line_index,
                    "end": child_version_line_index + len(window["after"])
                }
                if window["before"] != []:
                    parent_version_line_index += len(window["before"])
                if window["after"] != []:
                    child_version_line_index += len(window["after"])
        commit_snapshots[file_name] = snapshot
        
    # extract code logic path for each hunk
    hunk_idx = 0
    for rel_file_path, snapshot in commit_snapshots.items():
        abs_file_path = os.path.join(repo_path, rel_file_path)
        file_content = ""
        for window in snapshot:
            if isinstance(window, list):
                file_content += "".join(window)
            else:
                file_content += "".join(window["before"])

        for widx, window in enumerate(snapshot):
            if type(window) is list:
                continue
            # only deal with edit hunks

            line_index = window["parent_version_range"]["start"]
            language = check_language(abs_file_path)
                    
            if window["before"] == [] and window["after"] != []:
                line_index -= 1

            prev_window = snapshot[widx-1] if widx > 0 else None
            next_window = snapshot[widx+1] if widx < len(snapshot)-1 else None
            
            if prev_window is None:
                window["prefix"] = []
            else:
                window["prefix"] = prev_window[-1 * min(3, len(prev_window)):]
            
            if next_window is None:
                window["suffix"] = []
            else:
                window["suffix"] = next_window[:min(3, len(next_window))]
                    
            structural_path = find_code_structure(file_content, line_index, language)
            window["structural_path"] = structural_path
            window["idx"] = hunk_idx
            window["simulated"] = False
            window["allowed_as_next"] = False
            window["file_path"] = rel_file_path
            hunk_idx += 1
            
    return commit_message, commit_snapshots

def snapshot_2_locations(snapshots):
    """
    Convert snapshots to edit locations.
    """
    enriched_snapshots = add_info_to_snapshots(snapshots)
    replace_edit_locations = []
    insert_edit_locations = []

    has_confidence = False
    for file_path, snapshot in snapshots.items():
        line_idx = 0
        for window in snapshot:
            if isinstance(window, list):
                line_idx += len(window)
            else:
                if "confidence" in window and window["confidence"] is not None:
                    has_confidence = True
                if window["before"]:
                    loc = {
                        "file_path": file_path,
                        "atLines": [i for i in range(line_idx, line_idx + len(window["before"]))],
                        "editType": "replace",
                        "after": window["after"],
                        "confidence": window.get("confidence", None),
                        "suggestionRank": None
                    }
                    if "idx" in window:
                        loc["idx"] = window["idx"]
                    if "allowed_as_next" in window:
                        loc["allowed_as_next"] = window["allowed_as_next"]
                    replace_edit_locations.append(loc)
                    line_idx += len(window["before"])
                else:
                    loc = {
                        "file_path": file_path,
                        "atLines": [line_idx],
                        "editType": "insert",
                        "after": window["after"],
                        "structural_path": window["structural_path"],
                        "confidence": window.get("confidence", None),
                        "suggestionRank": None
                    }
                    if "idx" in window:
                        loc["idx"] = window["idx"]
                    if "allowed_as_next" in window:
                        loc["allowed_as_next"] = window["allowed_as_next"]
                    insert_edit_locations.append(loc)

    if has_confidence:
        confidences = [loc["confidence"] for loc in replace_edit_locations + insert_edit_locations]
        sorted_confidences = sorted(confidences, reverse=True)
        for i, loc in enumerate(replace_edit_locations + insert_edit_locations):
            loc["suggestionRank"] = sorted_confidences.index(loc["confidence"])
    
    return replace_edit_locations, insert_edit_locations

def get_version(snapshot, version):
    assert version in ["parent", "child"]
    version_content = []
    for window in snapshot:
        if isinstance(window, list):
            version_content.extend(window)
        else:
            if version == "parent":
                version_content.extend(window["before"])
            else:
                version_content.extend(window["after"])
        
    return version_content

def add_info_to_snapshots(snapshots):
    for rel_file_path, snapshot in snapshots.items():
        pre_edit_line_idx = 0
        post_edit_line_idx = 0
        parent_version_content = "".join(get_version(snapshot, "parent"))
        for widx, window in enumerate(snapshot):
            if isinstance(window, list):
                pre_edit_line_idx += len(window)
                post_edit_line_idx += len(window)
                continue
            window["parent_version_range"] = {
                "start": pre_edit_line_idx,
                "end": pre_edit_line_idx + len(window["before"]),
            }
            pre_edit_line_idx += len(window["before"])
            window["child_version_range"] = {
                "start": post_edit_line_idx,
                "end": post_edit_line_idx + len(window["after"]),
            }
            post_edit_line_idx += len(window["after"])

            pre_edit_line_idx = window["parent_version_range"]["start"]
            post_edit_line_idx = window["parent_version_range"]["end"]

            line_index = window["parent_version_range"]["start"]
            language = "python" #check_language(rel_file_path)
            # print("[WARNING:SUT] At src/optimization/utils.py, assume language is python")

            if window["before"] == [] and window["after"] != []:
                line_index -= 1

            prev_window = snapshot[widx-1] if widx > 0 else None
            next_window = snapshot[widx+1] if widx < len(snapshot)-1 else None
            
            if prev_window is None:
                window["prefix"] = []
            else:
                window["prefix"] = prev_window[-1 * min(3, len(prev_window)):]
            
            if next_window is None:
                window["suffix"] = []
            else:
                window["suffix"] = next_window[:min(3, len(next_window))]

            structural_path = find_code_structure(parent_version_content, line_index, language)

            window["structural_path"] = structural_path
            window["file_path"] = rel_file_path

    return snapshots

def overlap_percentage(list_a, list_b):
    set_a = set(list_a)
    set_b = set(list_b)
    overlap = set_a & set_b
    avg_len = (len(list_a) + len(list_b)) / 2
    return len(overlap) / avg_len if avg_len > 0 else 0.0

def indexing_edits_within_snapshots(snapshots): # Also used in optimization/utils.py
    """
    Indexing edits within snapshots.
    """
    if snapshots == {}:
        return snapshots
    idx = 0
    for file_path, snapshot in snapshots.items():
        for window in snapshot:
            if isinstance(window, list):
                continue
            if not isinstance(window, dict):
                import json
                with open("debug.json", "w") as f:
                    json.dump(snapshots, f, indent=4)
                raise ValueError("Window is not a dict")
            window["idx"] = idx
            idx += 1
    return snapshots

def get_bleu(pred, gdth):
    if isinstance(pred, list):
        pred = "".join(pred)
    if isinstance(gdth, list):
        gdth = "".join(gdth)

    (goldMap, predictionMap) = direct_computeMaps(pred, gdth)
    bleu_score = bleuFromMaps(goldMap, predictionMap)
    return bleu_score[0]

def deduplicate_edits(edit_list):
    seen = set()
    deduped = []

    for item in edit_list:
        detail = item["detail"]
        key = (
            detail["abs_file_path"],
            item["version"],
            tuple(sorted(detail["position"].items()))  # (start, end)
        )
        # position dict → tuple of tuples
        key = (
            detail["abs_file_path"],
            item["version"],
            (
                detail["position"]["start"]["line"],
                detail["position"]["start"]["column"],
                detail["position"]["end"]["line"],
                detail["position"]["end"]["column"],
            )
        )
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped

def formalize_input(edit1, edit2):
    edit1_str = f"<file_path>{edit1['file_path']}</file_path>\n<structural_path>\n"
    edit2_str = f"<file_path>{edit2['file_path']}</file_path>\n<structural_path>\n"

    def construct_structual_flow(s, edit):
        for idx, structural_path in enumerate(edit['structural_path']):
            indent = "\t"* idx
            s += f"{indent}{structural_path['signature']}\n"
        s += "</structural_path>\n"
        s += "<code>\n"
        return s

    edit1_str = construct_structual_flow(edit1_str, edit1)
    edit2_str = construct_structual_flow(edit2_str, edit2)
    
    edit1_dep_info = []
    edit2_dep_info = []
    for dep_info in edit1["base_dependency_caller"] + edit1["base_dependency_callee"]:
        if dep_info["to_hunk_idx"] == edit2['idx']:
            dep_info["version"] = "base"
            edit1_dep_info.append(dep_info)
    for dep_info in edit1["head_dependency_callee"] + edit1["head_dependency_caller"]:
        if dep_info["to_hunk_idx"] == edit2['idx']:
            dep_info["version"] = "head"
            edit1_dep_info.append(dep_info)
    
    for dep_info in edit2["base_dependency_caller"] + edit2["base_dependency_callee"]:
        if dep_info["to_hunk_idx"] == edit1['idx']:
            dep_info["version"] = "base"
            edit2_dep_info.append(dep_info)
    for dep_info in edit2["head_dependency_callee"] + edit2["head_dependency_caller"]:
        if dep_info["to_hunk_idx"] == edit1['idx']:
            dep_info["version"] = "head"
            edit2_dep_info.append(dep_info)

    def construct_code(s, edit, dep_infos):
        dep_infos = deduplicate_edits(dep_infos)
        codes = []
        # for idx, code in enumerate(edit["prefix"], start=-len(edit["prefix"])):
        for idx, code in enumerate(edit["prefix"][-1:], start=-1):
            codes.append({
                "before_idx": edit["parent_version_range"]["start"] + idx,
                "after_idx": edit["child_version_range"]["start"] + idx,
                "code": code
            })

        for idx, code in enumerate(edit["before"], start = 0):
            codes.append({
                "before_idx": edit["parent_version_range"]["start"] + idx,
                "after_idx": None,
                "code": code
            })
        
        for idx, code in enumerate(edit["after"], start = 0):
            codes.append({
                "before_idx": None,
                "after_idx": edit["child_version_range"]["start"] + idx,
                "code": code
            })

        for idx, code in enumerate(edit["suffix"][:1], start = 0):
            codes.append({
                "before_idx": edit["parent_version_range"]["end"] + idx,
                "after_idx": edit["child_version_range"]["end"] + idx,
                "code": code
            })

        idxs = [len(str(code["before_idx"])) for code in codes if code["before_idx"] is not None]
        idxs.extend([len(str(code["after_idx"])) for code in codes if code["after_idx"] is not None])
        max_len = max(idxs) if idxs else 0

        for code in codes:
            if code["before_idx"] is None:
                code["before_idx"] = " " * max_len
                special_token = "+"
            elif code["after_idx"] is None:
                code["after_idx"] = " " * max_len
                special_token = "-"
            else:
                special_token = " "

            deps_at_this_line = []
            for dep in dep_infos:
                if dep["version"] == "base" and special_token == "-" and dep["detail"]["position"]["start"]["line"] == code["before_idx"]:
                    deps_at_this_line.append([dep["detail"]["position"]["start"]["column"], dep["detail"]["position"]["end"]["column"]])
                elif dep["version"] == "head" and special_token == "+" and dep["detail"]["position"]["start"]["line"] == code["after_idx"]:
                    deps_at_this_line.append([dep["detail"]["position"]["start"]["column"], dep["detail"]["position"]["end"]["column"]])
            # sort by start column
            deps_at_this_line.sort(key=lambda x: x[0])
            for idx, dep in enumerate(deps_at_this_line):
                # add offset to column
                dep[0] += 11 * idx
                dep[1] += 11 * idx
                # insert </dep> at dep[1] for code["code"]
                code["code"] = code["code"][:dep[1]] + "</dep>" + code["code"][dep[1]:]
                # insert <dep> at dep[0] for code["code"]
                code["code"] = code["code"][:dep[0]] + "<dep>" + code["code"][dep[0]:]

            s += f"{code['before_idx']:>{max_len}} {code['after_idx']:>{max_len}} {special_token}   {code['code']}"

        s += "</code>\n"
        return s
        
    edit1_str = construct_code(edit1_str, edit1, edit1_dep_info)
    edit2_str = construct_code(edit2_str, edit2, edit2_dep_info)

    return edit1_str, edit2_str 

def chatgpt(prompt, model="claude-sonnet-4-20250514", temperature=0.0, n=1, top_p=1, stop=None, max_tokens=4096, 
                  presence_penalty=0, frequency_penalty=0, logit_bias={}, timeout=120):
    """
    Query chatgpt for response
    """
    messages = [{"role": "user", "content": prompt}]
    payload = {
        "messages": messages,
        "model": model,
        "temperature": temperature,
        "n": n, # the number of different completions
        "top_p": top_p,
        "stop": stop,
        "max_tokens": max_tokens,
        "presence_penalty": presence_penalty,
        "frequency_penalty": frequency_penalty,
        "logit_bias": logit_bias
    }
    retries = 0
    while True:
        try:
            r = requests.post(f'{OPENAI_BASE_URL}/chat/completions',
                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json = payload,
                timeout=timeout
            )
            if r.status_code != 200:
                print(f"Status code: {r.status_code}, retry")
                retries += 1
                time.sleep(1)
            else:
                break
        except requests.exceptions.ReadTimeout:
            print("ReadTimeout, retry")
            time.sleep(1)
            retries += 1
        except requests.exceptions.ConnectionError:
            print("ConnectionError, retry")
            time.sleep(1)
            retries += 1
    r = r.json()
    # NOTE: this return type should not be changed, as this func is used for multiple purposes.
    return [choice['message']['content'] for choice in r['choices']]