"""Read-only AgentApp inventory over an owned, host-pinned SSH tunnel."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import controller_gate
from backup import crypt
from integration_auth import SECRET_DIR, silence_auth_logs
from integration_cli import ConnectionBlocked, failure_reason, unique_pairs
from worker_lock import worker_lock

ROOT = Path(__file__).resolve().parent
HOST = '179.198.111.129'
HOST_KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDfguRzgo0dl7RTtLjcENBRdCxDK/P7jeQzBRi4kzCtq'
KEY = Path('E:/IA/credentials/coolify-tunnel/agentapp_ed25519')
EXPIRES = datetime(2026, 9, 26, 20, 55, 59, tzinfo=timezone.utc)
PROJECT = 'ecoy01g724tjbobuz2dzikaw'
ENVIRONMENT = 'uwlwcitmahaeqcjzncgpr7bq'
SERVER = 'e5t1ifwljxwq8k0m79vjbzhq'
CALLS = {
    'get_current_team': {},
    'get_project': {'uuid': PROJECT},
    'get_environment': {'project_uuid': PROJECT, 'environment_name_or_uuid': ENVIRONMENT},
    'get_server': {'uuid': SERVER},
}


def check_expiry(now=None):
    if (now or datetime.now(timezone.utc)) >= EXPIRES:
        raise ConnectionBlocked('coolify_tunnel_authorization_expired')


def read_token(path=SECRET_DIR / 'coolify.dpapi'):
    if not path.exists() or path.stat().st_size > 128000:
        raise ConnectionBlocked('local_credential_missing')
    data = json.loads(crypt(path.read_bytes(), decrypt=True), object_pairs_hook=unique_pairs)
    if (data.get('endpoint') != 'http://127.0.0.1:43880/mcp'
            or data.get('scope') != 'read' or data.get('team') != 'Root Team'
            or not isinstance(data.get('token'), str)
            or not re.fullmatch(r'\d+\|[A-Za-z0-9]{20,}', data['token'])):
        raise ConnectionBlocked('coolify_credential_scope_mismatch')
    return data['token']


def listener_owned(port, pid):
    # Compare the Windows socket owner to the child we started, never trust an
    # arbitrary pre-existing loopback listener. Arguments are controller ints.
    command = (f"@(Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
               "-ErrorAction SilentlyContinue | Select-Object LocalAddress,OwningProcess) | ConvertTo-Json -Compress")
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                          capture_output=True, text=True, timeout=8,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode or not proc.stdout.strip():
        return False
    rows = json.loads(proc.stdout)
    if isinstance(rows, dict): rows = [rows]
    return bool(rows) and all(r['LocalAddress'] == '127.0.0.1' and r['OwningProcess'] == pid for r in rows)


@asynccontextmanager
async def tunnel():
    check_expiry()
    if os.name != 'nt' or not KEY.is_file():
        raise ConnectionBlocked('coolify_ssh_key_missing')
    ssh = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/OpenSSH/ssh.exe'
    if not ssh.is_file(): raise ConnectionBlocked('coolify_ssh_unavailable')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='agentapp-coolify-') as temporary:
        hosts = Path(temporary) / 'known_hosts'
        hosts.write_text(HOST + ' ' + HOST_KEY + '\n', encoding='ascii')
        args = [str(ssh), '-F', 'NUL', '-N', '-T', '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
                '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
                '-o', 'GlobalKnownHostsFile=NUL', '-o', 'UserKnownHostsFile='+str(hosts),
                '-i', str(KEY), '-L', f'127.0.0.1:{port}:127.0.0.1:8000', 'root@'+HOST]
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(20):
                if proc.poll() is not None: raise ConnectionBlocked('coolify_ssh_connection_failed')
                if await asyncio.to_thread(listener_owned, port, proc.pid): break
                await asyncio.sleep(.25)
            else: raise ConnectionBlocked('coolify_tunnel_not_verified')
            check_expiry()
            yield f'http://127.0.0.1:{port}/mcp', proc, port
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: await asyncio.to_thread(proc.wait, 5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    await asyncio.to_thread(proc.wait, 5)


class ReadOnlyMCP:
    def __init__(self, session, schemas):
        self.session, self.schemas = session, schemas

    async def call(self, name, args):
        if name not in CALLS or args != CALLS[name]:
            raise ConnectionBlocked('coolify_operation_outside_allowlist')
        check_expiry()
        if name not in self.schemas: raise ConnectionBlocked('coolify_capability_missing')
        jsonschema.validate(args, self.schemas[name])
        result = await self.session.call_tool(name, args)
        if result.isError: raise ConnectionBlocked('coolify_read_rejected')
        texts = [c.text for c in result.content if c.type == 'text']
        if len(texts) != 1 or len(texts[0]) > 1000000:
            raise ConnectionBlocked('coolify_response_invalid')
        return json.loads(texts[0], object_pairs_hook=unique_pairs)['data']


async def collect(remote):
    team = await remote.call('get_current_team', {})
    if team['name'] != 'Root Team': raise ConnectionBlocked('coolify_team_mismatch')
    project = await remote.call('get_project', CALLS['get_project'])
    if project['uuid'] != PROJECT or project['name'] != 'AgentApp':
        raise ConnectionBlocked('coolify_project_mismatch')
    env = await remote.call('get_environment', CALLS['get_environment'])
    if env['uuid'] != ENVIRONMENT or env['project']['uuid'] != PROJECT:
        raise ConnectionBlocked('coolify_environment_mismatch')
    server = await remote.call('get_server', CALLS['get_server'])
    if server['uuid'] != SERVER: raise ConnectionBlocked('coolify_server_mismatch')
    counts = {}
    for kind in ('applications', 'services', 'databases'):
        value = project['counts'][kind]
        if type(value) is not int or value < 0:
            raise ConnectionBlocked('coolify_response_invalid')
        counts[kind] = value
    # Never return arbitrary descriptions, settings, environment values or logs.
    return {'service': 'coolify', 'project': PROJECT, 'environment': ENVIRONMENT,
            'resource_counts': counts,
            'server_reachable': server['is_reachable'] is True,
            'server_usable': server['is_usable'] is True,
            'resource_access_verified': True, 'live_writes_enabled': False,
            'transport': 'owned_host_pinned_ssh_tunnel', 'expires_at': EXPIRES.isoformat()}


async def inventory():
    controller_gate.require_green()
    check_expiry()
    silence_auth_logs()
    with worker_lock(ROOT / '.state/oauth-lock', 'coolify') as acquired:
        if not acquired: raise ConnectionBlocked('connector_busy')
        remaining = (EXPIRES - datetime.now(timezone.utc)).total_seconds()
        async with asyncio.timeout(min(90, max(.001, remaining))):
            async with tunnel() as (endpoint, process, port):
                token = read_token()
                async def guard(request):
                    check_expiry()
                    if (str(request.url) != endpoint or process.poll() is not None
                            or not await asyncio.to_thread(listener_owned, port, process.pid)):
                        raise ConnectionBlocked('coolify_tunnel_not_verified')
                async with httpx.AsyncClient(headers={'Authorization': 'Bearer '+token},
                        trust_env=False, follow_redirects=False, timeout=15,
                        event_hooks={'request': [guard]}) as client:
                    async with streamable_http_client(endpoint, http_client=client) as (r, w, _):
                        async with ClientSession(r, w) as session:
                            await session.initialize()
                            schemas, cursor = {}, None
                            for _ in range(20):
                                page = await session.list_tools(cursor=cursor)
                                schemas.update({t.name:t.inputSchema for t in page.tools if t.name in CALLS})
                                cursor = page.nextCursor
                                if not cursor: break
                            else: raise ConnectionBlocked('coolify_capability_list_incomplete')
                            return await collect(ReadOnlyMCP(session, schemas))


def main(argv=None):
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try: result = asyncio.run(inventory())
    except Exception as exc:
        print(json.dumps({'status':'blocked', 'reason':failure_reason(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0
