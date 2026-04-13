"""Connector registry for concrete read-side imports."""
from __future__ import annotations

from extensions.connectors import ConnectorRegistry

from connectors.csv_connector import CSVConnector


CONNECTOR_REGISTRY = ConnectorRegistry()
CONNECTOR_REGISTRY.register(CSVConnector())
