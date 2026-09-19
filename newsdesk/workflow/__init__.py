"""Data-defined, versioned workflows (workflow-module architecture).

Module 1 lives in ``schema.py`` (the descriptor language and its
deterministic validator); module 3 in ``engine.py`` (the RunContext
contract and the interpreter — wayfinder ticket 02); module 4 in
``catalog.py`` (the versioned catalog, seed v2 + CLI at W2 — tickets
04/06).

Import-order constraint: ``morning.registries`` imports ``workflow.schema``
to validate registration metadata, and ``engine`` imports the morning
modules — so this ``__init__`` must stay import-light (docstring only),
and ``catalog.py`` imports ``schema`` only, never the engine.
"""
