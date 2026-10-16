from typing import Any

import mcp_types
import pytest
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from fastmcp import Client, FastMCP
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.utilities.asgi_transport import run_asgi_lifespan
from fastmcp.utilities.tests import ASGIServer


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_proxy_preserves_non_object_tool_outputs_across_protocol_versions(
    mode: str,
) -> None:
    schemas = {
        "numbers": {"type": "array", "items": {"type": "integer"}},
        "flag": {"type": "boolean"},
        "record": {"type": "object", "properties": {"value": {"type": "integer"}}},
    }
    values = {"numbers": [1, 2, 3], "flag": False, "record": {"value": 1}}

    async def list_tools(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name=name,
                    input_schema={"type": "object", "properties": {}},
                    output_schema=schema,
                )
                for name, schema in schemas.items()
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        value = values[params.name]
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(value))],
            structured_content=value,
            _meta={"source": "backend"},
        )

    backend = Server("backend", on_list_tools=list_tools, on_call_tool=call_tool)
    app = backend.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1"]),
    )
    async with run_asgi_lifespan(app):
        upstream = ASGIServer("http://127.0.0.1/mcp", app, "http")
        proxy = FastMCP("proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(upstream.transport(), mode="auto"))
        )

        async with Client(proxy, mode=mode) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            numbers = await client.call_tool("numbers", {})
            flag = await client.call_tool("flag", {})
            record = await client.call_tool("record", {})

    assert numbers.data == [1, 2, 3]
    assert flag.data is False
    assert record.data.value == 1
    assert numbers.meta["source"] == "backend"
    assert flag.meta["source"] == "backend"
    assert record.structured_content == {"value": 1}
    assert tools["record"].output_schema == schemas["record"]
    if mode == "auto":
        assert tools["numbers"].output_schema == schemas["numbers"]
        assert numbers.structured_content == [1, 2, 3]
        assert flag.structured_content is False
    else:
        assert tools["numbers"].output_schema["type"] == "object"
        assert (
            tools["numbers"].output_schema["properties"]["result"] == schemas["numbers"]
        )
        assert numbers.structured_content == {"result": [1, 2, 3]}
        assert flag.structured_content == {"result": False}
