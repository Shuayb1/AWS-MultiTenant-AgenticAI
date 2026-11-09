# SPDX-License-Identifier: MIT-0
"""
setup_knowledge_base.py — Create a Bedrock Knowledge Base for Module 2, Lab 3
Module 2, Lab 3 Prerequisite

Creates:
  - Bedrock Knowledge Base backed by OpenSearch Serverless
  - S3 data source pointing to kb-datasource/ prefix
  - Triggers a sync of the data source

The KB execution role is pre-created by the CloudFormation stack.

Usage:
    python setup_knowledge_base.py

    Outputs the KNOWLEDGE_BASE_ID for use in tool_opensearch.py.
"""

import os
import sys
import time
from pathlib import Path

import boto3

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

boto3.setup_default_session(region_name=AWS_REGION)


def _resolve_config():
    """Resolve config from CloudFormation stack outputs."""
    config = {
        'S3_VECTOR_BUCKET': os.environ.get('S3_VECTOR_BUCKET', ''),
        'OPENSEARCH_COLLECTION_ARN': os.environ.get('OPENSEARCH_COLLECTION_ARN', ''),
        'KB_ROLE_ARN': os.environ.get('KB_ROLE_ARN', ''),
        'ACCOUNT_ID': '',
    }
    try:
        cfn = boto3.client('cloudformation')
        resp = cfn.describe_stacks(StackName=STACK_NAME)
        outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
        if not config['S3_VECTOR_BUCKET']:
            config['S3_VECTOR_BUCKET'] = outputs.get('DataBucketName', '')
        if not config['OPENSEARCH_COLLECTION_ARN']:
            config['OPENSEARCH_COLLECTION_ARN'] = outputs.get('OpenSearchCollectionArn', '')
        if not config['KB_ROLE_ARN']:
            config['KB_ROLE_ARN'] = outputs.get('KnowledgeBaseExecutionRoleArn', '')
        sts = boto3.client('sts')
        config['ACCOUNT_ID'] = sts.get_caller_identity()['Account']
    except Exception as e:
        print(f"Warning: Could not resolve config: {e}")
    return config


def _create_knowledge_base(role_arn, collection_arn):
    """Create a Bedrock Knowledge Base backed by OpenSearch Serverless."""
    bedrock_agent = boto3.client('bedrock-agent')
    kb_name = f"{STACK_NAME}-maintenance-kb"

    # Check if KB already exists
    existing = bedrock_agent.list_knowledge_bases(maxResults=100)
    for kb in existing.get('knowledgeBaseSummaries', []):
        if kb['name'] == kb_name:
            print(f"  Knowledge Base already exists: {kb['knowledgeBaseId']}")
            return kb['knowledgeBaseId']

    # Extract collection ID from ARN for the index name
    # ARN format: arn:aws:aoss:region:account:collection/collection-id
    response = bedrock_agent.create_knowledge_base(
        name=kb_name,
        description="Maintenance records for multi-tenant property management (Module 2 Lab 3)",
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            'type': 'VECTOR',
            'vectorKnowledgeBaseConfiguration': {
                'embeddingModelArn': f"arn:aws:bedrock:{AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0",
            },
        },
        storageConfiguration={
            'type': 'OPENSEARCH_SERVERLESS',
            'opensearchServerlessConfiguration': {
                'collectionArn': collection_arn,
                'vectorIndexName': 'maintenance_records',
                'fieldMapping': {
                    'vectorField': 'embedding',
                    'textField': 'text',
                    'metadataField': 'metadata',
                },
            },
        },
    )
    kb_id = response['knowledgeBase']['knowledgeBaseId']
    print(f"  Created Knowledge Base: {kb_id}")
    return kb_id


def _create_data_source(kb_id, bucket_name):
    """Create an S3 data source for the Knowledge Base."""
    bedrock_agent = boto3.client('bedrock-agent')
    ds_name = f"{STACK_NAME}-maintenance-datasource"

    # Check if data source already exists
    existing = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id, maxResults=100)
    for ds in existing.get('dataSourceSummaries', []):
        if ds['name'] == ds_name:
            print(f"  Data source already exists: {ds['dataSourceId']}")
            return ds['dataSourceId']

    response = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=ds_name,
        description="Maintenance history documents from S3 (tenant-scoped via metadata)",
        dataSourceConfiguration={
            'type': 'S3',
            's3Configuration': {
                'bucketArn': f"arn:aws:s3:::{bucket_name}",
                'inclusionPrefixes': ['kb-datasource/'],
            },
        },
    )
    ds_id = response['dataSource']['dataSourceId']
    print(f"  Created data source: {ds_id}")
    return ds_id


