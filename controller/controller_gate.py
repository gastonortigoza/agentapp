"""Recibo local de suite ligado al código, tests, lockfile y runtime actuales."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
RECEIPT = ROOT/'.state/controller-suite.json'


def source_identity(root=ROOT):
    root = Path(root)
    paths = sorted({*root.glob('*.py'), *root.glob('*.cmd'),
                    *(root/'tests').glob('*.py'), *(root/'schemas').glob('*.json'),
                    root/'pyproject.toml', root/'uv.lock',
                    *(p for p in (root/'config').glob('*') if p.name in {'agents.yaml','pilot-manifest.json','local-pilot.json','ci-policy.json','github-app.json','phase2.json'})})
    hashes = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    packages = {name:importlib.metadata.version(name) for name in ('crewai','jsonschema','PyYAML','pytest')}
    return hashlib.sha256(json.dumps({'files':hashes,'python':sys.version,'packages':packages},sort_keys=True).encode()).hexdigest()


def require_green(root=ROOT, receipt=RECEIPT):
    try:
        record = json.loads(Path(receipt).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('Ejecutar agent self-test antes de trabajo nuevo') from None
    if not isinstance(record,dict) or record.get('exit_code') != 0 or record.get('source_identity') != source_identity(root):
        raise ValueError('Suite ausente, fallida o de otra revisión; ejecutar agent self-test')
    return record['source_identity']


def self_test(root=ROOT, receipt=RECEIPT):
    before = source_identity(root)
    # Invalidar primero: fallo, timeout o interrupción no conservan un verde anterior.
    receipt = Path(receipt)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.unlink(missing_ok=True)
    proc = subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root,
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    print(proc.stdout)
    if proc.returncode or source_identity(root) != before:
        raise ValueError('Suite fallida o revisión modificada durante validación')
    temporary = receipt.with_name(receipt.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps({'source_identity':before,'exit_code':0,'scope':'local_controller_suite'}),encoding='utf-8')
    os.replace(temporary,receipt)
    return before
