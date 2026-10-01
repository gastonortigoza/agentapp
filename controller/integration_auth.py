"""MCP OAuth for the trusted Windows controller, with DPAPI token storage.

Interactive consent is a separate explicit command. Routine probes may refresh
an existing grant, but never start consent or broaden its permissions silently.
"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import secrets
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (OAuthClientInformationFull, OAuthClientMetadata,
                             OAuthMetadata, OAuthToken, ProtectedResourceMetadata)
from backup import crypt

SECRET_DIR = Path('E:/IA/credentials/phase2-oauth')
AUTH_DIR = Path(__file__).resolve().parent / '.state/phase2-auth'
HOSTS = {'notion': {'mcp.notion.com', 'api.notion.com', 'www.notion.so', 'notion.so'},
         'linear': {'mcp.linear.app', 'api.linear.app', 'linear.app'}}
PORTS = {'notion': 43871, 'linear': 43872}
HOSTS['linear_write'] = HOSTS['linear']
PORTS['linear_write'] = 43873
SCOPES = {'linear': 'read', 'linear_write': 'read write'}


class AuthenticationRequired(ValueError):
    pass


def check_url(service, url):
    p = urlsplit(str(url))
    if (p.scheme != 'https' or p.hostname not in HOSTS[service]
            or p.port not in {None, 443} or p.username or p.password or p.fragment):
        raise AuthenticationRequired('OAuth destination not authorized')


class SecureStorage:
    def __init__(self, service, endpoint, directory=SECRET_DIR):
        if service not in HOSTS:
            raise ValueError('Unsupported OAuth service')
        check_url(service, endpoint)
        self.service = service
        self.endpoint = endpoint
        self.path = Path(directory) / (service + '.dpapi')

    def read(self):
        if not self.path.exists():
            return {'endpoint': self.endpoint}
        if self.path.stat().st_size > 128000:
            raise AuthenticationRequired('Invalid credential store')
        data = json.loads(crypt(self.path.read_bytes(), decrypt=True))
        if data.get('endpoint') != self.endpoint:
            raise AuthenticationRequired('Credential belongs to another endpoint')
        return data

    def update(self, **values):
        data = self.read()
        data.update(values)
        encrypted = crypt(json.dumps(data, allow_nan=False).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    async def get_tokens(self):
        data = self.read().get('tokens')
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens):
        if self.service in SCOPES and tokens.scope and set(tokens.scope.split()) != set(SCOPES[self.service].split()):
            raise AuthenticationRequired('Unexpected Linear OAuth scope')
        self.update(tokens=tokens.model_dump(mode='json'),
                    expires_at=time.time() + tokens.expires_in if tokens.expires_in is not None else None)

    async def get_client_info(self):
        data = self.read().get('client')
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info):
        self.update(client=client_info.model_dump(mode='json'))


class LocalOAuth(OAuthClientProvider):
    def __init__(self, service, endpoint, storage, interactive=False, redirect=None, callback=None):
        self.service = service
        self.secure_storage = storage
        self.interactive = interactive
        metadata = OAuthClientMetadata(
            client_name='AgentApp Ticket Controller' if service == 'linear_write' else 'AgentApp Local Controller',
            redirect_uris=[f'http://127.0.0.1:{PORTS[service]}/callback'],
            grant_types=['authorization_code', 'refresh_token'], response_types=['code'],
            token_endpoint_auth_method='none', scope=SCOPES.get(service))
        super().__init__(endpoint, metadata, storage, redirect, callback, timeout=300)

    async def _initialize(self):
        await super()._initialize()
        saved = self.secure_storage.read()
        self.context.token_expiry_time = saved.get('expires_at')
        for key, model in [('oauth_metadata', OAuthMetadata),
                           ('protected_resource_metadata', ProtectedResourceMetadata)]:
            if saved.get(key):
                setattr(self.context, key, model.model_validate(saved[key]))
        self.context.auth_server_url = saved.get('auth_server_url')
        if not self.interactive and not self.context.current_tokens:
            raise AuthenticationRequired('Local OAuth login required')
        if (not self.interactive and not self.context.is_token_valid()
                and not self.context.can_refresh_token()):
            raise AuthenticationRequired('Credential expired; local OAuth login required')

    async def _perform_authorization(self):
        if not self.interactive:
            raise AuthenticationRequired('Interactive consent required')
        # The SDK discovers all advertised scopes; pin the actual Linear grant.
        if self.service in SCOPES:
            self.context.client_metadata.scope = SCOPES[self.service]
        return await super()._perform_authorization()

    def save_metadata(self):
        self.secure_storage.update(
            oauth_metadata=self.context.oauth_metadata.model_dump(mode='json') if self.context.oauth_metadata else None,
            protected_resource_metadata=self.context.protected_resource_metadata.model_dump(mode='json') if self.context.protected_resource_metadata else None,
            auth_server_url=self.context.auth_server_url)

    async def async_auth_flow(self, request):
        flow = super().async_auth_flow(request)
        try:
            outgoing = await anext(flow)
            while True:
                check_url(self.service, outgoing.url)
                response = yield outgoing
                if not self.interactive and response.status_code in {401, 403}:
                    raise AuthenticationRequired('Credential rejected or access denied; interactive login required')
                outgoing = await flow.asend(response)
                if self.context.current_tokens:
                    self.save_metadata()
        except StopAsyncIteration:
            if self.context.current_tokens:
                self.save_metadata()
        finally:
            await flow.aclose()


class Callback:
    """Loopback callback; state checked before accepting, never logged to stdout."""
    def __init__(self, service, directory=AUTH_DIR):
        self.service = service
        self.directory = Path(directory)
        self.expected_state = None
        self.result = None
        self.server = None

    @property
    def request_path(self):
        return self.directory / (self.service + '-authorization.json')

    async def redirect(self, url):
        check_url(self.service, url)
        params = parse_qs(urlsplit(url).query)
        state = params.get('state', [])
        if len(state) != 1 or not state[0]:
            raise AuthenticationRequired('OAuth state missing')
        self.expected_state = state[0]
        self.directory.mkdir(parents=True, exist_ok=True)
        # Contains only a consent URL, client identifier, PKCE challenge and state;
        # never an access token, code verifier, callback code or client secret.
        self.request_path.write_text(json.dumps({'service': self.service, 'authorization_url': url}), encoding='utf-8')
        print(json.dumps({'status': 'awaiting_oauth_consent', 'service': self.service,
                          'authorization_file': str(self.request_path)}), flush=True)

    async def wait(self):
        return await asyncio.wait_for(self.result, timeout=300)

    @contextmanager
    def listen(self):
        loop = asyncio.get_running_loop()
        self.result = loop.create_future()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                p = urlsplit(self.path)
                params = parse_qs(p.query)
                state = params.get('state', [])
                code = params.get('code', [])
                valid = (self.headers.get('Host') == f'127.0.0.1:{owner.server.server_port}'
                         and p.path == '/callback' and len(state) == len(code) == 1
                         and len(code[0]) <= 8192 and owner.expected_state
                         and secrets.compare_digest(state[0], owner.expected_state))
                self.send_response(200 if valid else 400)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                self.wfile.write(b'Authorization received. You can return to the task.' if valid else b'Invalid callback.')
                if valid:
                    def complete():
                        if not owner.result.done():
                            owner.result.set_result((code[0], state[0]))
                    loop.call_soon_threadsafe(complete)
        self.server = HTTPServer(('127.0.0.1', PORTS[self.service]), Handler)
        self.server.timeout = 1
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=2)
            self.request_path.unlink(missing_ok=True)


def silence_auth_logs():
    # SDK exceptions can contain response bodies. The CLI emits only safe codes.
    for name in ('mcp.client.auth', 'mcp.client.streamable_http', 'httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
