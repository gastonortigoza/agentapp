"""Contrato local v1: parseo estricto, esquema cerrado y validación semántica."""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess

from jsonschema import Draft202012Validator
import yaml

SCHEMA = Path(__file__).resolve().parent/'schemas/project-1.0.schema.json'


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError('Clave duplicada o no textual')
        result[key] = value
    return result


class StrictLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    return unique_pairs((loader.construct_object(k), loader.construct_object(v)) for k,v in node.value)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def canonical(data):
    try:
        return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('Datos no compatibles con JSON finito') from None


def identity(data):
    return hashlib.sha256(canonical(data).encode('utf-8')).hexdigest()


def parse(text, yaml_format=False):
    if len(text) > 1_000_000:
        raise ValueError('Manifiesto demasiado grande')
    if yaml_format:
        for token in yaml.scan(text):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise ValueError('No se admiten anchors/aliases YAML')
            if isinstance(token, yaml.tokens.ScalarToken) and token.plain:
                value = token.value
                if value.lower() in {'yes','no','on','off','y','n'} or re.fullmatch(r'0[0-9]+', value):
                    raise ValueError('Escalar YAML ambiguo; usar comillas o forma explícita')
        data = yaml.load(text, Loader=StrictLoader)
    else:
        data = json.loads(text, object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Número no finito')))
    canonical(data)  # rechaza NaN/Infinity y objetos YAML ajenos a JSON
    return data


def relative_path(value, workspace):
    if '\\' in value or ':' in value or re.search(r'[*?\x00-\x1f]', value):
        raise ValueError('Ruta no canónica')
    parts = value.split('/')
    if value != '.' and any(p in ('','.', '..') or p.rstrip(' .') != p for p in parts):
        raise ValueError('Ruta no canónica')
    if PurePosixPath(value).is_absolute():
        raise ValueError('Ruta absoluta no permitida')
    root = Path(workspace).resolve()
    path = root
    for part in parts:
        path = path/part
        if path.is_symlink() or path.is_junction():
            raise ValueError('Enlaces no permitidos')
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('Escape del workspace')
    return resolved


def validate(data, workspace, schema_path=None):
    canonical(data)
    if schema_path is None:
        version = data.get('schema_version') if isinstance(data,dict) else None
        if version not in {'1.0','1.1','1.2','1.3'}:
            raise ValueError('Versión de manifiesto no admitida')
        schema_path=SCHEMA.with_name('project-'+version+'.schema.json')
    schema = json.loads(Path(schema_path).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        # No reproducir valores no confiables ni posibles secretos.
        raise ValueError('Manifiesto incompatible con el esquema')
    profiles = data['limits']['network_profiles']
    for command in data['commands'].values():
        relative_path(command['cwd'], workspace)
        if command['network_profile'] not in profiles:
            raise ValueError('Perfil de red inexistente')
        if command['timeout_seconds'] > data['limits']['time_seconds']:
            raise ValueError('Timeout excede límite de ejecución')
    for service in data['services_required']:
        if service['healthcheck_command'] not in data['commands']:
            raise ValueError('Healthcheck sin comando')
    for check in data['checks'].values():
        if check['status'] == 'required' and any(name not in data['commands'] for name in check['commands']):
            raise ValueError('Check referencia comando inexistente')
        if check['status'] == 'not_applicable' and check['policy_revision'] != data['approval_policy']['revision']:
            raise ValueError('Excepción ligada a otra política')
    if data['checks']['unit']['status'] != 'required':
        raise ValueError('El piloto exige pruebas unitarias')
    allowed = [relative_path(p, workspace) for p in data['paths']['allowed_write']]
    denied = [relative_path(p, workspace) for p in data['paths']['denied_write']]
    if any(a.is_relative_to(d) for a in allowed for d in denied):
        raise ValueError('Ruta permitida completamente denegada')
    for role in data['roles'].values():
        if role['output_tokens'] > role['context_tokens']:
            raise ValueError('Salida supera contexto')
    budgets = data['budgets']
    for key in ('active_seconds','input_tokens','output_tokens','external_cost_max'):
        if budgets[key] > budgets['authorization'][key]:
            raise ValueError('Presupuesto supera autorización')
    if data['schema_version'] in {'1.1','1.2','1.3'}:
        from executor import IMAGE,TEST_ARGV
        from local_pilot import pilot_spec,digest
        REQUIREMENT,TESTS,_=pilot_spec(data)
        pipeline=data['pipeline']
        if data['schema_version']=='1.3':
            from integration_requirement import PAGE
            from integration_linear import PROJECT
            if data['allowed_resources']['notion_pages']!=[PAGE] or data['allowed_resources']['linear_projects']!=[PROJECT]:
                raise ValueError('Remote source is not authorized in this contract')
        if pipeline['requirement_sha256']!=digest(REQUIREMENT) or pipeline['tests_sha256']!=digest(TESTS):
            raise ValueError('Requisito o pruebas difieren del piloto aprobado')
        if data['commands']!={'unit':{'argv':TEST_ARGV,'cwd':'.','timeout_seconds':60,'max_retries':0,'network_profile':'offline'}}:
            raise ValueError('Comando distinto del ejecutor soportado')
        if any(data['limits'][key]!=value for key,value in {'memory_mib':512,'cpu_cores':1,'processes':64,'disk_mib':128,'time_seconds':60}.items()) or profiles!={'offline':{'mode':'none'}}:
            raise ValueError('Límites distintos del sandbox verificado')
        if data['approval_policy']['actions']!=['local_inference','sandbox_tests'] or data['paths']['allowed_write']!=['solution.py'] or budgets['external_cost_max']!=0:
            raise ValueError('Capacidades fuera del piloto local')
    return data


def load(path, workspace):
    path = Path(path)
    return validate(parse(path.read_text(encoding='utf-8'), path.suffix.lower() in {'.yaml','.yml'}), workspace)


def can_write(data, workspace, relative):
    """Denegación prevalece. No es una barrera contra carreras del filesystem."""
    target = relative_path(relative, workspace)
    denied = [relative_path(p, workspace) for p in data.get('paths', {}).get('denied_write', [])]
    allowed = [relative_path(p, workspace) for p in data.get('paths', {}).get('allowed_write', [])]
    return not any(target.is_relative_to(p) for p in denied) and any(target.is_relative_to(p) for p in allowed)


def verify_base(data, workspace):
    ref = data['project']['base_ref']
    if ref.startswith('-') or re.search(r'[\x00-\x20]', ref):
        raise ValueError('Referencia base inválida')
    result = subprocess.run(['git','-C',str(workspace),'rev-parse','--verify','--end-of-options',ref+'^{commit}'],
                            capture_output=True, text=True, timeout=15)
    if result.returncode or result.stdout.strip() != data['project']['resolved_base_sha']:
        raise ValueError('La revisión base cambió o no existe')
    head = subprocess.run(['git','-C',str(workspace),'rev-parse','HEAD'],capture_output=True,text=True,timeout=15)
    if head.returncode or head.stdout.strip() != data['project']['resolved_base_sha']:
        raise ValueError('El checkout no corresponde a la revisión base')
