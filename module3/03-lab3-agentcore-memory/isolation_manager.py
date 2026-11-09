# SPDX-License-Identifier: MIT-0
"""
isolation_manager.py — TVM credential manager for Module 3, Lab 1

Provides tenant-scoped boto3 sessions via STS AssumeRole + TenantID session tag.
Credentials are cached per tenant (TTL = 12 min) to avoid redundant STS calls.

Usage:
    mgr     = IsolationManager(role_arn="arn:aws:iam::123456789012:role/MyTvmRole")
    session = mgr.get_session(tenant_context)
    client  = mgr.get_client("bedrock-agentcore", tenant_context)
"""

import os
import boto3
from typing import Any, Dict

from cachetools import TTLCache

_CACHE_TTL_SECONDS = 720   # 12 min — 3 min buffer before 15 min STS expiry
_CACHE_MAXSIZE     = 128


class IsolationError(Exception):
    """Raised on any STS or configuration failure. No ambient-credential fallback."""


class IsolationManager:
    """Obtains and caches tenant-scoped boto3 sessions via STS AssumeRole."""

    def __init__(self, role_arn: str, region_name: str = None) -> None:
        if not role_arn:
            raise IsolationError("role_arn must be a non-empty string.")
        self.role_arn    = role_arn
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._sts        = boto3.client("sts")
        self._cache: TTLCache = TTLCache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL_SECONDS)

    def get_session(self, tenant_context: Dict) -> boto3.Session:
        """
        Return a boto3.Session scoped to the tenant via STS AssumeRole.

        The TenantID session tag activates IAM ABAC conditions on the TVM role.
        Credentials are cached per tenantId for 12 minutes.

        Raises IsolationError if tenantId is missing or STS fails.
        """
        tenant_id = tenant_context.get("tenantId")
        if not tenant_id:
            raise IsolationError("tenant_context must contain a non-empty 'tenantId'.")

        if tenant_id not in self._cache:
            try:
                response = self._sts.assume_role(
                    RoleArn=self.role_arn,
                    RoleSessionName=f"tenant-{tenant_id}",
                    Tags=[{"Key": "TenantID", "Value": tenant_id}],
                    DurationSeconds=900,
                )
                self._cache[tenant_id] = response["Credentials"]
            except Exception as exc:
                raise IsolationError(
                    f"STS AssumeRole failed for tenant '{tenant_id}': {exc}"
                ) from exc

        creds = self._cache[tenant_id]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )

    def get_client(self, service_name: str, tenant_context: Dict) -> Any:
        """Return boto3.client(service_name) from the tenant-scoped session."""
        return self.get_session(tenant_context).client(service_name, region_name=self.region_name)

    def get_resource(self, service_name: str, tenant_context: Dict) -> Any:
        """Return boto3.resource(service_name) from the tenant-scoped session."""
        return self.get_session(tenant_context).resource(service_name, region_name=self.region_name)
