"""Concrete local test policy. Documentary manifests never grant execution."""
from pathlib import Path
import copy
import hashlib
import io
import json
import re
import tarfile

import manifest
import phase3_prepare as preparation

NODE = 'node@sha256:0d130e2ee18e88e1561375276daced6bff032539200173f2daf48c2e33f38ff5'
POSTGRES = 'postgres@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722'
BROWSER = 'mcr.microsoft.com/playwright@sha256:42f02d323c310069b4d54c94bd91a608966a486c3492c48ae0b1cea747ec5ca2'
STAGES = {'install': ('.', ['npm', 'ci', '--offline', '--ignore-scripts', '--no-audit', '--fund=false', '--cache', '/cache', '--engine-strict'], 180),
          'migrate': ('backend', ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'age50_test'], 120),
          'unit': ('.', ['npm', 'run', 'test:unit'], 180),
          'be_build': ('backend', ['npm', 'run', 'build'], 180),
          'fe_build': ('frontend', ['npm', 'run', 'build'], 180),
          'be': ('backend', ['npm', 'run', 'dev'], 120),
          'fe': ('frontend', ['npm', 'run', 'dev', '--', '--host', '127.0.0.1'], 120),
          'e2e': ('frontend', ['npm', 'run', 'test:e2e'], 300)}
SCRIPTS = {'root': {'test:unit': 'npm run test:unit --workspace backend && npm run test:unit --workspace frontend'},
           'backend': {'dev': 'tsx src/server.ts', 'build': 'tsc --noEmit', 'test:unit': 'tsx --test tests/*.test.ts'},
           'frontend': {'dev': 'vite', 'build': 'tsc --noEmit && vite build', 'test:unit': 'tsx --test tests/*.test.ts',
                        'test:e2e': 'playwright test'}}
DEPENDENCIES = {'fastify': '5.6.1', 'pg': '8.16.3', 'react': '19.2.0', 'react-dom': '19.2.0',
                'vite': '6.4.1', 'typescript': '5.9.3', 'tsx': '4.20.6', '@types/node': '22.18.0',
                '@types/pg': '8.15.5', '@types/react': '19.2.0', '@types/react-dom': '19.2.0',
                '@playwright/test': '1.56.1'}
POLICY = {'schema': 'agentapp.phase3-local-sandbox-policy/1', 'scope': 'local_synthetic_directory_slice',
          'deployment': False, 'billing': False, 'remote_writes': False, 'host_mounts': False,
          'stage_order': ['migrate','install','be_build','fe_build','unit','be','fe','e2e'],
          'host_ports': False, 'network': 'shared_loopback_namespace_without_interfaces',
          'dependency_network': False, 'dependency_scripts': False,
          'total_seconds': 900, 'cleanup_seconds': 90, 'input_bytes': 8 * 1024 * 1024, 'cache_bytes': 100 * 1024 * 1024,
          'max_files': 200, 'max_packages': 200, 'cpu_total': 2, 'memory_total_mib': 4096,
          'node': {'image': NODE, 'cpu': '0.8', 'memory': '1024m', 'pids': '128', 'tmpfs': '512m'},
          'postgres': {'image': POSTGRES, 'cpu': '0.4', 'memory': '768m', 'pids': '64', 'tmpfs': '256m'},
          'browser': {'image': BROWSER, 'node_tool_version': '22.20.0', 'cpu': '0.8', 'memory': '2048m', 'pids': '256', 'tmpfs': '512m'}}


def packages(files):
    """Validate workspace scripts and exact pins; no lifecycle hooks or npm config."""
    result = {}
    for prefix, kind in (('', 'root'), ('backend/', 'backend'), ('frontend/', 'frontend')):
        key = prefix + 'package.json'
        pkg = manifest.parse(files[key].decode('utf-8'))
        allowed = {'name', 'version', 'private', 'type', 'scripts', 'engines', 'workspaces', 'dependencies', 'devDependencies'}
        if (set(pkg) - allowed or pkg.get('scripts') != SCRIPTS[kind] or pkg.get('private') is not True or
                pkg.get('engines') != {'node': '22.18.0', 'npm': '10.9.3'}):
            raise ValueError('Package scripts/engines denied: ' + key)
        deps = pkg.get('dependencies', {}) | pkg.get('devDependencies', {})
        if any(DEPENDENCIES.get(k) != v for k, v in deps.items()):
            raise ValueError('Dependency pin denied: ' + key)
        if kind == 'root' and (pkg.get('workspaces') != ['backend', 'frontend'] or deps):
            raise ValueError('Only the two bounded workspaces are supported')
        if kind != 'root' and 'workspaces' in pkg:raise ValueError('Nested workspaces denied')
        result[prefix.rstrip('/') or 'root'] = pkg
    lock = manifest.parse(files['package-lock.json'].decode('utf-8'))
    if lock.get('lockfileVersion') != 3 or not isinstance(lock.get('packages'), dict):
        raise ValueError('A concrete npm v3 lock is required')
    if len(lock['packages']) > POLICY['max_packages']:raise ValueError('Dependency count limit')
    for path, entry in lock['packages'].items():
        if path in ('', 'backend', 'frontend'):
            source = result[path or 'root']
            for field in ('dependencies', 'devDependencies', 'engines'):
                if entry.get(field, {}) != source.get(field, {}):raise ValueError('Package/lock drift: ' + path)
        elif path in ('node_modules/backend', 'node_modules/frontend'):
            if entry != {'resolved': path.removeprefix('node_modules/'), 'link': True}:raise ValueError('Workspace link denied')
        else:
            if (not re.fullmatch(r'node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+(?:/node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+)*', path)
                    or entry.get('link') or not re.fullmatch(r'https://registry\.npmjs\.org/[A-Za-z0-9_@./%+-]+\.tgz', entry.get('resolved', ''))
                    or not re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}', entry.get('integrity', ''))):
                raise ValueError('Registry source/integrity denied: ' + path)
    if not all(key in lock['packages'] for key in ('', 'backend', 'frontend')):raise ValueError('Workspace locks missing')
    # A workspace project has one actual lock. Per-component locks are intentionally
    # identical copies of the root lock, rather than independent divergent graphs.
    for key in ('backend/package-lock.json', 'frontend/package-lock.json'):
        if files[key] != files['package-lock.json']:raise ValueError('Component lock drift')
    return lock


