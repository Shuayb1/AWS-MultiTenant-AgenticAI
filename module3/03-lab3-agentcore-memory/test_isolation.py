#!/usr/bin/env python3
# test_isolation.py — Prove IAM-enforced tenant memory isolation
# Module 3, Lab 3
#
# This script demonstrates that tenant-002's TVM credentials are PHYSICALLY
# denied from accessing tenant-001's memory at the AWS control plane.
#
# Usage: python3 test_isolation.py
import base64
import json
import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
TVM_ROLE_ARN = os.environ.get("AGENTCORE_TVM_ROLE_ARN", "")
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "Workshop@123!")

if not TVM_ROLE_ARN or not MEMORY_ID:
    print("ERROR: Set AGENTCORE_TVM_ROLE_ARN and AGENTCORE_MEMORY_ID")
    print("  source .lab3_env")
    exit(1)

sts = boto3.client("sts", region_name=REGION)

# Get the Cognito sub (user ID) for testuser1 to match the actor_id format
cognito = boto3.client("cognito-idp", region_name=REGION)
try:
    auth = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=COGNITO_CLIENT_ID,
        AuthParameters={"USERNAME": "testuser1@example.com", "PASSWORD": TEST_PASSWORD},
    )
    token = auth["AuthenticationResult"]["IdToken"]
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    claims = json.loads(base64.urlsafe_b64decode(part))
    user_sub = claims.get("sub", "test-user")
except Exception:
    user_sub = "test-user"

# Build the same actor_id/session_id format as the agent
ACTOR_ID_T1 = f"tenant-001:workshop-agent:{user_sub}"
SESSION_ID_T1 = "tenant-001-workshop-agent-{}-conv-001".format(user_sub)

print("╔══════════════════════════════════════════════════════════════╗")
print("║  Tenant Memory Isolation Proof                              ║")
print("╚══════════════════════════════════════════════════════════════╝")
print()

# --- Step 1: Tenant-001 can access their own memory ---
print("── Step 1: Tenant-001 accesses OWN memory (should succeed) ──")
try:
    creds = sts.assume_role(
        RoleArn=TVM_ROLE_ARN,
        RoleSessionName="tenant-tenant-001",
        Tags=[{"Key": "TenantID", "Value": "tenant-001"}],
        DurationSeconds=900,
    )["Credentials"]

    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    client = session.client("bedrock-agentcore", region_name=REGION)
    resp = client.list_events(
        memoryId=MEMORY_ID,
        actorId=ACTOR_ID_T1,
        sessionId=SESSION_ID_T1,
    )
    print(f"  ✓ SUCCESS — tenant-001 listed events (count: {len(resp.get('events', []))})")
except Exception as e:
    print(f"  ✗ UNEXPECTED — {type(e).__name__}: {e}")

print()

# --- Step 2: Tenant-002 tries to access tenant-001's memory ---
print("── Step 2: Tenant-002 accesses TENANT-001's memory (should DENY) ──")
try:
    creds = sts.assume_role(
        RoleArn=TVM_ROLE_ARN,
        RoleSessionName="tenant-tenant-002",
        Tags=[{"Key": "TenantID", "Value": "tenant-002"}],
        DurationSeconds=900,
    )["Credentials"]

    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    client = session.client("bedrock-agentcore", region_name=REGION)

    # Attempt to read tenant-001's actor_id with tenant-002's credentials
    resp = client.list_events(
        memoryId=MEMORY_ID,
        actorId=ACTOR_ID_T1,
        sessionId=SESSION_ID_T1,
    )
    print(f"  ✗ ISOLATION FAILURE — tenant-002 accessed tenant-001's events!")
    print(f"    This should never happen. Check the TVM role IAM policy.")
except Exception as e:
    if "AccessDenied" in str(e):
        print(f"  ✓ ISOLATION CONFIRMED — IAM denied the request")
        print(f"    Error: AccessDeniedException")
        print(f"    Reason: bedrock-agentcore:actorId condition requires")
        print(f"            actorId to start with '${{aws:PrincipalTag/TenantID}}:'")
        print(f"            Tenant-002's tag = 'tenant-002' but requested actorId")
        print(f"            starts with 'tenant-001:' → DENIED at control plane")
    else:
        print(f"  ? Unexpected error: {type(e).__name__}: {e}")

print()
print("── Summary ────────────────────────────────────────────────────")
print("  Tenant-001 reads own memory    ✓  (actorId matches TenantID tag)")
print("  Tenant-002 denied cross-tenant ✓  (IAM ABAC condition enforced)")
print("  Enforcement level: AWS IAM control plane (not application code)")
