"""Independent official-SDK connector test; run explicitly in CI with mcp==2.2.0."""
import asyncio
from threading import Thread

import pytest
from werkzeug.serving import make_server

from test_agent_planner import core, grant
from smartplate.app import create_app
from smartplate import config


def test_official_sdk_negotiates_and_calls_scoped_tools(core, monkeypatch):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    import os, sys
    from pathlib import Path
    from mcp.client.streamable_http import streamable_http_client
    import httpx2
    owner, fake = core
    credentials = grant(owner)
    server = make_server('127.0.0.1',0,create_app(),threaded=True)
    thread = Thread(target=server.serve_forever,daemon=True)
    url = f'http://127.0.0.1:{server.server_port}/mcp'
    monkeypatch.setattr(config,'PUBLIC_URL',url[:-4])
    thread.start()
    async def check():
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+credentials['token']}) as http:
            async with Client(streamable_http_client(url,http_client=http),read_timeout_seconds=15) as peer:
                assert peer.protocol_version == '2025-11-25'
                tools = await peer.list_tools()
                assert 'plan_meals' in {t.name for t in tools.tools}
                result = await peer.call_tool('get_menu',{'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan'})
                assert not result.is_error and result.structured_content['items'][0]['id']=='m0'
                result = await peer.call_tool('get_food_memory',{})
                assert not result.is_error and result.structured_content == {'items':{}}
        parameters = StdioServerParameters(command=sys.executable,args=[str(Path('scripts/agent_bridge.py').resolve())],
            env={**os.environ,'SMARTPLATE_MCP_URL':url,'SMARTPLATE_AGENT_TOKEN':credentials['token']})
        async with Client(parameters,read_timeout_seconds=15) as peer:
            result=await peer.call_tool('get_food_memory',{})
            assert not result.is_error and result.structured_content == {'items':{}}
    try:
        asyncio.run(check())
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()
