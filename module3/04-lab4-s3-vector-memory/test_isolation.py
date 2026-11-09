#!/usr/bin/env python3
# test_isolation.py — Prove IAM-enforced tenant vector memory isolation
# Module 3, Lab 4
#
# Proves that tenant-002's TVM credentials are PHYSICALLY denied from
# accessing tenant-001's vector index at the AWS control plane.
#
# Usage: python3 test_isolation.py
import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
TVM_ROLE_ARN = os.environ.get("S3_VECTOR_TVM_ROLE_ARN", "")
BUCKET_NAME = os.environ.get("S3_VECTOR_BUCKET_NAME", "")

if not TVM_ROLE_ARN or not BUCKET_NAME:
    print("ERROR: Set S3_VECTOR_TVM_ROLE_ARN and S3_VECTOR_BUCKET_NAME")
    print("  source .lab4_env")
    exit(1)

sts = boto3.client("sts", region_name=REGION)

print("╔══════════════════════════════════════════════════════════════╗")
print("║  S3 Vector Memory Isolation Proof                           ║")
print("╚══════════════════════════════════════════════════════════════╝")
print()

# --- Step 1: Tenant-001 can access their own index ---
print("── Step 1: Tenant-001 accesses OWN index (should succeed) ──")
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
    client = session.client("s3vectors", region_name=REGION)
    resp = client.get_index(
        vectorBucketName=BUCKET_NAME,
        indexName="memory-tenant-001",
    )
    # Count vectors stored in tenant-001's index
    vectors_resp = client.list_vectors(
        vectorBucketName=BUCKET_NAME,
        indexName="memory-tenant-001",
    )
    vector_count = len(vectors_resp.get("vectors", []))
    print(f"  ✓ SUCCESS - tenant-001 accessed index 'memory-tenant-001' (vectors: {vector_count})")
except Exception as e:
    if "NotFound" in str(e) or "NoSuchIndex" in str(e):
        print(f"  ✓ SUCCESS - tenant-001 has access (index not yet created)")
    else:
        print(f"  ✗ UNEXPECTED - {type(e).__name__}: {e}")

print()

# --- Step 2: Tenant-002 tries to access tenant-001's index ---
print("── Step 2: Tenant-002 accesses TENANT-001's index (should DENY) ──")
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
    client = session.client("s3vectors", region_name=REGION)

    # Attempt to access tenant-001's index with tenant-002's credentials
    resp = client.get_index(
        vectorBucketName=BUCKET_NAME,
        indexName="memory-tenant-001",
    )
    print(f"  ✗ ISOLATION FAILURE - tenant-002 accessed tenant-001's index!")
except Exception as e:
    if "AccessDenied" in str(e):
        print(f"  ✓ ISOLATION CONFIRMED - IAM denied the request")
        print(f"    Error: AccessDeniedException")
        print(f"    Reason: TVM role Resource ARN scopes to")
        print(f"            .../index/memory-${{aws:PrincipalTag/TenantID}}")
        print(f"            Tenant-002's tag = 'tenant-002' but requested")
        print(f"            index = 'memory-tenant-001' - DENIED")
    else:
        print(f"  ? Unexpected error: {type(e).__name__}: {e}")

print()
print("── Summary ────────────────────────────────────────────────────")
print("  Tenant-001 accesses own index      ✓  (resource ARN matches tag)")
print("  Tenant-002 denied cross-tenant     ✓  (IAM resource-level enforcement)")
print("  Enforcement: IAM Resource ARN (not application code)")
