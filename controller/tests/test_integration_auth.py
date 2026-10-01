import asyncio
import json
import os
import time

import httpx
import pytest
from mcp.shared.auth import OAuthToken, OAuthClientInformationFull, OAuthMetadata

import integration_auth as auth

ENDPOINT = 'https://mcp.linear.app/mcp/readonly'


def store(tmp_path):
    return auth.SecureStorage('linear', ENDPOINT, tmp_path)


async def provision(storage, expired=False):
    await storage.set_client_info(OAuthClientInformationFull(
        client_id='synthetic-client', token_endpoint_auth_method='none',
        redirect_uris=['http://127.0.0.1:43872/callback']))
    await storage.set_tokens(OAuthToken(access_token='synthetic-old', token_type='Bearer',
                                       refresh_token='synthetic-refresh', scope='read', expires_in=100))
    metadata = OAuthMetadata(issuer='https://mcp.linear.app',
                             authorization_endpoint='https://mcp.linear.app/authorize',
                             token_endpoint='https://mcp.linear.app/token', response_types_supported=['code'])
    storage.update(oauth_metadata=metadata.model_dump(mode='json'),
                   expires_at=time.time() - 100 if expired else time.time() + 100)


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI acceptance')
def test_dpapi_roundtrip_no_plaintext_and_endpoint_binding(tmp_path):
    storage = store(tmp_path)
    asyncio.run(provision(storage))
    assert b'synthetic' not in storage.path.read_bytes()
    assert asyncio.run(store(tmp_path).get_tokens()).access_token == 'synthetic-old'
    other = auth.SecureStorage('linear', 'https://mcp.linear.app/mcp', tmp_path)
    with pytest.raises(auth.AuthenticationRequired):
        other.read()


def test_failed_encryption_preserves_previous_store(tmp_path, monkeypatch):
    storage = store(tmp_path)
    asyncio.run(provision(storage))
    prior = storage.path.read_bytes()
    original = auth.crypt
    def fail(data, decrypt=False):
        if not decrypt:
            raise OSError('synthetic failure')
        return original(data, decrypt=True)
    monkeypatch.setattr(auth, 'crypt', fail)
    with pytest.raises(OSError):
        storage.update(tokens={})
    assert storage.path.read_bytes() == prior


@pytest.mark.parametrize('url', ['http://mcp.linear.app/token', 'https://evil.test/token',
                                'https://mcp.linear.app.evil.test/token',
                                'https://secret@mcp.linear.app/token', 'https://mcp.linear.app:8443/token'])
def test_oauth_destination_denied(url):
    with pytest.raises(auth.AuthenticationRequired):
        auth.check_url('linear', url)


def test_refresh_after_restart_uses_persisted_expiry_and_metadata(tmp_path):
    async def scenario():
        storage = store(tmp_path)
        await provision(storage, expired=True)
        requests = []
        def remote(request):
            requests.append(request)
            if request.url.path == '/token':
                assert b'grant_type=refresh_token' in request.content
                return httpx.Response(200, json={'access_token': 'synthetic-new', 'token_type': 'Bearer',
                    'refresh_token': 'synthetic-refresh-new', 'expires_in': 3600, 'scope': 'read'})
            assert request.headers['Authorization'] == 'Bearer synthetic-new'
            return httpx.Response(200, json={'ok': True})
        provider = auth.LocalOAuth('linear', ENDPOINT, store(tmp_path))
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(remote)) as client:
            assert (await client.get(ENDPOINT)).status_code == 200
        assert [r.url.path for r in requests] == ['/token', '/mcp/readonly']
        assert (await store(tmp_path).get_tokens()).refresh_token == 'synthetic-refresh-new'
        assert store(tmp_path).read()['expires_at'] > time.time()
    asyncio.run(scenario())


def test_rejected_refresh_never_opens_consent_noninteractive(tmp_path):
    async def scenario():
        storage = store(tmp_path)
        await provision(storage, expired=True)
        requests = []
        def remote(request):
            requests.append(request.url.path)
            return httpx.Response(401, json={'error': 'invalid_grant'})
        provider = auth.LocalOAuth('linear', ENDPOINT, storage)
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(remote)) as client:
            with pytest.raises(auth.AuthenticationRequired):
                await client.get(ENDPOINT)
        assert requests == ['/token']
    asyncio.run(scenario())


def test_missing_credentials_fail_before_network(tmp_path):
    async def scenario():
        def remote(request):
            pytest.fail('No request should be sent')
        provider = auth.LocalOAuth('linear', ENDPOINT, store(tmp_path))
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(remote)) as client:
            with pytest.raises(auth.AuthenticationRequired):
                await client.get(ENDPOINT)
    asyncio.run(scenario())


def test_broadened_scope_is_not_persisted(tmp_path):
    async def scenario():
        storage = store(tmp_path)
        with pytest.raises(auth.AuthenticationRequired):
            await storage.set_tokens(OAuthToken(access_token='synthetic', token_type='Bearer', scope='read write'))
        assert not storage.path.exists()
    asyncio.run(scenario())


def test_write_grant_is_separate_and_rejects_admin_scope(tmp_path):
    async def scenario():
        reader = store(tmp_path)
        await provision(reader)
        writer = auth.SecureStorage('linear_write', 'https://mcp.linear.app/mcp', tmp_path)
        await writer.set_tokens(OAuthToken(access_token='synthetic-writer', token_type='Bearer', scope='read write'))
        assert writer.path != reader.path
        assert (await reader.get_tokens()).access_token == 'synthetic-old'
        with pytest.raises(auth.AuthenticationRequired):
            await writer.set_tokens(OAuthToken(access_token='bad', token_type='Bearer', scope='read write admin'))
        assert (await writer.get_tokens()).access_token == 'synthetic-writer'
    asyncio.run(scenario())


def test_hostile_discovery_cannot_redirect_authentication(tmp_path):
    async def scenario():
        seen = []
        def remote(request):
            seen.append(request.url.host)
            if request.url.path == '/mcp/readonly':
                return httpx.Response(401, headers={'WWW-Authenticate':
                    'Bearer resource_metadata="https://evil.test/.well-known/oauth-protected-resource"'})
            pytest.fail('Hostile metadata URL must not be requested')
        provider = auth.LocalOAuth('linear', ENDPOINT, store(tmp_path), interactive=True)
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(remote)) as client:
            with pytest.raises(auth.AuthenticationRequired):
                await client.get(ENDPOINT)
        assert seen == ['mcp.linear.app']
    asyncio.run(scenario())


def test_callback_accepts_only_bound_state_and_removes_auth_file(tmp_path, monkeypatch):
    monkeypatch.setitem(auth.PORTS, 'linear', 0)
    async def scenario():
        callback = auth.Callback('linear', tmp_path)
        with callback.listen():
            await callback.redirect('https://linear.app/oauth/authorize?state=expected-state')
            port = callback.server.server_port
            async with httpx.AsyncClient(trust_env=False) as client:
                bad = await client.get(f'http://127.0.0.1:{port}/callback?code=synthetic&state=wrong')
                assert bad.status_code == 400 and not callback.result.done()
                good = await client.get(f'http://127.0.0.1:{port}/callback?code=synthetic&state=expected-state')
                assert good.status_code == 200
                assert 'synthetic' not in good.text
            assert await callback.wait() == ('synthetic', 'expected-state')
        assert not callback.request_path.exists()
    asyncio.run(scenario())
