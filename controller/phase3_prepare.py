"""Pinned structured input/file planning. No command dispatcher or code execution."""
import hashlib
from pathlib import Path, PurePosixPath
import re
from jsonschema import Draft202012Validator
import manifest

ROOT = Path(__file__).resolve().parent
LOCK = ROOT/'config/phase3-input-lock.json'
FIXTURE = ROOT/'fixtures/phase3-contract-v1'
STAGES = ('fe', 'be', 'unit', 'fe_build', 'be_build', 'migrate', 'e2e')


class PreparationError(ValueError):
    def __init__(self, code, pointer, fix):
        self.finding = {'code':code, 'pointer':pointer, 'fix':fix}
        super().__init__(code + ' at ' + pointer + ': ' + fix)


def fail(code, pointer, fix):
    raise PreparationError(code, pointer, fix)


def read_json(path):
    if path.stat().st_size > 512_000:
        fail('input_too_large', '/', 'Use the bounded structured input.')
    return manifest.parse(path.read_text(encoding='utf-8'))


def load_bundle(bundle=FIXTURE):
    lock = read_json(LOCK)
    documents, raw = {}, {}
    for name, expected in lock['files'].items():
        path = manifest.relative_path(name, bundle)
        data = path.read_bytes()
        if len(data)>512_000 or hashlib.sha256(data).hexdigest()!=expected:
            fail('input_drift', '/' + name, 'Restore the frozen PR7 input; do not adopt an edited contract.')
        raw[name] = data
        documents[name] = manifest.parse(data.decode('utf-8'))
    bound = documents['manifest.json']
    if bound['requirement']['id']!=lock['requirement_id'] or bound['requirement']['revision']!=lock['revision']:
        fail('unsupported_requirement', '/requirement', 'Use the explicitly supported structured requirement.')
    contract = documents['contract.json']
    if bound['manifest'] != contract['manifest']:
        fail('manifest_binding', '/manifest', 'Restore the manifest bound to this exact contract.')
    if bound['requirement']['contract_sha256'] != lock['files']['contract.json'] or bound['effective_policy']['sha256']!=lock['files']['policy.json']:
        fail('hash_binding', '/requirement', 'Restore the requirement and policy hash binding.')
    # The source hash binds original user-record bytes, not the PR7 reserialization.
    if bound['requirement']['business_source_sha256'] != lock['files']['business-source.json'] or documents['business-source.json']!=documents['business-decisions.json']:
        fail('business_binding', '/requirement', 'Restore the business decision binding.')
    schema = documents['contract.schema.json']
    Draft202012Validator.check_schema(schema)
    if any(Draft202012Validator(schema).iter_errors(contract)):
        fail('contract_schema', '/contract', 'Use the accepted typed API/data/manifest contract.')
    if contract['manifest']['enabled'] is not False or contract['manifest']['deployment']!='disabled':
        fail('execution_not_authorized', '/manifest', 'Keep documentary preparation disabled for execution.')
    policy = documents['policy.json']
    if policy['generated_code_execution'] is not False or policy['tools'] or policy['remote_writes'] is not False:
        fail('policy_not_preparation', '/policy', 'Use the pinned preparation-only policy.')
    return lock, documents, raw


def criteria(documents):
    api = {c['id']:c['expected'] for c in documents['acceptance.json']}
    ui = {s.split(' ',1)[0]:s.split(' ',1)[1] for s in documents['ui-contract.json']['checks_specified']}
    return api | ui


def plan_path(path):
    if not isinstance(path,str) or len(path)>240 or not re.fullmatch(r'[A-Za-z0-9_./-]+',path):
        fail('path_denied', '/files/path', 'Use a canonical relative application file path.')
    parts = path.split('/')
    if any(p in ('','.', '..') or p.endswith('.') for p in parts) or PurePosixPath(path).is_absolute():
        fail('path_denied', '/files/path', 'Remove path traversal and ambiguous components.')
    reserved = {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}
    for p in parts:
        lower = p.lower()
        if lower in {'.git','.github','secrets','node_modules'} or lower.startswith('.env') or lower.split('.')[0] in reserved:
            fail('path_denied', '/files/path', 'Do not plan protected files, secrets or dependencies.')
    if path not in {'package.json','package-lock.json'} and not path.startswith(('frontend/','backend/','fixtures/synthetic/')):
        fail('path_denied', '/files/path', 'Plan only paths declared in the frozen application scope.')
    return path


