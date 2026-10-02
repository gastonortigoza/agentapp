"""Versioned correction protocol; historical geography review stays unchanged."""
import copy
import re
from jsonschema import Draft202012Validator
import manifest
import phase3_geography_source as old
import phase3_review as review

SECTION='geography_correction'
PROTOCOL='geography-source-correction/1'
RULES=old.RULES
PATH=old.PATH


def evidence(candidate):
    code=candidate.get('content','');choices=['']
    for line in code.splitlines():
        value=line.strip()
        if value and value not in choices:choices.append(value[:160])
    return choices[:81]


def review_schema(candidate):
    schema=copy.deepcopy(old.review_schema())
    for check in schema['properties']['checks']['properties'].values():
        check['properties']['quote']={'enum':evidence(candidate)}
    # Keep old exact rule citations; constrain each finding to its own rule.
    findings=[]
    for rule,text in RULES.items():
        finding=copy.deepcopy(schema['properties']['findings']['items'])
        finding['properties']['rule']={'const':rule};finding['properties']['rule_quote']={'const':text}
        findings.append(finding)
    schema['properties']['findings']['items']={'oneOf':findings}
    return schema


def validate_review(value,candidate):
    Draft202012Validator(review_schema(candidate)).validate(value)
    return old.validate_review(value,candidate)


def findings(candidate):
    """Recognize two exact defects from the original, never pretend execution."""
    code=candidate.get('content','');out=[]
    branch=re.search(r'if\s*\(cid\)\s*\{(.*?)\}\s*else\s+if\s*\(pid\)',code,re.S)
    joint=re.search(r'if\s*\((?:cid\s*&&\s*pid|pid\s*&&\s*cid)\)\s*\{',code)
    if branch and not joint and not re.search(r'\bpid\b',branch[1]):
        out.append({'rule':'G02','source':old.SOURCE,'rule_quote':RULES['G02'],'pointer':'/content',
          'issue':'combined_selectors_ignored','fix':'When country_id and province_id are both supplied, validate the province belongs to that country and filter provinces/zones by that province; inconsistent parents422.'})
    quote='if ((cid && !UUID_RE.test(cid)) || (pid && !UUID_RE.test(pid)))'
    if quote in code:
        out.append({'rule':'G02','source':old.SOURCE,'rule_quote':RULES['G02'],'pointer':'/content',
          'issue':'empty_selector_bypasses_validation','fix':'A supplied empty/non-string country_id/province_id is malformed:400. Check presence/type before UUID validation; normalize case when comparing valid UUIDs.'})
    return out


def feedback(child,ops):
    checks=findings(child['candidate'])
    if not checks:raise ValueError('No supported original source discrepancy')
    return {'protocol':PROTOCOL,'original_id':child['id'],'original_sha256':manifest.identity(child),
      'operations_sha256':manifest.identity(ops),'seed_sha256':child['candidate_sha256'],'findings':checks}


def validate_feedback(value,candidate):
    Draft202012Validator(old.writer_schema()).validate(candidate)
    if (not isinstance(value,dict) or set(value)!={'protocol','original_id','original_sha256','operations_sha256','seed_sha256','findings'}
        or value['protocol']!=PROTOCOL or not isinstance(value['original_id'],str)
        or not re.fullmatch('[a-z0-9-]{1,100}',value['original_id'])
        or any(not re.fullmatch('[a-f0-9]{64}',str(value[k])) for k in ('original_sha256','operations_sha256','seed_sha256'))
        or value['seed_sha256']!=manifest.identity(candidate) or candidate.get('path')!=PATH
        or value['findings']!=findings(candidate) or not value['findings']):raise ValueError('Grounded source feedback drift')
    return value


def prompt(row,op,documents):
    base=old.prompt(row,op,documents)
    base=base.replace('Write the first COMPLETE concise TypeScript module','Replace the existing COMPLETE concise TypeScript module')
    if op['role']=='developer':
        base+='\nCorrect this existing original module completely. Address the grounded source findings, never return repair instructions. Preserve A25; keep code <=4000chars. No tests have run. Check joint selectors, supplied empty/non-string UUIDs, case-normalized UUID comparisons and incomplete catalogue cases.'
    else:
        base+='\nSelect each quote EXACTLY from these bounded source lines; never concatenate lines or change whitespace. Examine behavior independently, especially joint selectors, unknown/inconsistent parents, empty/non-string selectors and unloaded catalogue. No source or product approval from markers alone. Allowed quote choices:\n'+manifest.canonical(evidence(row['candidate']))
    return base