def capture(workspace, plan):
    from phase3_handoff import inventory
    before, findings = inventory(workspace, plan)
    if findings:raise ValueError('Missing or unreviewed application files')
    root = Path(before['root']);files = {}
    for path, digest in before['files'].items():
        data = (root / path).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:raise ValueError('Code changed during capture')
        files[path] = data
    if inventory(workspace, plan)[0] != before:raise ValueError('Code changed during capture')
    packages(files)
    return before, files


def build_manifest(workspace, plan):
    lock, _, _ = preparation.load_bundle()
    plan = preparation.validate_plan(plan, lock, preparation.load_bundle()[1])
    fingerprint, files = capture(workspace, plan)
    return {'schema': 'agentapp.phase3-local-execution/1', 'requirement_id': lock['requirement_id'],
            'revision': lock['revision'], 'contract_sha256': lock['files']['contract.json'],
            'documentary_policy_sha256': lock['files']['policy.json'], 'policy': copy.deepcopy(POLICY),
            'stages': {k: {'cwd': v[0], 'argv': v[1], 'timeout_seconds': v[2]} for k, v in STAGES.items()},
            'workspace': fingerprint, 'plan_sha256': manifest.identity(plan),
            'scope': 'First public-directory slice, synthetic fixtures only; no full SaaS acceptance',
            'explicit_manifest_digest_required': True, 'product_acceptance_claimed': False}


def validate_execution(candidate, workspace, plan, approved_digest=None):
    actual = build_manifest(workspace, plan)
    if manifest.identity(candidate) != manifest.identity(actual):raise ValueError('Execution manifest/code/lock/policy drift')
    if approved_digest is not None and approved_digest != manifest.identity(actual):raise ValueError('Approval digest does not match concrete execution')
    return actual


def archive(files):
    """Copy captured bytes through stdin. No host paths are mounted in a worker."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for path, data in sorted(files.items()):
            preparation.plan_path(path)
            info = tarfile.TarInfo(path);info.size = len(data);info.mode = 0o444
            info.uid = info.gid = 1000;info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def container_argv(kind, name, namespace=None):
    if kind not in ('node', 'browser'):raise ValueError('Unsupported generated-code worker')
    cfg = POLICY[kind]
    if not re.fullmatch(r'age50-[a-z0-9-]{1,64}', name):raise ValueError('Unsafe container name')
    if namespace is not None and not re.fullmatch(r'age50-[a-z0-9-]{1,64}', namespace):raise ValueError('Unsafe namespace')
    writable = ['/work/node_modules:rw,exec,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=0700',
                '/work/frontend/dist:rw,nosuid,nodev,size=64m,uid=1000,gid=1000,mode=0700'] if kind=='node' else []
    return ['docker', 'create', '-i', '--pull', 'never', '--name', name, '--network', 'none' if namespace is None else 'container:' + namespace,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '1000:1000',
            '--cpus', cfg['cpu'], '--memory', cfg['memory'], '--memory-swap', cfg['memory'], '--pids-limit', cfg['pids'],
            '--shm-size', '128m', '--log-driver', 'none', '--ulimit', 'fsize=16777216:16777216',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=0700',
            *[arg for path in writable for arg in ('--tmpfs',path)],
            '--workdir', '/work', '--env', 'HOME=/tmp', '--env', 'NPM_CONFIG_USERCONFIG=/tmp/empty-npmrc',
            '--env', 'NPM_CONFIG_LOGS_DIR=/tmp/npm-logs', '--env', 'CI=1', '--env', 'FORCE_COLOR=0',
            '--env', 'PATH=/cache/bin:/usr/local/bin:/usr/bin:/bin',
            cfg['image'], 'sleep', '900']
