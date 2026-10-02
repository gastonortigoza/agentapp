"""Prospective source review of a complete, historically overlong proposal.

The previous rejection and its 4000-character policy remain immutable. This
version permits 6000 characters, with the same model/time/token limits.
"""
import copy
import re
from jsonschema import Draft202012Validator
import manifest
import phase3_geography_source as old
import phase3_geography_correction as correction
import phase3_review as review

SECTION='geography_revalidation'
PROTOCOL='geography-source-revalidation/1'
SOURCE='geography-source-rules/2'
PATH=old.PATH
RULES=old.RULES|{'G03':old.RULES['G03'].replace('4000characters','6000characters')}
# Exact unsupported instructions observed in immutable reviewer originals. This
# narrow guard never approves code; unrecognized semantics still need PG tests.
CONTRADICTORY_FIXES={
 'Remove the 503 catalog_unavailable check for empty provinces in the country_id filter branch to allow empty zone/province arrays for valid countries.',
 'Remove the hardcoded \'AR\' filter from the SQL query to allow the catalogue to return all available countries, or ensure the query does not restrict the dataset to a single hardcoded code.'}


def writer_schema():
    schema=copy.deepcopy(old.writer_schema())
    schema['properties']['content']['maxLength']=6000
    return schema


def evidence(candidate):
    # Every line, including final error handling, is available to the reviewer.
    return list(dict.fromkeys(['']+[line.strip()[:160] for line in candidate.get('content','').splitlines() if line.strip()]))


def review_schema(candidate):
    schema=copy.deepcopy(old.review_schema())
    for check in schema['properties']['checks']['properties'].values():
        check['properties']['quote']={'enum':evidence(candidate)}
    findings=[]
    for rule,text in RULES.items():
        finding=copy.deepcopy(schema['properties']['findings']['items'])
        finding['properties'].update(rule={'const':rule},source={'const':SOURCE},rule_quote={'const':text})
        findings.append(finding)
    schema['properties']['findings']['items']={'oneOf':findings}
    return schema


def validate_review(value,candidate):
    Draft202012Validator(review_schema(candidate)).validate(value)
    for check in value['checks'].values():
        if check['passed'] and not check['quote']:raise ValueError('Approval needs source evidence')
        if check['quote'] and check['quote'] not in candidate['content']:raise ValueError('Invented source evidence')
    if {k for k,c in value['checks'].items() if not c['passed']}!={f['rule'] for f in value['findings']}:raise ValueError('Findings/checks disagree')
    if any(f['rule']=='G02' and f['fix'] in CONTRADICTORY_FIXES for f in value['findings']):raise ValueError('Reviewer correction contradicts frozen G02; never forward to developer')
    return value


def defects(candidate):
    failed=[]
    try:Draft202012Validator(writer_schema()).validate(candidate)
    except Exception:failed.append(('G01','Return the complete bounded module at its fixed path.'))
    else:
        code=candidate['content']
        for rule,tokens in [('G01',('registerGeography','/api/catalog/geography','catalog_unavailable')),
          ('G02',('agentapp.countries','agentapp.provinces','agentapp.zones','country_id','province_id','geography_invalid')),
          ('G03',('request_id','service_unavailable'))]:
            if any(t not in code for t in tokens):failed.append((rule,'Implement required module behavior: '+', '.join(tokens)))
        if re.search(r'child_process|node:fs|node:net|process\.env|\beval\s*\(|\bnew\s+Function\s*\(|\bconsole\s*\.|https?://|\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT)\b',code):
            failed.append(('G01','Remove host/network access, logging or DB mutation.'))
    found=[{'rule':k,'source':SOURCE,'rule_quote':RULES[k],'pointer':'/content','issue':'source_structure','fix':fix} for k,fix in failed]
    return found+[f|{'source':SOURCE,'rule_quote':RULES[f['rule']]} for f in correction.findings(candidate)]


