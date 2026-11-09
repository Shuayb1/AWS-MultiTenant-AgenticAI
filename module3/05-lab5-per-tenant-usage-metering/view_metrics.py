# SPDX-License-Identifier: MIT-0
"""
view_metrics.py - Consolidated view of AgentCore and Bedrock metrics
Module 3, Lab 5

Shows platform-level metrics (no tenant breakdown) to demonstrate the gap
that the TenantMeteringPlugin fills.

Usage:
    python view_metrics.py
"""

import os
from datetime import datetime, timedelta, timezone

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")


def main():
    cw = boto3.client("cloudwatch", region_name=REGION)
    agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=12)

    # Find the Lab 5 agent runtime
    runtimes = agentcore.list_agent_runtimes()
    runtime = next(
        (r for r in runtimes.get("agentRuntimes", [])
         if "Lab5" in r["agentRuntimeName"]),
        None,
    )
    if not runtime:
        runtime = next(
            (r for r in runtimes.get("agentRuntimes", []) if r["status"] == "READY"),
            None,
        )

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Platform Metrics (last 12 hours)                         ║")
    print("╠══════════════════════════════════════════════════════════════╣")

    # --- AWS/Bedrock metrics ---
    print("║                                                            ║")
    print("║  AWS/Bedrock (Model Layer)                                 ║")
    print("║  ────────────────────────────────────────────────────────  ║")

    bedrock_metrics = {
        "InputTokenCount": {"stat": "Sum", "label": "Input Tokens"},
        "OutputTokenCount": {"stat": "Sum", "label": "Output Tokens"},
        "Invocations": {"stat": "Sum", "label": "Model Invocations"},
        "InvocationLatency": {"stat": "Average", "label": "Avg Latency (ms)"},
    }

    for metric_name, config in bedrock_metrics.items():
        result = cw.get_metric_statistics(
            Namespace="AWS/Bedrock",
            MetricName=metric_name,
            StartTime=start,
            EndTime=now,
            Period=43200,
            Statistics=[config["stat"]],
        )
        datapoints = result.get("Datapoints", [])
        if datapoints:
            value = datapoints[0].get(config["stat"], 0)
            line = f"    {config['label']:<25} {value:>8.0f}"
        else:
            line = f"    {config['label']:<25} {'N/A':>8}"
        print(f"║{line:<60}║")

    # --- AWS/Bedrock-AgentCore metrics ---
    if runtime:
        print("║                                                            ║")
        print("║  AWS/Bedrock-AgentCore (Runtime Layer)                     ║")
        print("║  ────────────────────────────────────────────────────────  ║")

        agent_line = f"    Agent: {runtime['agentRuntimeName']}"
        print(f"║{agent_line:<60}║")

        runtime_arn = runtime["agentRuntimeArn"]
        runtime_name = f"{runtime['agentRuntimeName']}::DEFAULT"

        dimensions = [
            {"Name": "Resource", "Value": runtime_arn},
            {"Name": "Operation", "Value": "InvokeAgentRuntime"},
            {"Name": "Name", "Value": runtime_name},
        ]

        runtime_metrics = {
            "Invocations": {"stat": "Sum", "label": "Agent Invocations"},
            "Duration": {"stat": "Average", "label": "Avg Duration (ms)"},
            "Latency": {"stat": "Average", "label": "Avg Latency (ms)"},
            "Sessions": {"stat": "Sum", "label": "Sessions"},
            "UserErrors": {"stat": "Sum", "label": "User Errors"},
            "SystemErrors": {"stat": "Sum", "label": "System Errors"},
            "Throttles": {"stat": "Sum", "label": "Throttles"},
        }

        for metric_name, config in runtime_metrics.items():
            try:
                result = cw.get_metric_statistics(
                    Namespace="AWS/Bedrock-AgentCore",
                    MetricName=metric_name,
                    Dimensions=dimensions,
                    StartTime=start,
                    EndTime=now,
                    Period=43200,
                    Statistics=[config["stat"]],
                )
                datapoints = result.get("Datapoints", [])
                if datapoints:
                    value = datapoints[0].get(config["stat"], 0)
                    line = f"    {config['label']:<25} {value:>8.0f}"
                else:
                    line = f"    {config['label']:<25} {'N/A':>8}"
            except Exception:
                line = f"    {config['label']:<25} {'err':>8}"
            print(f"║{line:<60}║")

    # --- AWS/Bedrock-AgentCore Gateway metrics (if Lab 2 was completed) ---
    gateways = []
    try:
        gw_response = agentcore.list_gateways()
        gateways = gw_response.get("items", [])
    except Exception:
        pass

    if gateways:
        gw = gateways[0]
        gw_arn = gw.get("gatewayArn", gw.get("arn", ""))
        gw_name = gw.get("name", gw.get("gatewayId", "unknown"))

        if not gw_arn:
            # Try to construct ARN from gatewayId
            gw_id = gw.get("gatewayId", "")
            if gw_id:
                gw_arn = f"arn:aws:bedrock-agentcore:{REGION}:{os.environ.get('ACCOUNT_ID', boto3.client('sts').get_caller_identity()['Account'])}:gateway/{gw_id}"

    if gateways and gw_arn:
        print("║                                                            ║")
        print("║  AWS/Bedrock-AgentCore (Gateway Layer)                     ║")
        print("║  ────────────────────────────────────────────────────────  ║")

        gw_line = f"    Gateway: {gw_name}"
        print(f"║{gw_line:<60}║")

        gw_dimensions = [
            {"Name": "Resource", "Value": gw_arn},
            {"Name": "Operation", "Value": "InvokeGateway"},
        ]

        gw_metrics = {
            "Invocations": {"stat": "Sum", "label": "Tool Invocations"},
            "Latency": {"stat": "Average", "label": "Avg Latency (ms)"},
            "UserErrors": {"stat": "Sum", "label": "User Errors"},
        }

        for metric_name, config in gw_metrics.items():
            try:
                result = cw.get_metric_statistics(
                    Namespace="AWS/Bedrock-AgentCore",
                    MetricName=metric_name,
                    Dimensions=gw_dimensions,
                    StartTime=start,
                    EndTime=now,
                    Period=43200,
                    Statistics=[config["stat"]],
                )
                datapoints = result.get("Datapoints", [])
                if datapoints:
                    value = datapoints[0].get(config["stat"], 0)
                    line = f"    {config['label']:<25} {value:>8.0f}"
                else:
                    line = f"    {config['label']:<25} {'N/A':>8}"
            except Exception:
                line = f"    {config['label']:<25} {'err':>8}"
            print(f"║{line:<60}║")

    # --- Custom SaaS/AgentMetering (from plugin) ---
    print("║                                                            ║")
    print("║  SaaS/AgentMetering (Custom Plugin - Per Tenant)           ║")
    print("║  ────────────────────────────────────────────────────────  ║")

    # Discover actual dimensions from CloudWatch
    plugin_metrics = ["InputTokens", "OutputTokens", "InvocationCount"]
    all_custom_metrics = cw.list_metrics(Namespace="SaaS/AgentMetering")
    custom_metrics_list = all_custom_metrics.get("Metrics", [])

    if custom_metrics_list:
        # Extract unique dimension sets
        agents = set()
        tenants = set()
        models = set()
        for m in custom_metrics_list:
            for d in m.get("Dimensions", []):
                if d["Name"] == "Agent":
                    agents.add(d["Value"])
                elif d["Name"] == "TenantId":
                    tenants.add(d["Value"])
                elif d["Name"] == "Model":
                    models.add(d["Value"])

        # Show dimensions header
        line = f"    Dimensions: Agent, TenantId, Model"
        print(f"║{line:<60}║")
        if agents:
            line = f"    Agent(s):   {', '.join(sorted(agents))}"
            print(f"║{line:<60}║")
        if models:
            line = f"    Model(s):   {', '.join(sorted(models))}"
            print(f"║{line:<60}║")
        print(f"║{'':60}║")

        # Show per-tenant metrics (one metric per line for readability)
        has_custom = False
        for tenant in sorted(tenants) if tenants else ["tenant-001", "tenant-002"]:
            header = f"    {tenant}:"
            print(f"║{header:<60}║")
            for metric_name in plugin_metrics:
                try:
                    matching = [m for m in custom_metrics_list
                                if m["MetricName"] == metric_name
                                and any(d["Name"] == "TenantId" and d["Value"] == tenant
                                        for d in m.get("Dimensions", []))]
                    # Prefer metrics with 3 dimensions (Agent, TenantId, Model) over 2
                    matching.sort(key=lambda m: len(m.get("Dimensions", [])), reverse=True)
                    if matching:
                        dims = matching[0]["Dimensions"]
                        result = cw.get_metric_statistics(
                            Namespace="SaaS/AgentMetering",
                            MetricName=metric_name,
                            Dimensions=dims,
                            StartTime=start,
                            EndTime=now,
                            Period=43200,
                            Statistics=["Sum"],
                        )
                        datapoints = result.get("Datapoints", [])
                        if datapoints:
                            value = datapoints[0].get("Sum", 0)
                            line = f"      {metric_name:<20} {value:>8.0f}"
                            print(f"║{line:<60}║")
                            has_custom = True
                except Exception:
                    pass

        if not has_custom:
            print(f"║{'    No per-tenant metrics yet.':<60}║")
            print(f"║{'    Complete Task 5.3-5.4 and redeploy to enable.':<60}║")
    else:
        tenants = ["tenant-001", "tenant-002"]
        for tenant in tenants:
            print(f"║{'    ' + tenant + ':':<60}║")
        print(f"║{'    No per-tenant metrics yet.':<60}║")
        print(f"║{'    Complete Task 5.3-5.4 and redeploy to enable.':<60}║")

    print("║                                                            ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  Notice: Platform metrics have NO TenantId dimension.      ║")
    print("║  The plugin adds TenantId to close the attribution gap.    ║")
    print("╚══════════════════════════════════════════════════════════════╝")


if __name__ == "__main__":
    main()
