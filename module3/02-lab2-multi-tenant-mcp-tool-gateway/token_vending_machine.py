# SPDX-License-Identifier: MIT-0
"""
TokenVendingMachine — Tenant-Scoped AWS Credential Manager
Module 3 (reuses the same pattern from Module 1, Lab 2)

Provides short-term, tenant-scoped AWS credentials via a two-step credential model:

    1. Lambda execution role holds only sts:AssumeRole + sts:TagSession
       on TenantIsolationRole.
    2. TokenVendingMachine calls sts:AssumeRole with a tenantId session tag,
       receives 15-minute credentials, and returns a boto3.Session scoped
       to that tenant's data by IAM conditions on the assumed role.

Credentials are cached per tenant in a TTLCache (TTL < 900 s) to avoid
redundant STS calls across warm Lambda invocations.

Any failure raises IsolationError immediately. There is NO ambient-credential fallback.

Usage:
    from token_vending_machine import token_vending_machine, IsolationError

    session = token_vending_machine.get_session({"tenantId": "tenant-001"})
    client  = session.client("dynamodb")
"""

import os
import boto3
from typing import Any, Dict

from cachetools import TTLCache

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_TTL = 800   # seconds — must be < 900 (STS credential lifetime)
_CACHE_MAXSIZE     = 128   # covers typical Lambda concurrency


# ---------------------------------------------------------------------------
# IsolationError
# ---------------------------------------------------------------------------

class IsolationError(Exception):
    """
    Raised on any STS or configuration failure.
    Never falls back to ambient Lambda execution-role credentials.
    """


# ---------------------------------------------------------------------------
# TokenVendingMachine
# ---------------------------------------------------------------------------

class TokenVendingMachine:
    """
    Obtains and caches tenant-scoped boto3 sessions via STS AssumeRole.

    Instantiate once at module load time so the TTLCache persists across
    warm Lambda invocations.
    """

    def __init__(self, role_arn: str = None, region_name: str = None) -> None:
        """
        Initialize with role ARN (from arg or TENANT_ISOLATION_ROLE_ARN env var).

        Raises IsolationError if role_arn is missing or empty.
        """
        self.role_arn = role_arn or os.environ.get('TENANT_ISOLATION_ROLE_ARN', '')
        if not self.role_arn:
            raise IsolationError(
                "role_arn must be provided or TENANT_ISOLATION_ROLE_ARN env var must be set."
            )
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")

        ttl = int(os.environ.get('ISOLATION_CACHE_TTL_SECONDS', _DEFAULT_CACHE_TTL))
        if ttl >= 900:
            raise IsolationError(
                f"Cache TTL must be < 900 (got {ttl}). Must be less than STS credential lifetime."
            )

        self._cache: TTLCache = TTLCache(maxsize=_CACHE_MAXSIZE, ttl=ttl)
        self._sts_client = boto3.client('sts')

    def _assume_role(self, tenant_id: str) -> Dict:
        """Call sts:AssumeRole with a tenantId session tag."""
        try:
            response = self._sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName=f"tenant-{tenant_id}",
                Tags=[{"Key": "tenantId", "Value": tenant_id}],
                DurationSeconds=900,
            )
            return response["Credentials"]
        except Exception as exc:
            raise IsolationError(
                f"STS AssumeRole failed for tenant '{tenant_id}': {exc}"
            ) from exc

    def get_session(self, tenant_context: Dict) -> boto3.Session:
        """
        Return a boto3.Session scoped to the tenant via STS AssumeRole.

        Checks the TTLCache first; calls _assume_role on a cache miss.

        Args:
            tenant_context: Dict containing at least 'tenantId'.

        Raises:
            IsolationError: if tenantId is absent/empty, or STS fails.
        """
        tenant_id = tenant_context.get("tenantId")
        if not tenant_id:
            raise IsolationError("tenant_context must contain a non-empty 'tenantId'.")

        if tenant_id not in self._cache:
            self._cache[tenant_id] = self._assume_role(tenant_id)

        creds = self._cache[tenant_id]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=self.region_name,
        )

    def get_client(self, service_name: str, tenant_context: Dict) -> Any:
        """Return boto3.client(service_name) from the tenant-scoped session."""
        return self.get_session(tenant_context).client(service_name)

    def get_resource(self, service_name: str, tenant_context: Dict) -> Any:
        """Return boto3.resource(service_name) from the tenant-scoped session."""
        return self.get_session(tenant_context).resource(service_name)


# ---------------------------------------------------------------------------
# Module-level singleton (uses env var for role ARN)
# ---------------------------------------------------------------------------
try:
    token_vending_machine = TokenVendingMachine()
except IsolationError:
    # Allow import without env var set (e.g., in agent.py Phase 1 where TVM isn't used)
    token_vending_machine = None
