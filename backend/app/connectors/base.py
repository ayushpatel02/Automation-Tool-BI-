"""Connector abstraction and factory.

A ``DataConnector`` knows how to (a) test a connection and (b) expose a SQLAlchemy engine
or an in-memory representation the profiler can introspect. The factory maps a
``ConnectorType`` to a concrete implementation so adding a source is a registration, not a
rewrite.
"""

from __future__ import annotations

import abc

from app.schemas.connector import ConnectionTestResult, ConnectorConfig, ConnectorType


class DataConnector(abc.ABC):
    """Base class for all data connectors."""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abc.abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Attempt a lightweight connection and return the result."""

    def sqlalchemy_url(self) -> str | None:
        """Return a SQLAlchemy URL for engine-backed connectors, else None."""
        return None


_REGISTRY: dict[ConnectorType, type[DataConnector]] = {}


def register(*types: ConnectorType):
    def deco(cls: type[DataConnector]) -> type[DataConnector]:
        for t in types:
            _REGISTRY[t] = cls
        return cls

    return deco


def get_connector(config: ConnectorConfig) -> DataConnector:
    """Instantiate the connector implementation for the given config."""
    # Import implementations so their @register decorators run.
    from app.connectors import files, sql, warehouses  # noqa: F401

    cls = _REGISTRY.get(config.type)
    if cls is None:
        raise ValueError(f"No connector registered for type: {config.type}")
    return cls(config)
