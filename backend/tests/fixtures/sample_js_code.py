"""Sample JavaScript source fixture for static analysis testing.

NOTE: NEVER EXECUTE THIS CODE DIRECTLY. FOR STATIC ANALYSIS PARSER TESTING ONLY.
"""

SAMPLE_JS_SOURCE = """// Sample JavaScript module for static analysis testing
function processInput(input) {
    // Line 4: Known ESLint no-eval violation
    const result = eval(input);
    return result;
}

function unchangedFunction(input) {
    // Line 10: Unchanged eval violation
    return eval(input);
}
"""