def _sync_data_source(kb_id, ds_id):
    """Start a sync job for the data source."""
    bedrock_agent = boto3.client('bedrock-agent')

    response = bedrock_agent.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
    )
    job_id = response['ingestionJob']['ingestionJobId']
    print(f"  Started sync job: {job_id}")

    # Wait for sync to complete
    print("  Waiting for sync to complete", end="")
    for _ in range(60):
        time.sleep(5)
        print(".", end="", flush=True)
        status = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job_id,
        )
        job_status = status['ingestionJob']['status']
        if job_status == 'COMPLETE':
            stats = status['ingestionJob'].get('statistics', {})
            print(f"\n  Sync complete! Documents: {stats.get('numberOfDocumentsScanned', '?')}")
            return
        elif job_status == 'FAILED':
            reason = status['ingestionJob'].get('failureReasons', ['Unknown'])
            print(f"\n  Sync FAILED: {reason}")
            sys.exit(1)

    print("\n  WARNING: Sync still in progress after 5 minutes. Check the console.")


def main():
    print("=== Setting up Bedrock Knowledge Base ===\n")

    cfg = _resolve_config()
    bucket_name = cfg['S3_VECTOR_BUCKET']
    collection_arn = cfg['OPENSEARCH_COLLECTION_ARN']
    role_arn = cfg['KB_ROLE_ARN']
    account_id = cfg['ACCOUNT_ID']

    missing = []
    if not bucket_name:
        missing.append('S3_VECTOR_BUCKET')
    if not collection_arn:
        missing.append('OPENSEARCH_COLLECTION_ARN')
    if not role_arn:
        missing.append('KB_ROLE_ARN (KnowledgeBaseExecutionRoleArn stack output)')
    if missing:
        print(f"ERROR: Could not resolve: {', '.join(missing)}")
        print(f"Ensure the '{STACK_NAME}' stack is deployed and up to date.")
        sys.exit(1)

    print(f"  Bucket: {bucket_name}")
    print(f"  Collection: {collection_arn}")
    print(f"  KB Role: {role_arn}\n")

    # Step 1: Create Knowledge Base
    print("1. Creating Knowledge Base...")
    kb_id = _create_knowledge_base(role_arn, collection_arn)

    # Wait for KB to become ACTIVE
    print("  Waiting for Knowledge Base to become ACTIVE", end="")
    bedrock_agent = boto3.client('bedrock-agent')
    for _ in range(60):
        resp = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
        status = resp['knowledgeBase']['status']
        if status == 'ACTIVE':
            print(" done")
            break
        elif status == 'FAILED':
            print(f"\n  ERROR: Knowledge Base creation failed")
            sys.exit(1)
        print(".", end="", flush=True)
        import time
        time.sleep(5)
    else:
        print("\n  ERROR: Timed out waiting for Knowledge Base to become ACTIVE")
        sys.exit(1)

    # Step 2: Create data source
    print("\n2. Creating S3 data source...")
    ds_id = _create_data_source(kb_id, bucket_name)

    # Step 3: Sync data source
    print("\n3. Syncing data source...")
    _sync_data_source(kb_id, ds_id)

    # Write KB ID to env file for automatic sourcing
    env_file = Path(__file__).parent / '.kb_env'
    env_file.write_text(f"export KNOWLEDGE_BASE_ID={kb_id}\n")

    # Output
    print(f"\n{'='*60}")
    print(f"KNOWLEDGE_BASE_ID={kb_id}")
    print(f"{'='*60}")
    print(f"\nEnvironment saved to .kb_env. Run:")
    print(f"  source .kb_env")
    print(f"\nThen verify:")
    print(f"  python tool_opensearch.py")


if __name__ == '__main__':
    main()
