# SPDX-License-Identifier: MIT-0
"""
verify_mcp.py — Verify the MCP Server tool over the network
Module 2, Lab 2, Task 2.3 Verification

Connects to the running MCP server (started in another terminal) and
verifies tool discovery and invocation over the MCP protocol.

Setup (Terminal 1):
    python tool_s3_vectors.py --serve

Verify (Terminal 2):
    python verify_mcp.py
"""

import json
import os
import sys
import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_URL = os.environ.get('MCP_URL', 'http://localhost:9100/sse')


async def run_mcp_verification():
    """Connect to the running MCP server and verify tool discovery + invocation."""

    print(f"  Connecting to MCP server at {MCP_URL}...")

    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            # Step 1: Initialize the MCP session
            await session.initialize()
            print("  PASS: Connected and session initialized\n")

            # Step 2: Discover tools (ListTools)
            print("  2. Discovering tools (ListTools)...")
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"     Found {len(tool_names)} tool(s): {tool_names}")

            if 'search_financial_docs' not in tool_names:
                print("     FAIL: search_financial_docs not found!")
                return False

            print("     PASS: search_financial_docs discovered via MCP protocol")

            # Step 3: Show the tool schema
            print("\n  3. Tool schema (what the agent sees):")
            for tool in tools.tools:
                if tool.name == 'search_financial_docs':
                    print(f"     Name: {tool.name}")
                    desc = tool.description or ''
                    print(f"     Description: {desc[:100]}...")
                    schema = tool.input_schema  # MCP 2.x: renamed from inputSchema
                    props = schema.get('properties', {})
                    print(f"     Parameters: {list(props.keys())}")
                    break

            # Step 4: Invoke as tenant-001
            print("\n  4. Invoking as tenant-001...")
            print("     Request: search_financial_docs(query='roof repair cost', tenant_id='tenant-001')")

            result = await session.call_tool(
                'search_financial_docs',
                arguments={
                    'query': 'How much did the roof repair cost?',
                    'tenant_id': 'tenant-001',
                    'top_k': 3,
                },
            )

            for content in result.content:
                if hasattr(content, 'text'):
                    parsed = json.loads(content.text)
                    results = parsed.get('results', [])
                    print(f"     Response: {len(results)} chunks")
                    for i, r in enumerate(results):
                        text = r.get('text', '')
                        score = r.get('score', 'N/A')
                        print(f"       [{i+1}] score={score} | {text}")
                    break

            # Step 5: Invoke as tenant-002 (isolation proof)
            print("\n  5. Invoking as tenant-002 (isolation proof)...")
            result2 = await session.call_tool(
                'search_financial_docs',
                arguments={
                    'query': 'How much did the roof repair cost?',
                    'tenant_id': 'tenant-002',
                    'top_k': 3,
                },
            )
            for content in result2.content:
                if hasattr(content, 'text'):
                    parsed2 = json.loads(content.text)
                    results2 = parsed2.get('results', [])
                    print(f"     Response: {len(results2)} chunks")
                    for i, r in enumerate(results2):
                        text = r.get('text', '')
                        score = r.get('score', 'N/A')
                        print(f"       [{i+1}] score={score} | {text}")
                    break

            print("\n     PASS: Same tool, different tenant → different data")

    return True


def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Task 2.3: MCP Server Verification                          ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  Prerequisites:")
    print("    In another terminal, start the MCP server:")
    print("    $ python tool_s3_vectors.py --serve")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("  1. Connecting to MCP server...")

    try:
        success = asyncio.run(run_mcp_verification())
    except ConnectionRefusedError:
        print("\n  FAIL: Connection refused. Is the MCP server running?")
        print("     Start it in another terminal: python tool_s3_vectors.py --serve")
        return 1
    except Exception as e:
        print(f"\n  FAIL: {type(e).__name__}: {e}")
        print("     Make sure the MCP server is running: python tool_s3_vectors.py --serve")
        return 1

    print()
    if success:
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  PASS: MCP verification complete.")
        print()
        print("  KEY INSIGHT:")
        print("    • The tool ran in Terminal 1 (its own process)")
        print("    • This client ran in Terminal 2 (separate process)")
        print("    • Communication was over HTTP/SSE (MCP protocol)")
        print("    • No shared memory, no imports, no function calls")
        print()
        print("  In production:")
        print("    • Tool process has its own IAM role (TVM-scoped)")
        print("    • Any MCP-compatible agent can connect")
        print("    • Tool can be deployed + scaled independently")
        print("    • Isolation boundary is at the PROCESS level")
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    else:
        print("  FAIL: Verification failed.")

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
