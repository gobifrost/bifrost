"""FastAPI router package.

Router modules remain independently importable through Python's normal package
semantics. The full API assembles its explicit router aliases in ``src.main``;
keeping this initializer inert prevents focused consumers, such as the worker
SDK app, from importing the complete API router graph.
"""
