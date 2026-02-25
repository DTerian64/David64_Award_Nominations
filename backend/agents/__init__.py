"""
agents/__init__.py
───────────────────
Public interface of the agents package.
main.py only needs this import:

    from agents import AskAgent, AskResult
"""

from .ask_agent import AskAgent, AskResult

__all__ = ["AskAgent", "AskResult"]