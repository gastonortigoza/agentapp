"""Frozen A25 PostgreSQL harness and source preflight, no execution capability.

Preparation never consumes a new agent budget, accepts a rejected source, or
dispatches Docker. A separate durable materializer remains required.
"""
import copy
import json
from pathlib import Path
import re
import controller_gate
import manifest
import phase3_auth_sandbox as dependencies
import phase3_auth_source as auth
import phase3_geography_source as geography
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_prepare as preparation
import phase3_queued_sources as base
import phase3_review as review
import phase3_source_corrections as correction
import phase3_source_revalidations as revalidation

SEED=Path(__file__).resolve().parent.parent/'examples/phase3-geography-execution/inputs'
FILES={
 'backend/geography.tsconfig.json':'32700f5087b0a6da8d911695580a34f758b83725af0c01330f7143ba51a52c86',
 'geography-acceptance.test.mjs':'400f3298ea3a6a4b9d6a2276e2e950f4eb5fadbaea316b1f6b22d158bb88da3f',
 'schema.sql':'3ece9251d44eca00f5a3973935633acd83779680d725e4bdef2aa79a3da9e8e1'}
PACKAGES=('package.json','package-lock.json','backend/package.json','frontend/package.json')
CASES=(
 'GPG01 unavailable qualification returns503 without DB calls',
 'GPG02 synthetic loaded hierarchy exact DTO and AR parameter',
 'GPG03 country filter preserves hierarchy',
 'GPG04 province alone derives country and filters zones',
 'GPG05 combined filters honor selected province',
 'GPG06 empty malformed repeated and extra query keys return400',
 'GPG07 unknown and inconsistent selectors return422',
 'GPG08 uppercase UUID selectors preserve canonical parents',
 'GPG09 empty country catalogue returns503',
 'GPG10 missing provinces returns503 with and without country filter',
 'GPG11 missing zones returns503 including selected province',
 'GPG12 database failure returns safe503 with request id',
 'GPG13 application role cannot modify geography and FK protects parents',
 'GPG14 standalone registration exposes no auth or profile routes')
POLICY={'schema':'agentapp.geography-scaffold-policy/1','database':'age50_geography_test',
 'scope':'A25 standalone API acceptance using explicitly synthetic geography',
 'network':'shared_loopback_namespace_without_interfaces','host_mounts':False,'host_ports':False,
 'dependency_network':False,'dependency_scripts':False,'official_catalogue_loaded':False,
 'credentials':['GEO_APP_DATABASE_URL','GEO_FIXTURE_DATABASE_URL','GEO_DISPOSABLE_FIXTURE'],
 'postgres_image':dependencies.directory.POSTGRES,'node_image':dependencies.directory.NODE,
 'input_bytes':8*1024*1024,'input_count':40,'minimum_api_tests':14,
 'product_execution_budget':'same original increment queue; reserve before future materialization',
 'deployment_authorized':False,'execution_authorized':False,'runtime_ready':False}
STAGES={
 'be_build':{'cwd':'/work/backend','argv':['node','../node_modules/typescript/bin/tsc','--noEmit','-p','geography.tsconfig.json'],'seconds':60},
 'api':{'cwd':'/work','argv':['node','--import','tsx','--test','--test-reporter=tap','--test-concurrency=1','geography-acceptance.test.mjs'],'seconds':180}}
TABLE='phase3_geography_scaffolds'


def seed_files():
    # .gitattributes is repository metadata, not an executable runtime input.
    files=dependencies.read_files(SEED,set(FILES)|{'.gitattributes'})
    files.pop('.gitattributes')
    if {n:dependencies.sha(v) for n,v in files.items()}!=FILES:raise ValueError('Frozen geography scaffold drift')
    parent=dependencies.seed_files()
    files.update({n:parent[n] for n in PACKAGES})
    dependencies.packages(files)
    if len(files)>POLICY['input_count'] or sum(map(len,files.values()))>POLICY['input_bytes']:raise ValueError('Geography scaffold bounds')
    code=files['geography-acceptance.test.mjs'].decode('utf-8')
    if tuple(re.findall(r"^test\('([^']+)'",code,re.M))!=CASES:raise ValueError('Frozen geography acceptance cases drift')
    return files


