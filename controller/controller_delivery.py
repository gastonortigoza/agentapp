"""Exportación explícita del piloto cerrado hacia una propuesta manual nueva.

No reabre el run, no cambia su contrato y no hereda autorización de publicación.
Sólo soporta el piloto status_summary_v1 y sus dos archivos de entrega.
"""
import hashlib
import json
from pathlib import Path
import uuid

import controller_gate
import github_publish
import manifest
from local_control import LocalStore, artifact, require_evidence
from local_pilot import TESTS, validate_candidate

FILES=('status_summary.py','tests/test_status_summary.py')


def delivery(database,run_id):
    if not Path(database).is_file():raise ValueError('Base de ejecuciones inexistente')
    store=LocalStore(database)
    with store.transaction() as db:
        row=store._row(db,run_id)
        if row['state']!='delivered' or row['execution_kind']!='local':
            raise ValueError('Sólo se exporta una entrega local cerrada y validada')
        contract=json.loads(row['manifest'])
        if contract.get('pipeline',{}).get('kind') not in {'status_summary_v1','attention_summary_v1','remote_summary_v1'}:
            raise ValueError('Piloto sin adaptador de publicación')
        require_evidence(db,row,'delivered')
        candidate=artifact(db,run_id,'materialize')
        tests=artifact(db,run_id,'tests')
        review=artifact(db,run_id,'reviewer')
        code=validate_candidate(candidate['code'])
        if hashlib.sha256(code.encode()).hexdigest()!=candidate['code_sha256']:
            raise ValueError('El código almacenado difiere del artefacto probado')
        proof={'parent_run_id':run_id,'parent_manifest_hash':row['manifest_hash'],
               'parent_repository':contract['project']['repository'],
               'code_sha256':candidate['code_sha256'],'tests_sha256':candidate['tests_sha256'],
               'tests_evidence_hash':manifest.identity(tests),'review_evidence_hash':manifest.identity(review),
               'scope':'manual_pilot_export','parent_state':'delivered'}
    return code,proof


def prepare(database,run_id,branch):
    controller_gate.require_green()
    github_publish.branch_name(branch)
    code,proof=delivery(database,run_id)
    from local_pilot import pilot_spec
    contract=json.loads(LocalStore(database).get(run_id)['manifest'])
    _,pilot_tests,module=pilot_spec(contract)
    files=(module+'.py','tests/test_'+module+'.py')
    if proof['parent_repository']!=github_publish.connection.configured_repo():
        raise ValueError('La entrega pertenece a otro repositorio')
    source=github_publish.JOBS.parent/('delivery-export-'+uuid.uuid4().hex)
    (source/'tests').mkdir(parents=True)
    (source/files[0]).write_text(code,encoding='utf-8',newline='\n')
    # Única adaptación: nombre del módulo exportado; los 12 casos no cambian.
    (source/files[1]).write_text(pilot_tests.replace('from solution import','from '+module+' import'),encoding='utf-8',newline='\n')
    result=github_publish.prepare(source,list(files),branch)
    job=github_publish.job_path(result['job_id'])
    result['manifest']['controller_delivery']=proof
    (job/'manifest.json').write_text(json.dumps(result['manifest'],ensure_ascii=False,indent=2),encoding='utf-8')
    result['review_digest']=github_publish.digest(result['manifest'])
    result.update(parent_run_id=run_id,execution_authorized=False,
                  note='Propuesta nueva: requiere revisión y aprobación propias; no modifica el run cerrado')
    return result
