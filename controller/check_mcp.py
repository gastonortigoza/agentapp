import asyncio
import json
import sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def check():
    params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('mcp_local.py'))])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            names={t.name for t in tools.tools}
            assert names=={'list_documents','read_document','git_status','search_documents'}
            good=await session.call_tool('read_document',{'name':'social.md'})
            assert not good.isError
            bad=await session.call_tool('read_document',{'name':'../../open-webui/data/webui.db'})
            assert bad.isError
            write=await session.call_tool('git_status',{'path':'E:/IA/workspace','action':'push'})
            assert write.isError
            result={'status':'pass','transport':'stdio','tools':sorted(names),'path_escape':'blocked','writes':'not_exposed','remote_connectors':'not_configured'}
            Path('E:/IA/logs/mcp-validation.json').write_text(json.dumps(result,indent=2))
            print(json.dumps(result))

if __name__=='__main__': asyncio.run(check())
