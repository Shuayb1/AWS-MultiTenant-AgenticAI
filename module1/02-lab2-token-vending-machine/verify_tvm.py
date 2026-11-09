# SPDX-License-Identifier: MIT-0
"""
Verify the Token Vending Machine (TVM) is working correctly.
Run after completing Tasks 2.1 and 2.2.

Usage:
    python verify_tvm.py
"""

import json
import os

import boto3

from token_vending_machine import token_vending_machine, IsolationError

REGION = os.environ.get('AWS_REGION', 'us-east-1')
ROLE_ARN = os.environ.get('TENANT_ISOLATION_ROLE_ARN', '')


def main():
    print("=== Token Vending Machine Verification ===\n")

    # ── Step 1: Show the IAM policy that enforces isolation ──────────────
    print("─── IAM Policy on TVM Role ───")
    role_name = ROLE_ARN.split('/')[-1] if ROLE_ARN else 'workshop-infrastructure-tenant-isolation-role'
    iam = boto3.client('iam', region_name=REGION)
    try:
        policy = iam.get_role_policy(
            RoleName=role_name,
            PolicyName='TenantIsolationPolicy',
        )
        statements = policy['PolicyDocument']['Statement']
        for stmt in statements:
            condition = stmt.get('Condition', {})
            if condition:
                print(json.dumps(stmt, indent=2, default=str))
                print()
                break

        print("  How it works:")
        print("  - ${aws:PrincipalTag/tenantId} is resolved from the STS session tag")
        print("  - When you assume with tenantId=tenant-001, IAM substitutes it at runtime")
        print("  - Only resources matching 'tenant-001#*' or 'tenants/tenant-001/*' are accessible")
        print("  - Everything else is denied at the control plane\n")
    except Exception as e:
        print(f"  Could not read policy: {e}\n")

    # ── Step 2: Assume role as tenant-001 ────────────────────────────────
    print("─── Assuming TVM Role as tenant-001 ───")
    session = token_vending_machine.get_session({'tenantId': 'tenant-001'})
    sts = session.client('sts')
    identity = sts.get_caller_identity()
    print(f"  Assumed Role: {identity['Arn']}")
    print(f"  Session tag:  tenantId=tenant-001\n")

    # ── Step 3: Empty tenantId rejected ──────────────────────────────────
    print("─── Test: Empty tenantId (fail-closed) ───")
    try:
        token_vending_machine.get_session({})
        print("  ERROR: Should have raised IsolationError")
    except IsolationError:
        print("  Correctly rejected: IsolationError raised")
        print("  No STS call made without a valid tenantId\n")

    print("=== Verification Complete ===")


if __name__ == '__main__':
    main()
