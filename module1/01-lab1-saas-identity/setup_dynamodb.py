# SPDX-License-Identifier: MIT-0
"""
Setup script to create DynamoDB table and populate sample data
"""

import boto3
import json
import os
from datetime import datetime

AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')


def create_tenant_metadata_table():
    """Create the TenantMetadata DynamoDB table (or verify it exists)"""
    dynamodb = boto3.client('dynamodb', region_name=AWS_REGION)
    
    # Check if table already exists (created by CloudFormation)
    try:
        response = dynamodb.describe_table(TableName='TenantMetadata')
        if response['Table']['TableStatus'] == 'ACTIVE':
            print("Table already exists (created by CloudFormation)")
            return True
    except dynamodb.exceptions.ResourceNotFoundException:
        pass  # Table doesn't exist, try to create it
    except Exception as e:
        # If we can't describe it, try to create it anyway
        pass

    try:
        response = dynamodb.create_table(
            TableName='TenantMetadata',
            KeySchema=[
                {
                    'AttributeName': 'tenantId',
                    'KeyType': 'HASH'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'tenantId',
                    'AttributeType': 'S'
                }
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        
        print("Creating table TenantMetadata...")
        waiter = dynamodb.get_waiter('table_exists')
        waiter.wait(TableName='TenantMetadata')
        print("Table created successfully!")
        return True
    except dynamodb.exceptions.ResourceInUseException:
        print("Table already exists")
        return True
    except Exception as e:
        print(f"Error creating table: {str(e)}")
        return False


def populate_sample_data():
    """Populate the table with sample tenant data"""
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table('TenantMetadata')
    
    # Sample tenants
    tenants = [
        {
            'tenantId': 'tenant-001',
            'tenantName': 'Acme Corporation',
            'tier': 'premium',
            'status': 'active',
            'createdAt': '2024-01-15T10:00:00Z',
            'features': ['advanced-analytics', 'api-access', 'custom-models'],
            'limits': {
                'maxUsers': 100,
                'maxStorage': 1099511627776,  # 1TB in bytes
                'apiRateLimit': 1000
            }
        },
        {
            'tenantId': 'tenant-002',
            'tenantName': 'TechStart Inc',
            'tier': 'standard',
            'status': 'active',
            'createdAt': '2024-02-01T10:00:00Z',
            'features': ['api-access'],
            'limits': {
                'maxUsers': 50,
                'maxStorage': 107374182400,  # 100GB in bytes
                'apiRateLimit': 500
            }
        },
        {
            'tenantId': 'tenant-003',
            'tenantName': 'Demo Company',
            'tier': 'free',
            'status': 'trial',
            'createdAt': '2024-03-01T10:00:00Z',
            'features': [],
            'limits': {
                'maxUsers': 10,
                'maxStorage': 10737418240,  # 10GB in bytes
                'apiRateLimit': 100
            }
        }
    ]
    
    # TODO: Insert each tenant into the table
    for tenant in tenants:
        try:
            # IMPLEMENT: Use table.put_item() to insert tenant
            table.put_item(Item=tenant)
            print(f"Inserted tenant: {tenant['tenantId']} - {tenant['tenantName']}")
        except Exception as e:
            print(f"Error inserting tenant {tenant['tenantId']}: {str(e)}")
    
    print(f"\nPopulated {len(tenants)} sample tenants")


def verify_setup():
    """Verify the table and data"""
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table('TenantMetadata')
    
    try:
        # Scan table to get all items
        response = table.scan()
        items = response['Items']
        
        print(f"\nVerification: Found {len(items)} tenants in table")
        for item in items:
            print(f"  - {item['tenantId']}: {item['tenantName']} ({item['tier']}, {item['status']})")
        
        return True
    except Exception as e:
        print(f"Error verifying setup: {str(e)}")
        return False


def main():
    """Main setup function"""
    print("=== DynamoDB Table Setup ===\n")
    
    # Step 1: Create table
    if create_tenant_metadata_table():
        print("\n✓ Table creation successful\n")
    else:
        print("\n✗ Table creation failed\n")
        return
    
    # Step 2: Populate data
    print("=== Populating Sample Data ===\n")
    populate_sample_data()
    
    # Step 3: Verify
    print("\n=== Verification ===")
    verify_setup()
    
    print("\n=== Setup Complete ===")


if __name__ == '__main__':
    main()
