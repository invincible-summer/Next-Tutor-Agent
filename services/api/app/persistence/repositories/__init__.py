"""Repository layer: domain-visible records + protocols + implementations.

Domain services import from here (records & protocols only). The SQLAlchemy
implementation classes stay importable but are wiring concerns of the
composition root (enterprise mode startup), never of domain modules.
"""
from __future__ import annotations