def origin(child,ops):
    """Adopt only exact complete raw output whose sole schema error was length."""
    if (child['state']!='blocked_evidence' or child['reason']!='Invalid output schema: ValidationError'
        or child['binding'].get('section')!=correction.SECTION or child['binding']['rules']!=old.RULES
        or manifest.identity(child['binding'])!=child['binding_sha256'] or child['calls']!=1 or len(ops)!=1
        or child['execution_authorized'] or child['product_tests_executed']):raise ValueError('Unsupported revalidation original')
    op=ops[0];r=op.get('result',{});request=op.get('request',{})
    if (op['seq']!=0 or op['role']!='developer' or op['state']!='confirmed'
        or not r.get('ok') or r.get('done') is not True or r.get('done_reason')!='stop' or r.get('tool_calls')
        or r.get('model_digest')!=child['binding']['digest']
        or any(type(r.get(k)) is not int or not 0<=r[k]<=op['reserve_'+k.split('_')[0]] for k in ('input_tokens','output_tokens'))
        or not isinstance(r.get('text'),str) or len(r['text'].encode())>24000
        or manifest.identity(request)!=op.get('request_sha256') or request.get('format')!=old.writer_schema()
        or request.get('model')!=child['binding']['model'] or request.get('digest')!=child['binding']['digest']
        or r.get('prompt_sha256')!=manifest.identity(request.get('messages'))
        or op['candidate']!=child['candidate'] or manifest.identity(op['candidate'])!=child['candidate_sha256']):raise ValueError('Incomplete or changed original')
    if 'artifact' in r:
        from phase3_transport import inspection
        captured=inspection(request)
        if captured|{'prompt_sha256':manifest.identity(request['messages'])}!=r:raise ValueError('Durable original changed')
    candidate=manifest.parse(r['text'])
    errors=list(Draft202012Validator(old.writer_schema()).iter_errors(candidate))
    if len(errors)!=1 or errors[0].validator!='maxLength' or list(errors[0].path)!=['content']:raise ValueError('Only character overflow can be revalidated')
    Draft202012Validator(writer_schema()).validate(candidate)
    proof={'protocol':PROTOCOL,'original_id':child['id'],'original_sha256':manifest.identity(child),
      'operations_sha256':manifest.identity(ops),'operation_sequence':0,'result_sha256':manifest.identity(r),
      'request_sha256':op['request_sha256'],'seed_sha256':manifest.identity(candidate)}
    return candidate,proof


def validate_origin(proof,candidate):
    keys={'protocol','original_id','original_sha256','operations_sha256','operation_sequence','result_sha256','request_sha256','seed_sha256'}
    if (not isinstance(proof,dict) or set(proof)!=keys or proof['protocol']!=PROTOCOL or type(proof['operation_sequence']) is not int
        or proof['operation_sequence']!=0 or not re.fullmatch('[a-z0-9-]{1,100}',str(proof['original_id']))
        or any(not re.fullmatch('[a-f0-9]{64}',str(proof[k])) for k in keys if k.endswith('sha256'))
        or proof['seed_sha256']!=manifest.identity(candidate) or not 4000<len(candidate.get('content',''))<=6000):raise ValueError('Revalidation origin drift')
    Draft202012Validator(writer_schema()).validate(candidate)
    return proof


def verify_original(journal,proof,candidate):
    seed,actual=origin(journal.get(proof['original_id']),journal.records(proof['original_id'],'plan_ops'))
    if seed!=candidate or actual!=proof:raise ValueError('Revalidation original drift')


def prompt(row,op,documents):
    value=old.prompt(row,op,documents).replace(manifest.canonical(old.RULES),manifest.canonical(RULES)).replace(old.SOURCE,SOURCE)
    value=value.replace('Write the first COMPLETE concise TypeScript module','Replace the existing COMPLETE concise TypeScript module')
    value+='\nFrozen contract clarification, not a new requirement: AR is the required country code and a valid bound SQL parameter, never a synthetic fixture ID. Do not request all countries or remove the AR predicate. Empty/incomplete/unloaded catalogue requires503 catalog_unavailable; do not replace that response with200 empty arrays. Province/zone completeness remains a future frozen PostgreSQL check.'
    if op['role']=='reviewer':
        value+='\nNew prospective review of an immutable complete historical output; its prior rejection remains rejected. Independently check ALL behavior, including both selectors, malformed/empty selectors, unknown/inconsistent parents, and incomplete catalogue with no zones. Select quotes EXACTLY from these lines; never join or rewrite them. Quote presence alone cannot establish behavior. Allowed quotes:\n'+manifest.canonical(evidence(row['candidate']))
    else:value+='\nAddress all findings in the complete module <=6000characters. Preserve the frozen PostgreSQL acceptance cases and business contract; no tests have run.'
    return value
