"""Accounting-software connectors as plugins."""

from app.connectors.base import ConfigField, Connector, PushItem, PushOutcome, PushResult
from app.connectors.service import ConnectorRegistry, auto_push_job, push_documents

__all__ = [
    "ConfigField",
    "Connector",
    "ConnectorRegistry",
    "PushItem",
    "PushOutcome",
    "PushResult",
    "auto_push_job",
    "push_documents",
]
