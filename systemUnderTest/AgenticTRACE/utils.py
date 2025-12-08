import os
import platform
from tree_sitter import Language, Parser

def get_parser(language): 
    assert language in ["python", "go", "java", "javascript", "typescript"], "Currently only python, go, java, javascript, and typescript are supported"
    system = platform.system().lower()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tree_sitter_dir = os.path.normpath(os.path.join(base_dir, "../../libs/tree-sitter"))
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


def get_renameable_elements(code, language):
    """
    Parse code and extract all renameable elements (identifiers) with their positions.

    Args:
        code (str): Source code to parse
        language (str): Programming language ('python', 'go', 'java', 'javascript', 'typescript')

    Returns:
        list: List of dictionaries, each containing:
            - name (str): The identifier name
            - type (str): Element type (e.g., 'function', 'variable', 'class', 'parameter')
            - line (int): Line number (0-indexed)
            - column (int): Column number (0-indexed)
            - start_byte (int): Start position in bytes
            - end_byte (int): End position in bytes
    """
    parser = get_parser(language)
    tree = parser.parse(bytes(code, 'utf8'))

    # Define which node types are renameable for each language
    renameable_node_types = {
        'python': {
            'identifier',
            'function_definition',
            'class_definition',
            'parameter',
            'keyword_argument',
            'attribute',
        },
        'go': {
            'identifier',
            'function_declaration',
            'method_declaration',
            'type_identifier',
            'field_identifier',
            'package_identifier',
        },
        'java': {
            'identifier',
            'method_declaration',
            'class_declaration',
            'interface_declaration',
            'variable_declarator',
            'formal_parameter',
        },
        'javascript': {
            'identifier',
            'function_declaration',
            'class_declaration',
            'variable_declarator',
            'formal_parameters',
            'property_identifier',
        },
        'typescript': {
            'identifier',
            'function_declaration',
            'class_declaration',
            'interface_declaration',
            'type_identifier',
            'property_identifier',
            'variable_declarator',
        }
    }

    elements = []
    visited_positions = set()  # Track (line, column) to avoid duplicates

    def traverse(node, parent_type=None):
        """Recursively traverse the AST and collect renameable elements"""
        node_type = node.type

        # Determine the element type based on context
        element_type = None
        name_node = None

        if language == 'python':
            if node_type == 'function_definition':
                element_type = 'function'
                name_node = node.child_by_field_name('name')
            elif node_type == 'class_definition':
                element_type = 'class'
                name_node = node.child_by_field_name('name')
            elif node_type == 'parameter':
                element_type = 'parameter'
                name_node = node.child_by_field_name('name')
            elif node_type == 'identifier':
                # Only add if not already captured by a parent node
                element_type = 'identifier'
                name_node = node
            elif node_type == 'attribute':
                element_type = 'attribute'
                name_node = node.child_by_field_name('attribute')

        elif language == 'go':
            if node_type in ['function_declaration', 'method_declaration']:
                element_type = 'function'
                name_node = node.child_by_field_name('name')
            elif node_type in ['type_identifier', 'field_identifier', 'package_identifier']:
                element_type = node_type.replace('_identifier', '')
                name_node = node
            elif node_type == 'identifier':
                element_type = 'identifier'
                name_node = node

        elif language == 'java':
            if node_type == 'method_declaration':
                element_type = 'method'
                name_node = node.child_by_field_name('name')
            elif node_type == 'class_declaration':
                element_type = 'class'
                name_node = node.child_by_field_name('name')
            elif node_type == 'interface_declaration':
                element_type = 'interface'
                name_node = node.child_by_field_name('name')
            elif node_type == 'variable_declarator':
                element_type = 'variable'
                name_node = node.child_by_field_name('name')
            elif node_type == 'identifier':
                element_type = 'identifier'
                name_node = node

        elif language in ['javascript', 'typescript']:
            if node_type == 'function_declaration':
                element_type = 'function'
                name_node = node.child_by_field_name('name')
            elif node_type == 'class_declaration':
                element_type = 'class'
                name_node = node.child_by_field_name('name')
            elif node_type == 'variable_declarator':
                element_type = 'variable'
                name_node = node.child_by_field_name('name')
            elif node_type == 'property_identifier':
                element_type = 'property'
                name_node = node
            elif node_type == 'identifier':
                element_type = 'identifier'
                name_node = node

            if language == 'typescript' and node_type in ['interface_declaration', 'type_identifier']:
                element_type = 'type' if node_type == 'type_identifier' else 'interface'
                name_node = node.child_by_field_name('name') if node_type == 'interface_declaration' else node

        # If we found a renameable element, add it to the list
        if name_node and element_type:
            line = name_node.start_point[0]       # 0-indexed
            column = name_node.start_point[1]     # 0-indexed
            position_key = (line, column)

            # Only add if we haven't seen this exact position before
            if position_key not in visited_positions:
                visited_positions.add(position_key)
                name = code[name_node.start_byte:name_node.end_byte]

                elements.append({
                    'name': name,
                    'type': element_type,
                    'line': line,
                    'column': column,
                    'start_byte': name_node.start_byte,
                    'end_byte': name_node.end_byte
                })

        # Recursively traverse children
        for child in node.children:
            traverse(child, node_type)

    traverse(tree.root_node)
    return elements


def construct_agent_input(json_input: dict) -> str:
    """
    Construct a detailed input string for the agent based on the provided JSON input.

    Args:
        json_input (dict): Input data sent from simulation framework.
    Returns:
        agent_input (str): Constructed input string for the agent.
    """
    agent_input = f"Project directory: {json_input['repo_dir']}\n"
    agent_input += f"Edit history: \n"
    for edit in json_input["prior_edits"]:
        idx = edit["idx"]
        prefix = edit["prefix"]
        before = edit["before"]
        after = edit["after"]
        suffix = edit["suffix"]
        file_path = edit["file_path"]
        agent_input += f"Edit {idx} in file {file_path}:\n"
        for line in prefix:
            agent_input += f"  {line}"
        for line in before:
            agent_input += f"- {line}"
        for line in after:
            agent_input += f"+ {line}"
        for line in suffix:
            agent_input += f"  {line}"
        agent_input += "\n"
        
    return agent_input