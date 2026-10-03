"""ServiceNow adapter — Table API v2 change_request integration."""
from .client import ServiceNowClient
from .protocol import ChangeRecord, ChangeRecordInput

__all__ = ["ServiceNowClient", "ChangeRecord", "ChangeRecordInput", "get_servicenow_adapter"]


def get_servicenow_adapter(cfg: dict) -> ServiceNowClient:
    """Factory: build a ``ServiceNowClient`` from a config dict.

    Expects ``cfg`` to be the ``servicenow:`` block from ``agentteam.yaml``::

        servicenow:
          instance_url: https://mycompany.service-now.com
          field_map: {...}
    """
    return ServiceNowClient.from_config(cfg)
