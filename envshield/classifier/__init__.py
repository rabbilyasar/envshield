# envshield/classifier/__init__.py
"""
Context classifier for false-positive reduction in secret scanning.

This module provides the tokenizer and context classifier used to distinguish
code patterns (function keyword arguments, type annotations, destructuring)
from actual secret values, reducing false positives in the Generic API Key
detector.

See Milestone 2 (tokenizer) and Milestone 3 (classifier) documentation for
design rationale and limitations.
"""
