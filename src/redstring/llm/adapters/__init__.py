"""`LlmProvider` adapters.

**The only place in the library where a `langchain*` import is permitted.**
`tests/unit/llm/test_port_does_not_leak.py` enforces that by walking the
source tree, because the rule is one import away from being broken by
someone who did not read the port's docstring.
"""
