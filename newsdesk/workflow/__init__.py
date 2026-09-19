"""Data-defined, versioned workflows (workflow-module architecture).

Module 1 lives in ``schema.py`` (the descriptor language and its
deterministic validator); module 3 in ``engine.py`` (the RunContext
contract and the interpreter — wayfinder ticket 02). The catalog
(module 4) joins this package at W2; until then the schema and the
engine are the seams.
"""
