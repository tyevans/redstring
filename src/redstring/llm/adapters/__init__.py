"""`LlmProvider` adapters.

**The only place in the library where a `langchain*` import is permitted.**
`tests/unit/test_dependencies_stay_confined.py` enforces that by walking the
source tree, because the rule is one import away from being broken by
someone who did not read the port's docstring.
"""
