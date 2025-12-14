import os
import platform

from .utils import parse

def traverse_python_tree(node, results):
    def process_args(param, args):
        if param.type == 'identifier': # a
            args.append(param.text.decode('utf-8').strip())
        elif param.type == 'typed_parameter' or param.type == 'typed_default_parameter': # a: int or a: int = 1
            args.append(param.text.decode('utf-8').split(':')[0].strip())
        elif param.type == 'default_parameter': # a = 1
            args.append(param.child_by_field_name('name').text.decode('utf-8').strip())
        elif param.type == "list_splat_pattern": # *args
            args.append(param.text.decode('utf-8').strip())
        elif param.type == "dictionary_splat_pattern": # **kwargs
            args.append(param.text.decode('utf-8').strip())
            
    if node.type == 'function_definition':
        function_name = node.child_by_field_name('name').text.decode('utf-8')
        # get function name range
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        if function_name == '__init__': # args of __init__ belongs to class definition name
            return results
        parameters = node.child_by_field_name('parameters')
        args = []
        for param in parameters.children:
            process_args(param, args)
        results.append({"type": "def", "name": function_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'class_definition':
        class_name = node.child_by_field_name('name').text.decode('utf-8')
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        init_method = None
        for child in node.children:
            if child.type == 'block':
                for sub_child in child.children:
                    if sub_child.type == 'function_definition':
                        function_name = sub_child.child_by_field_name('name').text.decode('utf-8')
                        if function_name == '__init__':
                            init_method = sub_child
                            break
        if init_method:
            parameters = init_method.child_by_field_name('parameters')
            args = []
            for param in parameters.children:
                process_args(param, args)
        else:
            args = []
        results.append({"type": "def", "name": class_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'call':
        function_name = node.child_by_field_name('function').text.decode('utf-8')
        range_start = node.child_by_field_name('function').start_point
        range_end = node.child_by_field_name('function').end_point
        arguments = node.child_by_field_name('arguments')
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": function_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    return results

def traverse_java_tree(node, results):
    def process_args(param, args):
        if param.type == 'formal_parameter':  # int a
            args.append(param.child_by_field_name('name').text.decode('utf-8').strip())
        elif param.type == 'spread_parameter': # int... a
            for child in param.children:
                if child.type == 'variable_declarator':
                    args.append(child.text.decode('utf-8').strip())
    
    if node.type == 'method_declaration' or node.type == 'constructor_declaration':
        method_name = node.child_by_field_name('name').text.decode('utf-8')
        parameters = node.child_by_field_name('parameters')
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        args = []
        for param in parameters.children:
            process_args(param, args)
        results.append({"type": "def", "name": method_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'method_invocation':
        method_name = node.child_by_field_name('name').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": method_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'object_creation_expression':
        class_name = node.child_by_field_name('type').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        range_start = node.child_by_field_name('type').start_point
        range_end = node.child_by_field_name('type').end_point
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": class_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    return results

def traverse_go_tree(node, results):
    def process_params(param, params):
        # Parameters in Go can be simple variables or typed variables
        if param.type == 'parameter_declaration':
            for child in param.children:
                if child.type == 'identifier':
                    params.append(child.text.decode('utf-8').strip())

    # Handle struct instantiation
    if node.type == 'composite_literal':
        type_node = node.child_by_field_name('type')
        body_node = node.child_by_field_name('body')
        if type_node and type_node.type == 'type_identifier':
            # Assume all type identifiers starting with an uppercase letter are structs (Go language naming convention)
            if type_node.text.decode('utf-8').strip()[0].isupper():
                struct_name = type_node.text.decode('utf-8').strip()
                range_start = type_node.start_point
                range_end = type_node.end_point
                args = []
                for param in body_node.children:
                    if param.type not in ["{", "}", ","]:
                        args.append(param.text.decode('utf-8').strip())
                results.append({"type": "ref", "name": struct_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
                
    if node.type == 'function_declaration' or node.type == 'method_declaration':
        func_name = node.child_by_field_name('name').text.decode('utf-8').strip()
        parameters = node.child_by_field_name('parameters')
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        params = []
        for param in parameters.children:
            process_params(param, params)
        results.append({"type": "def", "name": func_name, "args": params, "name_range_start": range_start, "name_range_end": range_end})
    
    elif node.type == 'call_expression':
        # Call expressions can be function calls or method calls
        func_name = node.child_by_field_name('function').text.decode('utf-8').strip()
        arguments = node.child_by_field_name('arguments')
        range_start = node.child_by_field_name('function').start_point
        range_end = node.child_by_field_name('function').end_point
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": func_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})

    elif node.type == 'type_spec':
        class_name = node.child_by_field_name('name').text.decode('utf-8').strip()
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        args = []
        try:
            if node.child_by_field_name('type').children[0].type == "struct":
                for param in node.child_by_field_name('type').children[1].children:
                    if param.type == "field_declaration":
                        param_name = param.child_by_field_name('name').text.decode('utf-8').strip()
                        args.append(param_name)
                results.append({"type": "def", "name": class_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
        except:
            return results
            
    return results

def traverse_javascript_tree(node, results):
    def process_params(params):
        args = []
        for param in params.children:
            if param.type == 'identifier':  # simple parameter: a
                args.append(param.text.decode('utf-8').strip())
            elif param.type == 'assignment_pattern':  # default parameter: a = 1
                args.append(param.child_by_field_name('left').text.decode('utf-8').strip())
            elif param.type == 'array_pattern':  # array pattern: [a, b]
                args.append(param.text.decode('utf-8').strip())
            elif param.type == 'object_pattern':  # object pattern: {a, b}
                args.append(param.text.decode('utf-8').strip())
            elif param.type == 'rest_pattern':  # rest pattern: ...args
                args.append(param.text.decode('utf-8').split("...")[1].strip())
            
        return args

    if node.type in ['function_declaration', 'function_expression', 'arrow_function']:
        if node.child_by_field_name('name'):
            func_name = node.child_by_field_name('name').text.decode('utf-8')
            range_start = node.child_by_field_name('name').start_point
            range_end = node.child_by_field_name('name').end_point
        elif node.parent.child_by_field_name('name'):
            # find where the function is given a name
            func_name = node.parent.child_by_field_name('name').text.decode('utf-8')
            range_start = node.parent.child_by_field_name('name').start_point
            range_end = node.parent.child_by_field_name('name').end_point
        else:
            func_name = "<anonymous>"
            range_start = node.start_point
            range_end = node.end_point
        try:
            params = node.child_by_field_name('parameters')
            args = process_params(params)
        except:
            return results
        results.append({"type": "def", "name": func_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'call_expression':
        func_name = node.child_by_field_name('function').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        range_start = node.child_by_field_name('function').start_point
        range_end = node.child_by_field_name('function').end_point
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": func_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'new_expression':
        class_name = node.child_by_field_name('constructor').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        args = []
        range_start = node.child_by_field_name('constructor').start_point
        range_end = node.child_by_field_name('constructor').end_point
        if arguments:
            for arg in arguments.children:
                if arg.type not in ["(", ")", ","]:
                    args.append(arg.text.decode('utf-8').strip())
            results.append({"type": "ref", "name": class_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'class_declaration':
        class_name = node.child_by_field_name('name').text.decode('utf-8')
        constructor_args = []
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        # find constructor and extract arguments 
        for child in node.children:
            if child.type == 'class_body':
                if child.child_by_field_name('member') is None:
                    continue
                constructor_params = child.child_by_field_name('member').child_by_field_name('parameters')
                if constructor_params:
                    constructor_args = process_params(constructor_params)
                    break
                else:
                    return results
        results.append({"type": "def", "name": class_name, "args": constructor_args, "name_range_start": range_start, "name_range_end": range_end})
    return results

def traverse_typescript_tree(node, results):
    def process_params(params):
        args = []
        for param in params.children:
            if param.type in ["required_parameter", 'optional_parameter']:
                args.append(param.child_by_field_name('pattern').text.decode('utf-8').strip())
            
        return args

    if node.type in ['function_declaration', 'function_expression', 'arrow_function']:
        if node.child_by_field_name('name'):
            func_name = node.child_by_field_name('name').text.decode('utf-8')
            range_start = node.child_by_field_name('name').start_point
            range_end = node.child_by_field_name('name').end_point
        elif node.parent.child_by_field_name('name'):
            # find where the function is given a name
            func_name = node.parent.child_by_field_name('name').text.decode('utf-8')
            range_start = node.parent.child_by_field_name('name').start_point
            range_end = node.parent.child_by_field_name('name').end_point
        else:
            func_name = "<anonymous>"
            range_start = node.start_point
            range_end = node.end_point
        try:
            params = node.child_by_field_name('parameters')
            args = process_params(params)
        except:
            return results
        results.append({"type": "def", "name": func_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'call_expression':
        func_name = node.child_by_field_name('function').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        range_start = node.child_by_field_name('function').start_point
        range_end = node.child_by_field_name('function').end_point
        args = []
        for arg in arguments.children:
            if arg.type not in ["(", ")", ","]:
                args.append(arg.text.decode('utf-8').strip())
        results.append({"type": "ref", "name": func_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'new_expression':
        class_name = node.child_by_field_name('constructor').text.decode('utf-8')
        arguments = node.child_by_field_name('arguments')
        args = []
        range_start = node.child_by_field_name('constructor').start_point
        range_end = node.child_by_field_name('constructor').end_point
        if arguments:
            for arg in arguments.children:
                if arg.type not in ["(", ")", ","]:
                    args.append(arg.text.decode('utf-8').strip())
        else:
            return results
        results.append({"type": "ref", "name": class_name, "args": args, "name_range_start": range_start, "name_range_end": range_end})
    elif node.type == 'class_declaration':
        class_name = node.child_by_field_name('name').text.decode('utf-8')
        constructor_args = []
        range_start = node.child_by_field_name('name').start_point
        range_end = node.child_by_field_name('name').end_point
        # find constructor and extract arguments
        for child in node.children:
            if child.type == 'class_body':
                members = child.children
                for member in members:
                    if member.type == 'method_definition' and member.child_by_field_name('name').text.decode('utf-8') == 'constructor':
                        constructor_params = member.child_by_field_name('parameters')
                        constructor_args = process_params(constructor_params)
                        break
        results.append({"type": "def", "name": class_name, "args": constructor_args, "name_range_start": range_start, "name_range_end": range_end})
    return results

def traverse_tree(node, results, lang):
    if lang == "python":
        results = traverse_python_tree(node, results)
    elif lang == "go":
        results = traverse_go_tree(node, results)
    elif lang == "java":
        results = traverse_java_tree(node, results)
    elif lang == "javascript":
        results = traverse_javascript_tree(node, results)
    elif lang == "typescript":
        results = traverse_typescript_tree(node, results)
    for child in node.children:
        traverse_tree(child, results, lang)
        
def parse_args(code: str, lang):
    tree = parse(code, lang)
    root_node = tree.root_node

    results = []
    traverse_tree(root_node, results, lang)
    return results

def lcs(a, b):
    # Get the lengths of the two sequences
    m, n = len(a), len(b)
    
    # Create a 2D array to store the LCS length
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    # Fill the DP table
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1]['type'] == b[j - 1]['type'] and a[i - 1]['name'] == b[j - 1]['name'] and len(a[i - 1]['args']) != len(b[j - 1]['args']):
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack from the DP table to find the LCS
    result = []
    i, j = m, n
    while i > 0 and j > 0:
        if a[i - 1]['type'] == b[j - 1]['type'] and a[i - 1]['name'] == b[j - 1]['name'] and len(a[i - 1]['args']) != len(b[j - 1]['args']):
            result.insert(0, (a[i - 1], b[j - 1]))  # Insert the matching element pair at the beginning of the result list
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1

    return result

def args_diff(before_arg_cases, after_arg_cases):
    """
    Func:
        Given the arguments before edit and arguments after edit, determine whether the edit is a definition change or a reference change
    Args:
        before_arg_cases: list, a list of argument cases before edit
        after_arg_cases: list, a list of argument cases after edit
    Returns:
        result: dict | None, the change info
    """
    # find matches of same type, same name but different number of arguments
    match = lcs(before_arg_cases, after_arg_cases)
    if len(match) == 0:
        return None
    else:
        case = match[0]
        change_info = {}
        change_info["type"] = case[1]["type"]
        change_info["name"] = case[1]["name"]
        change_info["name_range_start"] = case[1]["name_range_start"] # here we use the after case's name range
        change_info["name_range_end"] = case[1]["name_range_end"]
        change_info["before_args"] = case[0]["args"]
        change_info["after_args"] = case[1]["args"]
        change_info["before_args_num"] = len(case[0]["args"])
        change_info["after_args_num"] = len(case[1]["args"])
        return change_info
    
def is_defref_edit(code_before: str, code_after: str, lang: str):
    args_before = parse_args(code_before, lang)
    args_after = parse_args(code_after, lang)

    diff_result = args_diff(args_before, args_after)
    if diff_result is not None:
        return diff_result
    else:
        return False
    
if __name__ == "__main__":
    code_before = "        color={rgbaToHex(color)}"
    code_after = "        color={rgbaToHex(color, true)}"
    lang = "typescript"
    print(is_defref_edit(code_before, code_after, lang))