def frozen_history(db,rid):
    parent=queue.load(db,rid);first=base.load(db,rid);prior=correction.load(db,rid);latest=revalidation.load(db,rid)
    revalidation.check(db,latest,parent,prior)
    if latest['state']!='awaiting_discrepancy' or latest['pending_audit']:raise ValueError('Only stopped reviewed discrepancy permits fixture preparation')
    packet=revalidation.packet(latest,parent,prior,first)
    if packet['through_round']-packet['from_round']+1>=queue.REVIEW_EVERY:raise ValueError('Periodic flow audit required')
    originals=base.child_originals(db,parent)
    for record in (first,prior,latest):originals[record['child_id']]=correction.originals(db,record['child_id'])
    return {'queue_id':rid,'parent_sha256':manifest.identity(parent),'first_source_sha256':manifest.identity(first),
      'correction_sha256':manifest.identity(prior),'revalidation_sha256':manifest.identity(latest),
      'originals':originals,'shared_used':base.aggregate(db,parent),'limits':copy.deepcopy(queue.LIMITS),
      'formal_audited_through':prior['audit']['packet']['through_round'],'closed_attempts':packet['through_round'],
      'source_review_ids':[record['child_id'] for record in (first,prior,latest)]}


def binding(journal,rid,cache_root):
    suite=controller_gate.require_green();files=seed_files();lock,docs,_=preparation.load_bundle()
    if lock['files']['contract.json']!=auth.CONTRACT_SHA:raise ValueError('Geography contract drift')
    cache=dependencies.cached_tarballs(files,cache_root)
    with journal.transaction() as db:history=frozen_history(db,rid)
    result={'suite_identity':suite,'input_identity':manifest.identity(lock),'contract_sha256':auth.CONTRACT_SHA,
      'tables_sha256':manifest.identity({k:docs['contract.json']['data']['tables'][k] for k in ('countries','provinces','zones')}),
      'acceptance_sha256':manifest.identity(geography.ACCEPTANCE),'history':history,'policy':copy.deepcopy(POLICY),
      'stages':copy.deepcopy(STAGES),'cases':list(CASES),'files':{n:dependencies.sha(v) for n,v in files.items()},
      'lock_sha256':dependencies.LOCK_SHA256,'cache':{n:dependencies.sha(v) for n,v in sorted(cache.items())}}
    if controller_gate.require_green()!=suite or seed_files()!=files:raise ValueError('Geography scaffold changed during qualification')
    return result


