"""Sample Python source fixture for static analysis testing.

NOTE: NEVER EXECUTE THIS CODE DIRECTLY. FOR STATIC ANALYSIS PARSER TESTING ONLY.
"""

SAMPLE_PYTHON_SOURCE = """# Sample Python module for testing
import sys

def calculate_sum(a, b):
    # Safe helper function
    return a + b

def vulnerable_function(user_input):
    # Line 10: Known Bandit B307 issue (eval)
    result = eval(user_input)
    # Line 12: Known Ruff F821 undefined name
    print(unknown_variable_name)
    return result

def unchanged_vulnerable_function(payload):
    # Line 17: Unchanged line with eval issue
    return eval(payload)
"""
