# SPDX-License-Identifier: MIT-0
"""
TokenVendingMachine — Tenant-Scoped AWS Credential Manager
Module 1, Tenant Isolation Lab 2

Provides short-term, tenant-scoped AWS credentials for any IAM-isolatable
service (DynamoDB, S3, Aurora, OpenSearch) via a two-step credential model:

    1. Lambda execution role holds only sts:AssumeRole + sts:TagSession
       on TenantIsolationRole.
    2. TokenVendingMachine calls sts:AssumeRole with a tenantId session tag,
       receives 15-minute credentials, and returns a boto3.Session scoped
       to that tenant's data by IAM conditions on the assumed role.

Credentials are cached per tenant in a TTLCache (TTL < 900 s) to avoid
redundant STS calls across warm Lambda invocations.

Any failure — missing config, missing tenant ID, STS error — raises
IsolationError immediately. There is NO ambient-credential fallback.

Usage (module-level singleton pattern):
    from token_vending_machine import token_vending_machine, IsolationError

    # In TenantRepository.__init__:
    self.dynamodb = token_vending_machine.get_resource('dynamodb', tenant_context)

    # In TenantS3Client.__init__:
    self.s3_client = token_vending_machine.get_client('s3', tenant_context)

    # In TenantConnectionManager / TenantOpenSearchClient:
    session = token_vending_machine.get_session(tenant_context)
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
    Raised on any STS or configuration failure in IsolationManager.

    Never falls back to ambient Lambda execution-role credentials.
    Callers must handle this exception explicitly — there is no silent
    degradation path.
    """


# ---------------------------------------------------------------------------
# IsolationManager
# ---------------------------------------------------------------------------

class TokenVendingMachine:
    """
    Obtains and caches tenant-scoped boto3 sessions via STS AssumeRole.

    Instantiate once at module load time (see singleton at bottom of file)
    so the TTLCache persists across warm Lambda invocations.
    """

    def __init__(self) -> None:
        """
        Read TENANT_ISOLATION_ROLE_ARN and ISOLATION_CACHE_TTL_SECONDS from env.

        Raises IsolationError if:
          - TENANT_ISOLATION_ROLE_ARN is missing or empty
          - ISOLATION_CACHE_TTL_SECONDS is >= 900 (would exceed STS credential lifetime)
        """
        role_arn = os.environ.get('TENANT_ISOLATION_ROLE_ARN', '')
        if not role_arn:
            raise IsolationError(
                "TENANT_ISOLATION_ROLE_ARN environment variable is not set. "
                "Ensure the Lambda function has this variable configured."
            )
        self.role_arn = role_arn

        try:
            ttl = int(os.environ.get('ISOLATION_CACHE_TTL_SECONDS', _DEFAULT_CACHE_TTL))
        except ValueError as exc:
            raise IsolationError(
                f"ISOLATION_CACHE_TTL_SECONDS must be an integer, got: "
                f"{os.environ.get('ISOLATION_CACHE_TTL_SECONDS')}"
            ) from exc

        if ttl >= 900:
            raise IsolationError(
                f"ISOLATION_CACHE_TTL_SECONDS must be < 900 (got {ttl}). "
                "The cache TTL must be strictly less than the STS credential "
                "lifetime (900 s) to ensure cached credentials never expire "
                "while still in use."
            )
        self._ttl = ttl

        self._cache: TTLCache = TTLCache(maxsize=_CACHE_MAXSIZE, ttl=ttl)
        self._sts_client = boto3.client('sts')

    # ------------------------------------------------------------------
    # Internal: assume role and return raw credentials dict
    # ------------------------------------------------------------------

    def _assume_role(self, tenant_id: str) -> Dict:
        """
        Call sts:AssumeRole with a tenantId session tag.

        The session tag is the mechanism that activates IAM attribute-based
        access control (ABAC) conditions on TenantIsolationRole, e.g.:
            dynamodb:LeadingKeys: "${aws:PrincipalTag/tenantId}#*"

        Args:
            tenant_id: The tenant identifier to embed as a session tag.

        Returns:
            The Credentials dict from the STS AssumeRole response.

        Raises:
            IsolationError: on any STS exception or unexpected error.
        """
        try:
            # TODO: Task 2.1 — call sts:AssumeRole with tenantId session tag
            raise NotImplementedError("Complete Task 2.1 — _assume_role")  # REPLACE THIS LINE
        except IsolationError:
            raise
        except Exception as exc:
            raise IsolationError(
                f"STS AssumeRole failed for tenant '{tenant_id}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Public: get a tenant-scoped boto3.Session
    # ------------------------------------------------------------------

    def get_session(self, tenant_context: Dict) -> boto3.Session:
        """
        Return a boto3.Session built from tenant-scoped temporary credentials.

        Checks the TTLCache first; calls _assume_role on a cache miss and
        stores the credentials for subsequent calls within the TTL window.

        Args:
            tenant_context: Dict containing at least 'tenantId'.

        Returns:
            boto3.Session constructed from the scoped STS credentials.

        Raises:
            IsolationError: if tenantId is absent/empty, or STS fails.
        """
        # TODO: Task 2.2 — validate tenant_id, check cache, build boto3.Session
        raise NotImplementedError("Complete Task 2.2 — get_session")  # REPLACE THIS LINE

    # ------------------------------------------------------------------
    # Public: convenience wrappers
    # ------------------------------------------------------------------

    def get_client(self, service_name: str, tenant_context: Dict) -> Any:
        """
        Return boto3.client(service_name) from the tenant-scoped session.

        Use for services where the client interface is preferred (e.g. S3,
        for presigned URLs and upload_file with ExtraArgs).

        Args:
            service_name:   boto3 service identifier, e.g. 's3', 'rds'.
            tenant_context: Dict containing at least 'tenantId'.

        Returns:
            A boto3 service client scoped to the tenant's credentials.

        Raises:
            IsolationError: propagated from get_session.
        """
        session = self.get_session(tenant_context)
        return session.client(service_name)

    def get_resource(self, service_name: str, tenant_context: Dict) -> Any:
        """
        Return boto3.resource(service_name) from the tenant-scoped session.

        Use for services where the resource interface is preferred (e.g.
        DynamoDB, for Table.put_item / Table.query).

        Args:
            service_name:   boto3 service identifier, e.g. 'dynamodb'.
            tenant_context: Dict containing at least 'tenantId'.

        Returns:
            A boto3 service resource scoped to the tenant's credentials.

        Raises:
            IsolationError: propagated from get_session.
        """
        session = self.get_session(tenant_context)
        return session.resource(service_name)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# Instantiated once at Lambda cold-start so the TTLCache persists across
# warm invocations. All downstream modules import this singleton:
#
#     from token_vending_machine import token_vending_machine, IsolationError
#
token_vending_machine = TokenVendingMachine()
