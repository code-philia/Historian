def reverse_string(text: str) -> str:
    """
    Reverse the given string.
    
    Args:
        text: The string to be reversed.
    
    Returns:
        The reversed string.
    """
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    return text[::-1]

undefined_var = some_var 