def default_plan(lock, documents):
    known = criteria(documents)
    api = sorted(k for k in known if k.startswith('A'))
    ui = sorted(k for k in known if k.startswith('UI'))
    files = [
        ('package.json','Root aggregate scripts for frontend/backend checks.',api+ui),
        ('package-lock.json','Root dependency lock from a separate provisioning step.',[]),
        ('backend/package.json','API scripts, dependency pins and engines.',api),
        ('backend/package-lock.json','Exact backend dependency lock.',[]),
        ('backend/src/app.ts','Typed API routes and rules from the frozen contract.',api),
        ('backend/tests/contract.test.ts','API acceptance cases with the supplied expected results.',api),
        ('frontend/package.json','UI scripts, dependency pins and engines.',ui),
        ('frontend/package-lock.json','Exact frontend dependency lock.',[]),
        ('frontend/src/App.tsx','Public directory and private flow from the accepted UI contract.',ui),
        ('frontend/tests/contract.test.tsx','UI acceptance, including filters for both sections.',ui),
        ('frontend/e2e/vertical.spec.ts','Integrated FE/API/DB journey; no invented backend-only E2E requirement.',api+ui)]
    return {'schema':'agentapp.phase3-file-plan/1', 'requirement_id':lock['requirement_id'],
            'revision':lock['revision'], 'contract_sha256':lock['files']['contract.json'],
            'files':[{'path':p,'purpose':purpose,'criteria':ids} for p,purpose,ids in files],
            'stages':list(STAGES)}


def validate_plan(plan, lock, documents):
    keys = {'schema','requirement_id','revision','contract_sha256','files','stages'}
    if not isinstance(plan,dict) or set(plan)!=keys or plan['schema']!='agentapp.phase3-file-plan/1':
        fail('plan_shape', '/', 'Return the exact typed plan object; commands and tools are not plan fields.')
    for key, expected in [('requirement_id',lock['requirement_id']),('revision',lock['revision']),('contract_sha256',lock['files']['contract.json'])]:
        if plan[key]!=expected:
            fail('plan_identity', '/' + key, 'Bind the plan to the accepted requirement revision and contract hash.')
    if plan['stages']!=list(STAGES):
        fail('stage_denied', '/stages', 'Use declared stage names; do not supply commands or omit checks.')
    if not isinstance(plan['files'],list) or not 1<=len(plan['files'])<=200:
        fail('plan_files', '/files', 'Supply a bounded list of planned application files.')
    known, seen, covered = criteria(documents), set(), set()
    for i, entry in enumerate(plan['files']):
        pointer = '/files/' + str(i)
        if not isinstance(entry,dict) or set(entry)!={'path','purpose','criteria'}:
            fail('file_shape', pointer, 'Return only path, purpose and criterion references; no code or argv.')
        path = plan_path(entry['path'])
        if path.lower() in seen:
            fail('duplicate_path', pointer+'/path', 'Use unique paths, including on Windows.')
        seen.add(path.lower())
        if not isinstance(entry['purpose'],str) or not 1<=len(entry['purpose'])<=1000:
            fail('purpose_shape', pointer+'/purpose', 'Describe the file task within the text limit.')
        refs = entry['criteria']
        if not isinstance(refs,list) or not all(isinstance(c,str) and c in known for c in refs) or len(set(refs))!=len(refs):
            fail('criterion_reference', pointer+'/criteria', 'Cite exact accepted criterion IDs, without duplicates.')
        covered.update(refs)
    if covered!=set(known):
        fail('criteria_missing', '/files', 'Plan every accepted criterion; documentary status is not test success.')
    required = {'package.json','package-lock.json','frontend/package.json','frontend/package-lock.json','backend/package.json','backend/package-lock.json'}
    if not required<=seen:
        fail('scaffold_missing', '/files', 'Plan package scripts and locked dependencies for all three roots.')
    return plan


def prepare(bundle=FIXTURE, plan=None):
    lock, documents, raw = load_bundle(bundle)
    plan = validate_plan(default_plan(lock,documents) if plan is None else plan, lock, documents)
    frozen = documents['manifest.json']['manifest']
    unresolved = [s for s in frozen['unverified_blocks_enablement'] if not s.startswith('AGE-30 ')]
    return {'schema':'agentapp.phase3-preparation/1', 'scope':'structured_input_and_file_plan',
            'state':'prepared_execution_disabled', 'execution_authorized':False,
            'integrated_base':lock['integrated_base'], 'source_commit':lock['source_commit'],
            'input_identity':manifest.identity(lock), 'snapshot_hashes':lock['files'],
            'plan_hash':manifest.identity(plan), 'plan':plan,
            'stage_proposals':{k:frozen['commands'][k] for k in STAGES},
            'criteria':criteria(documents), 'unresolved_execution_blockers':unresolved,
            'limits_claimed_enforced':False, 'product_tests_executed':False}


def snapshot(bundle, destination, plan=None):
    result = prepare(bundle,plan)
    _, _, raw = load_bundle(bundle)
    root = Path(destination)
    root.mkdir(parents=True,exist_ok=True)
    snapshot_key = manifest.identity({'input_identity':result['input_identity'],'plan_hash':result['plan_hash']})
    folder = manifest.relative_path(snapshot_key,root)
    folder.mkdir(exist_ok=True)
    content = raw | {'preparation.json':(manifest.canonical(result)+'\n').encode('utf-8')}
    for name,data in content.items():
        path = manifest.relative_path(name,folder)
        try:
            with path.open('xb') as output: output.write(data)
        except FileExistsError:
            if path.read_bytes()!=data:
                fail('snapshot_conflict', '/snapshot', 'Keep original evidence; reconcile altered snapshot files.')
    return result | {'snapshot_directory':str(folder.resolve())}
