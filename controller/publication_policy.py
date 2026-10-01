"""Rutas de control excluidas del publicador ordinario del piloto.

Cambios a estos archivos requieren una entrega de mantenimiento separada.
La aprobación de un diff de aplicación no modifica esta política.
"""
from pathlib import PurePosixPath

CONTROL_FILES=frozenset({
    'codeowners','conftest.py','dockerfile','compose.yaml','compose.yml','docker-compose.yml',
    'pyproject.toml','uv.lock','package.json','package-lock.json','critical_modules.yaml',
    'agent.py','agent.cmd','agent_runtime.py','controller.py','controller_cli.py',
    'controller_gate.py','controller_delivery.py','controlled_flow.py','executor.py',
    'github_ci.py','github_pr.py','github_publish.py','github_connection.py','github_app_auth.py','backup.py','git_control.py',
    'publication_journal.py','publication_policy.py','external_content.py',
    'integration_sync.py','integration_cli.py','integration_auth.py','integration_linear.py','integration_notion.py','integration_coolify.py','integration_linear_state.py','integration_requirement.py','integration_closeout.py',
    'local_control.py','local_flow.py','local_pilot.py','local_tracing.py',
    'manifest.py','worker_lock.py','ollama_worker.py','lab.py','mcp_local.py',
})
CONTROL_DIRS=frozenset({'.github','.git','config','schemas','infra','infrastructure','secrets','credentials','.state'})


def check_paths(names):
    for name in names:
        if not isinstance(name,str) or not name or '\\' in name or ':' in name:
            raise ValueError('Ruta de publicación inválida')
        parts=name.split('/')
        if PurePosixPath(name).is_absolute() or any(p in {'','..','.'} or p.rstrip(' .')!=p for p in parts):
            raise ValueError('Ruta no canónica')
        folded=[p.casefold() for p in parts]
        if any(p in CONTROL_DIRS for p in folded) or folded[-1] in CONTROL_FILES or folded[-1].startswith('dockerfile.'):
            raise ValueError('Archivo de control protegido: '+name)
        if folded[0]=='tests' and any(folded[-1].startswith(prefix) for prefix in
                ('test_controller','test_github','test_publication','test_external','test_manifest','test_worker','test_integration')):
            raise ValueError('Prueba del controlador protegida: '+name)


def check_trusted_tests(name,data):
    if name.casefold()=='tests/test_attention_summary.py':
        from local_pilot import ATTENTION_TESTS
        expected=ATTENTION_TESTS.replace('from solution import','from attention_summary import').encode('utf-8')
        if data!=expected:raise ValueError('Los tests confiables del piloto no pueden relajarse')
    if name.casefold()=='tests/test_status_summary.py':
        from local_pilot import TESTS
        expected=TESTS.replace('from solution import','from status_summary import').encode('utf-8')
        if data!=expected:raise ValueError('Los tests confiables del piloto no pueden relajarse')
