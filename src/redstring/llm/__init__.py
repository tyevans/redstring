"""LLM transport: the `LlmProvider` adapters and the concerns that surround them.

Sibling of `redstring.graph` and `redstring.vector` in the layered contract,
and held apart from `redstring.extraction` by it: extraction depends on
`redstring.ports.llm_provider`, never on anything in here. That is what keeps
a LangChain breaking change confined to `adapters/langchain.py`.

Retry, rate limiting and circuit breaking live here rather than in extraction
because they are properties of *calling a model over a network*, not of
turning prose into entities. Extraction would have to grow the same three
concerns for any other transport, which is the sign they were in the wrong
place.
"""
