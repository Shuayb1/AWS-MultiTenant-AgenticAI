# SPDX-License-Identifier: MIT-0
lved.items():
        print(f"export {key}={value}")

    print("# Environment ready.", file=sys.stderr)


if __name__ == "__main__":
    main()
lved[env_var] = value
            print(f"  {env_var}={value}", file=sys.stderr)

    resolved.update(STATIC_VARS)

    if missing:
        for msg in missing:
            print(msg, file=sys.stderr)
        print(
            f"\nOne or more variables failed to load.\n"
            f"Check: AWS Console → CloudFormation → Stacks → {STACK_NAME} → Outputs",
            file=sys.stderr,
        )
        sys.exit(1)

    # Print export statements to stdout so the caller can source them
    for key, value in reso_name}': {e}", file=sys.stderr)
        sys.exit(1)


def main():
    print(f"# Loading environment from stack: {STACK_NAME} (region: {AWS_REGION})",
          file=sys.stderr)

    outputs = get_stack_outputs(STACK_NAME, AWS_REGION)

    resolved = {}
    missing = []

    for env_var, output_key in CFN_OUTPUTS.items():
        value = outputs.get(output_key, "")
        if not value:
            missing.append(f"  ERROR: {env_var} — OutputKey '{output_key}' not found in stack")
        else:
            reso for o in outputs}
    except client.exceptions.ClientError as e:
        print(f"ERROR: Could not describe stack '{stackyUrl",
}

# Static workshop credentials (pre-created by workshop setup)
STATIC_VARS = {
    "TEST_USERNAME1": "testuser1@example.com",
    "TEST_PASSWORD": "Workshop@123!",
    "AWS_REGION":    AWS_REGION,
}


def get_stack_outputs(stack_name: str, region: str) -> dict:
    client = boto3.client("cloudformation", region_name=region)
    try:
        response = client.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0].get("Outputs", [])
        return {o["OutputKey"]: o["OutputValue"]up_env.py              # prints export commands and writes .env
    source <(python setup_env.py)    # exports variables directly into your shell session
"""

import boto3
import os
import sys

STACK_NAME = os.environ.get("STACK_NAME", "workshop-infrastructure")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Variables to pull from CloudFormation Outputs
CFN_OUTPUTS = {
    "COGNITO_USER_POOL_ID": "CognitoUserPoolId",
    "COGNITO_CLIENT_ID":    "CognitoClientId",
    "API_GATEWAY_URL":      "ApiGatewa#!/usr/bin/env python3
"""
setup_env.py — Load workshop environment variables from CloudFormation stack outputs.

Usage:
    python set