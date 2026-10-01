"""Local-runtime connection inventory. No write tools are exposed."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import controller_gate

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config/phase2.json'


class ConnectionBlocked(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def failure_reason(exc):
    from integration_auth import AuthenticationRequired
    if isinstance(exc, AuthenticationRequired):
        return 'local_oauth_login_required'
    if isinstance(exc, ConnectionBlocked):
        return exc.reason
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return {401: 'credential_expired_or_rejected', 403: 'access_denied'}.get(
            exc.response.status_code, 'remote_http_failure')
    if isinstance(exc, BaseExceptionGroup):
        reasons = [failure_reason(e) for e in exc.exceptions]
        for reason in reasons:
            if reason.startswith('coolify_'):
                return reason
        for reason in ('credential_expired_or_rejected', 'access_denied', 'local_oauth_login_required'):
            if reason in reasons:
                return reason
    return 'connection_not_verified'


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key')
        result[key] = value
    return result


def load(path=CONFIG):
    data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
    if not isinstance(data, dict) or set(data) != {'schema_version', 'notion', 'linear', 'coolify'}:
        raise ValueError('Invalid connector configuration')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Unsupported connector schema')
    for name in ('notion', 'linear', 'coolify'):
        cfg = data[name]
        if not isinstance(cfg, dict) or set(cfg) != {'enabled', 'endpoint', 'token_env', 'resources'}:
            raise ValueError('Invalid connector fields')
        if type(cfg['enabled']) is not bool:
            raise ValueError('Invalid enable flag')
        if not isinstance(cfg['token_env'], str) or not re.fullmatch(r'AGENT_[A-Z_]{1,64}', cfg['token_env']):
            raise ValueError('Invalid credential reference')
        if not isinstance(cfg['resources'], list) or any(not isinstance(r, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', r) for r in cfg['resources']):
            raise ValueError('Invalid resource allowlist')
        endpoint = cfg['endpoint']
        if endpoint is None and name == 'coolify' and not cfg['enabled']:
            continue
        if not isinstance(endpoint, str):
            raise ValueError('Missing endpoint')
        parts = urlsplit(endpoint)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Expected HTTPS endpoint without credentials')
        official = {'notion': {'https://mcp.notion.com/mcp'},
                    'linear': {'https://mcp.linear.app/mcp/readonly'}}
        if name in official and endpoint not in official[name]:
            raise ValueError('Unexpected service endpoint')
        if name == 'coolify' and parts.path != '/mcp':
            raise ValueError('Expected instance MCP endpoint')
    return data


def status(path=CONFIG):
    data = load(path)
    from integration_auth import SECRET_DIR
    return {'scope': 'local_runtime', 'live_writes_enabled': False,
            'connectors': {name: {'enabled': data[name]['enabled'],
                'endpoint_configured': bool(data[name]['endpoint']),
                'credential_available': bool(os.environ.get(data[name]['token_env'])),
                'oauth_store_present': (SECRET_DIR / (name + '.dpapi')).exists(),
                'authorized_resource_count': len(data[name]['resources']),
                'acceptance': 'pending'} for name in ('notion', 'linear', 'coolify')}}


async def probe(name, path=CONFIG, interactive=False):
    """Only initialize/list_tools; no content search, writes, refresh or redirects."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from integration_auth import Callback, LocalOAuth, SecureStorage, silence_auth_logs
    from worker_lock import worker_lock
    from contextlib import nullcontext, ExitStack
    silence_auth_logs()
    cfg = load(path)[name]
    if not cfg['enabled'] and not interactive:
        raise ConnectionBlocked('connector_disabled')
    controller_gate.require_green()
    token = os.environ.get(cfg['token_env'])
    if not token and name == 'coolify':
        raise ConnectionBlocked('local_credential_missing')
    if token and ('\r' in token or '\n' in token):
        raise ValueError('Invalid credential format')
    callbacks = Callback(name) if interactive else None
    context = callbacks.listen() if interactive else nullcontext()
    state = ROOT / '.state'
    state.mkdir(exist_ok=True)
    with worker_lock(state / 'oauth-lock', name) as acquired, ExitStack() as stack:
        if not acquired:
            raise ConnectionBlocked('connector_busy')
        stack.enter_context(context)
        auth = None
        headers = {}
        if token and not interactive:
            headers = {'Authorization': 'Bearer ' + token}
        else:
            auth = LocalOAuth(name, cfg['endpoint'], SecureStorage(name, cfg['endpoint']),
                              interactive=interactive,
                              redirect=callbacks.redirect if callbacks else None,
                              callback=callbacks.wait if callbacks else None)
        async with asyncio.timeout(360 if interactive else 45):
          async with httpx.AsyncClient(headers=headers, auth=auth, trust_env=False,
                                       timeout=15, follow_redirects=False) as client:
            async with streamable_http_client(cfg['endpoint'], http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    discovered = []
                    cursor = None
                    for _ in range(20):
                        page = await session.list_tools(cursor=cursor)
                        for tool in page.tools:
                            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool.name):
                                raise ValueError('Unexpected tool identity')
                            discovered.append({'name': tool.name, 'schema_sha256': hashlib.sha256(
                                json.dumps(tool.inputSchema, sort_keys=True).encode()).hexdigest()})
                        cursor = page.nextCursor
                        if not cursor:
                            break
                    else:
                        raise ValueError('Tool inventory pagination limit exceeded')
    return {'service': name, 'scope': 'capabilities_only', 'tools': discovered,
            'server_version_sha256': hashlib.sha256(initialized.serverInfo.version.encode()).hexdigest(),
            'resource_access_verified': False, 'live_writes_enabled': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('integration-status')
    p = sub.add_parser('integration-probe')
    p.add_argument('service', choices=['notion', 'linear', 'coolify'])
    login = sub.add_parser('integration-login')
    login.add_argument('service', choices=['notion', 'linear'])
    args = parser.parse_args(argv)
    try:
        result = status() if args.command == 'integration-status' else asyncio.run(
            probe(args.service, interactive=args.command == 'integration-login'))
    except Exception as exc:
        # Never print remote bodies, headers, OAuth responses or exception reprs.
        print(json.dumps({'status': 'blocked', 'reason': failure_reason(exc),
                          'next_action': 'Check configuration, local authentication and access; no write was sent.'}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