def prepare(journal,rid,cache_root):
    qualified=binding(journal,rid,cache_root)
    row={'schema':'agentapp.geography-scaffold-receipt/1','queue_id':rid,'binding':qualified,'binding_sha256':manifest.identity(qualified),
      'state':'prepared_fixture_only','execution_authorized':False,'runtime_ready':False,
      'source_review_accepted':False,'product_tests_executed':False,'official_catalogue_loaded':False,
      'full_product_acceptance':False,'reason':'Immutable PG acceptance inputs prepared; accepted source and durable materializer still required'}
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS phase3_geography_scaffolds(queue_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        if frozen_history(db,rid)!=qualified['history']:raise ValueError('Geography original changed during preparation')
        found=db.execute('SELECT body FROM phase3_geography_scaffolds WHERE queue_id=?',(rid,)).fetchone()
        if found:
            previous=manifest.parse(found[0])
            # Preparation is documentary input evidence, not a controller permit.
            # Future controller qualifications may inspect the SAME immutable
            # fixture without overwriting its historical preparation receipt.
            if previous['binding']|{'suite_identity':qualified['suite_identity']}!=qualified:raise ValueError('Existing scaffold drift; preserve original preparation')
            row=previous
        else:db.execute('INSERT INTO phase3_geography_scaffolds VALUES(?,?)',(rid,manifest.canonical(row)))
    return row


def get(journal,rid):
    with journal.transaction() as db:
        found=db.execute('SELECT body FROM phase3_geography_scaffolds WHERE queue_id=?',(rid,)).fetchone()
        if found is None:raise ValueError('Unknown geography scaffold')
        row=manifest.parse(found[0])
        if row['queue_id']!=rid or row['state']!='prepared_fixture_only':raise ValueError('Geography scaffold state drift')
        if manifest.identity(row['binding'])!=row['binding_sha256'] or row['binding']['history']!=frozen_history(db,rid):raise ValueError('Geography scaffold/original drift')
        if any(row[k] is not False for k in ('execution_authorized','runtime_ready','source_review_accepted','product_tests_executed','official_catalogue_loaded','full_product_acceptance')):raise ValueError('Scaffold cannot grant product acceptance')
        return row


def source_preflight(journal,rid,review_id,workspace,cache_root):
    """Future materializer input only. Even valid evidence cannot dispatch here."""
    receipt=get(journal,rid)
    current=binding(journal,rid,cache_root)
    if current!=receipt['binding']|{'suite_identity':current['suite_identity']}:raise ValueError('Scaffold inputs no longer current')
    if review_id not in receipt['binding']['history']['source_review_ids']:raise ValueError('Source must belong to the same preserved queue')
    source=journal.get(review_id);section=source['binding'].get('section')
    if section not in ('geography_api','geography_correction','geography_revalidation'):raise ValueError('Unsupported geography source')
    row,ops,_,_=handoff.consume(journal,review_id,section)
    expected=seed_files();files=dependencies.read_files(workspace,set(expected)|{geography.PATH})
    if any(files[n]!=expected[n] for n in expected) or files[geography.PATH]!=row['candidate']['content'].encode():raise ValueError('Geography fixture/source bytes differ')
    if binding(journal,rid,cache_root)!=current:raise ValueError('Geography preflight changed')
    return {'schema':'agentapp.geography-source-preflight/1','scaffold_sha256':manifest.identity(receipt),'suite_identity':current['suite_identity'],
      'source':{'id':row['id'],'row_sha256':manifest.identity(row),'operations_sha256':manifest.identity(ops)},
      'files':{n:dependencies.sha(v) for n,v in files.items()},'execution_authorized':False,'runtime_ready':False,
      'product_tests_executed':False,'full_product_acceptance':False,'reason':'Separate queue-bound operation ledger/materializer required before execution'}


def database_sql(schema,app_password,fixture_password):
    if dependencies.sha(schema)!=FILES['schema.sql']:raise ValueError('Geography bootstrap schema drift')
    if app_password==fixture_password or any(not isinstance(p,str) or not re.fullmatch('[a-f0-9]{64}',p) for p in (app_password,fixture_password)):raise ValueError('Distinct ephemeral geography credentials required')
    return ("BEGIN; CREATE ROLE age50_geo_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\n"
      f"CREATE ROLE age50_geo_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{app_password}';\n"
      f"CREATE ROLE age50_geo_fixture LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{fixture_password}';\n"
      "REVOKE CONNECT,TEMP ON DATABASE age50_geography_test FROM PUBLIC; REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;\n"
      "GRANT CONNECT ON DATABASE age50_geography_test TO age50_geo_app,age50_geo_fixture;\n"
      +schema.decode('utf-8')+"\nCOMMIT;\n").encode()


def check_test_totals(exit_code,stdout):
    """All fixed named tests must run exactly once; skips/retries cannot pass."""
    if type(exit_code) is not int or exit_code!=0 or not isinstance(stdout,bytes) or len(stdout)>64000:raise ValueError('Geography acceptance result unavailable')
    text=stdout.decode('utf-8','strict')
    for name in CASES:
        if len(re.findall(r'^ok \d+ - '+re.escape(name)+r'$',text,re.M))!=1:raise ValueError('Missing or repeated geography acceptance case')
    for key,value in (('tests',14),('pass',14),('fail',0),('cancelled',0),('skipped',0),('todo',0)):
        if len(re.findall(r'^# '+key+' '+str(value)+r'$',text,re.M))!=1:raise ValueError('Geography acceptance totals invalid')
    return {'tests':14,'pass':14,'fail':0,'skipped':0,'scope':'synthetic A25 API only','official_catalogue_loaded':False,'full_product_acceptance':False}


def export(journal,rid,path):
    row=get(journal,rid);target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8')
