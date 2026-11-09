# SPDX-License-Identifier: MIT-0
"""
enable_observability.py - Enable AgentCore Runtime observability
Module 3, Lab 5

One-time setup to enable CloudWatch Transaction Search and log delivery
for the agent runtime. This makes built-in metrics visible in the
CloudWatch GenAI Observability console.

Usage:
    python enable_observability.py
"""

import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "")
STACK_NAME = os.environ.get("STACK_NAME", "workshop-infrastructure")


def main():
    print("=== Enabling AgentCore Observability ===\n")

    if not ACCOUNT_ID:
        sts = boto3.client("sts", region_name=REGION)
        account_id = sts.get_caller_identity()["Account"]
    else:
        account_id = ACCOUNT_ID

    # Find any deployed agent runtime (Lab 1, 2, 3, or 4)
    agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)
    runtimes = agentcore.list_agent_runtimes()
    all_runtimes = runtimes.get("agentRuntimes", [])

    # Prefer Lab 5 > Lab 4 > Lab 3 > Lab 2 > Lab 1 > any
    runtime = None
    for name_prefix in ["workshopModule3Lab5", "workshopModule3Lab4", "workshopModule3Lab3", "workshopModule3Lab2", "workshopModule3PooledAgent", "workshopModule3"]:
        runtime = next((r for r in all_runtimes if r["agentRuntimeName"].startswith(name_prefix)), None)
        if runtime:
            break

    if not runtime and all_runtimes:
        runtime = all_runtimes[0]

    if not runtime:
        print("  ERROR: No agent runtime found. Complete Lab 1, 2, 3, or 4 first.")
        return

    runtime_id = runtime["agentRuntimeId"]
    print(f"  Agent Runtime: {runtime_id}")
    print(f"  Status: {runtime['status']}")

    # Check if log group exists (AgentCore creates it automatically)
    logs = boto3.client("logs", region_name=REGION)
    log_group_prefix = f"/aws/bedrock-agentcore/runtimes/{runtime_id}"

    try:
        response = logs.describe_log_groups(logGroupNamePrefix=log_group_prefix)
        if response.get("logGroups"):
            print(f"  Log group exists: {response['logGroups'][0]['logGroupName']}")
        else:
            print(f"  Log group will be created on first invocation: {log_group_prefix}-DEFAULT")
    except Exception as e:
        print(f"  Note: {e}")

    # List available CloudWatch metrics for AgentCore
    cw = boto3.client("cloudwatch", region_name=REGION)
    metrics = cw.list_metrics(Namespace="AWS/BedrockAgentCore")
    metric_names = sorted(set(m["MetricName"] for m in metrics.get("Metrics", [])))

    if metric_names:
        print(f"\n  Available AgentCore metrics ({len(metric_names)}):")
        for name in metric_names[:10]:
            print(f"    - {name}")
        if len(metric_names) > 10:
            print(f"    ... and {len(metric_names) - 10} more")
    else:
        print("\n  No metrics yet. Invoking the agent to generate metrics...")

        # Find the agent endpoint and invoke it a few times
        _invoke_agent_for_metrics(runtime, account_id)

        print("\n  Metrics will appear within 3-5 minutes.")

    print("\n  View in console:")
    print(f"  https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#generative-ai:agentcore")
    print("\n=== Done ===")


def _invoke_agent_for_metrics(runtime, account_id):
    """Invoke the agent a few times using curl to generate CloudWatch metrics."""
    import subprocess
    import time

    # Find the agent endpoint from lab env files
    env_files = [
        "/workshop/module3/05-lab5-per-tenant-usage-metering/.lab5_env",
        "/workshop/module3/04-lab4-s3-vector-memory/.lab4_env",
        "/workshop/module3/03-lab3-agentcore-memory/.lab3_env",
        "/workshop/module3/02-lab2-multi-tenant-mcp-tool-gateway/.lab2_env",
        "/workshop/module3/01-lab1-multi-tenant-agents/.lab1_env",
    ]

    agent_endpoint = ""
    for env_file in env_files:
        try:
            with open(env_file) as f:
                for line in f:
                    if "AGENT_ENDPOINT" in line and "=" in line:
                        val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                        if val and val.startswith("http"):
                            agent_endpoint = val
                            break
            if agent_endpoint:
                break
        except FileNotFoundError:
            continue

    if not agent_endpoint:
        print("  No agent endpoint found in lab env files. Invoke the agent manually.")
        return

    print(f"    Using endpoint: {agent_endpoint}")

    # Get JWT token
    cfn = boto3.client("cloudformation", region_name=REGION)
    stacks = cfn.describe_stacks(StackName="workshop-infrastructure")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks["Stacks"][0]["Outputs"]}
    client_id = outputs.get("CognitoClientId", "")

    cognito = boto3.client("cognito-idp", region_name=REGION)
    try:
        auth_result = cognito.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=client_id,
            AuthParameters={"USERNAME": "testuser1@example.com", "PASSWORD": "Workshop@123!"},
        )
        token = auth_result["AuthenticationResult"]["IdToken"]
    except Exception as e:
        print(f"  Could not get token: {e}. Invoke the agent manually.")
        return

    messages = [
        "Hello, what can you help me with?",
        "Tell me about multi-tenant architecture.",
        "What are the benefits of SaaS?",
    ]

    for i, msg in enumerate(messages, 1):
        session_id = f"metering-warmup-session-{i:033d}"
        cmd = [
            "curl", "-s", "-X", "POST", agent_endpoint,
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-H", f"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: {session_id}",
            "-d", f'{{"message": "{msg}"}}',
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout:
                print(f"    Invocation {i}/{len(messages)}: ok")
            else:
                print(f"    Invocation {i}/{len(messages)}: {result.stderr[:100] if result.stderr else 'empty response'}")
        except Exception as e:
            print(f"    Invocation {i}/{len(messages)}: {type(e).__name__}")
        if i < len(messages):
            time.sleep(2)


if __name__ == "__main__":
    main()
