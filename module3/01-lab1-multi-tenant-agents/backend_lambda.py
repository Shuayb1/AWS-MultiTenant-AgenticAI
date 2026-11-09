# SPDX-License-Identifier: MIT-0
"""
backend_lambda.py — Backend Lambda for Pooled Multi-Tenant Agent
Module 3, Lab 1

This Lambda sits behind API Gateway and mediates between end users and AgentCore Runtime.
API Gateway validates the Cognito JWT via the Lambda Authorizer before this function is invoked.

Responsibilities:
  1. Extract tenant_id from the validated JWT claims (provided by API Gateway authorizer)
  2. Construct a tenant-scoped session ID: "{tenant_id}-{conversation_id}"
  3. Assume the TVM role with a TenantID session tag
  4. Call AgentCore Runtime via SigV4 with the scoped credentials
  5. Pass the caller's JWT as a custom header (X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization)
     so the agent can propagate tenant identity to downstream tools

The IAM condition on the TVM role (bedrock-agentcore:sessionId) ensures that even if this
code has a bug in session ID construction, IAM will deny a mismatch at the control plane.

Env vars:
  AGENT_RUNTIME_ARN      — ARN of the AgentCore Runtime agent
  TVM_ROLE_ARN           — IAM role ARN for TVM credential scoping
  AWS_REGION             — AWS region (default: us-east-1)
"""

import json
import os
import uuid

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
TVM_ROLE_ARN = os.environ.get("TVM_ROLE_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_response(response: dict) -> str:
    """Parse the response from AgentCore Runtime into a string."""
    content = []
    content_type = response.get("contentType", "")

    # The SDK returns 'response' as a StreamingBody or EventStream
    resp_body = response.get("response")
    if resp_body is None:
        return ""

    try:
        # StreamingBody: read all bytes
        if hasattr(resp_body, "read"):
            raw = resp_body.read().decode("utf-8")
            # Try to parse as JSON first
            try:
                parsed = json.loads(raw)
                return parsed.get("response", parsed.get("text", str(parsed)))
            except json.JSONDecodeError:
                return raw

        # EventStream: iterate chunks
        if hasattr(resp_body, "__iter__"):
            for chunk in resp_body:
                if isinstance(chunk, bytes):
                    content.append(chunk.decode("utf-8"))
                elif isinstance(chunk, dict):
                    body = chunk.get("body", b"")
                    if isinstance(body, bytes):
                        content.append(body.decode("utf-8"))
                    else:
                        content.append(str(body))
                else:
                    content.append(str(chunk))

            joined = "".join(content)
            try:
                parsed = json.loads(joined)
                return parsed.get("response", parsed.get("text", str(parsed)))
            except json.JSONDecodeError:
                return joined

    except Exception as e:
        return f"Error parsing response: {str(e)}"

    return str(response)


# ---------------------------------------------------------------------------
# Task 1.1 — Backend Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    API Gateway Lambda Proxy handler.

    The Lambda Authorizer has already validated the Cognito JWT. The tenant identity
    is available in event["requestContext"]["authorizer"]["claims"]["custom:tenant_id"].

    This function:
      1. Extracts tenant_id from the validated claims
      2. Reads conversation_id from the request body (or generates a new one)
      3. Constructs the tenant-scoped session ID
      4. Assumes the TVM role with a TenantID session tag
      5. Calls AgentCore Runtime via SigV4 with the scoped credentials
      6. Returns the agent response to the caller

    Args:
        event: API Gateway Lambda Proxy event.
        context: Lambda execution context.

    Returns:
        API Gateway Lambda Proxy response.
    """
    # Parse request body
    try:
        body = json.loads(event.get("body", "{}"))

        # TODO: Task 1.4 - Extract tenant_id and construct the tenant-scoped session ID
        raise NotImplementedError("Complete Task 1.4 - handle_request")  # REPLACE THIS LINE

        # ---------------------------------------------------------------------------
        # Pre-implemented: Steps 3-5 (TVM AssumeRole, invoke AgentCore, parse response)
        # ---------------------------------------------------------------------------

        # Step 3: Assume TVM role with tenantId session tag
        sts = boto3.client("sts", region_name=REGION)
        credentials = sts.assume_role(
            RoleArn=TVM_ROLE_ARN,
            RoleSessionName=f"tenant-{tenant_id}",
            Tags=[{"Key": "tenantId", "Value": tenant_id}],
            DurationSeconds=900,
        )["Credentials"]

        # Step 4: Invoke AgentCore Runtime via SigV4 with TVM-scoped credentials
        # Pass the caller's JWT as a custom header so the agent can propagate tenant identity
        # to downstream tools (MCP servers, Lambda functions, etc.)
        jwt_token = event.get("headers", {}).get("Authorization", "")

        tvm_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        agentcore = tvm_session.client("bedrock-agentcore", region_name=REGION)

        # Register a botocore event handler to inject the JWT as a custom header
        # per https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html
        EVENT_NAME = "before-sign.bedrock-agentcore.InvokeAgentRuntime"
        CUSTOM_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization"

        def add_jwt_header(request, **kwargs):
            request.headers[CUSTOM_HEADER] = jwt_token

        event_system = agentcore.meta.events
        event_system.register_first(EVENT_NAME, add_jwt_header)

        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            qualifier="DEFAULT",
            payload=json.dumps({
                "prompt": message,
            }).encode(),
        )

        event_system.unregister(EVENT_NAME, add_jwt_header)

        # Step 5: Parse and return the agent response
        agent_response = parse_response(response)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "response": agent_response,
                "tenant_id": tenant_id,
                "session_id": session_id,
            }),
        }
    except Exception as e:
        import traceback
        print(f"ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e), "type": type(e).__name__}),
        }
