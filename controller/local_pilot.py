"""Requisito y pruebas independientes fijados antes de generar la solución."""
import ast
import hashlib

REQUIREMENT = '''Implementá summarize_runs(records) en Python. records debe ser una lista de diccionarios.
Devolvé {"total": cantidad de registros, "by_status": diccionario de conteos por estado}.
Leé recorded_status de cada registro. Un string se normaliza con strip(); si falta, está vacío
o no es string, usá "unknown". Ordená las claves de by_status alfabéticamente.
No modifiques la entrada. Una entrada que no sea lista o un elemento que no sea diccionario
debe lanzar TypeError. No uses imports, E/S, globals, decorators, dunders ni otras funciones.
La salida debe contener únicamente def summarize_runs(records): y su cuerpo, sin markdown.
Builtins permitidos: isinstance, list, dict, str, len, sorted, TypeError.
Métodos permitidos: get, strip, items, keys, values. No afirmes haber ejecutado pruebas.'''

TESTS = '''import unittest
import copy
from solution import summarize_runs

class SummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]),{'total':0,'by_status':{}})
    def test_counts(self):
        records=[{'recorded_status':'pass'},{'recorded_status':'error'},{'recorded_status':'pass'}]
        self.assertEqual(summarize_runs(records),{'total':3,'by_status':{'error':1,'pass':2}})
    def test_unknown(self):
        records=[{}, {'recorded_status':None},{'recorded_status':[]},{'recorded_status':23}]
        self.assertEqual(summarize_runs(records),{'total':4,'by_status':{'unknown':4}})
    def test_whitespace(self):
        self.assertEqual(summarize_runs([{'recorded_status':'  pass  '},{'recorded_status':' '}]),{'total':2,'by_status':{'pass':1,'unknown':1}})
    def test_sorted(self):
        result=summarize_runs([{'recorded_status':'z'},{'recorded_status':'a'}])
        self.assertEqual(list(result['by_status']),['a','z'])
    def test_unchanged(self):
        records=[{'recorded_status':' pass ','nested':[1,2]},{}]
        previous=copy.deepcopy(records)
        summarize_runs(records)
        self.assertEqual(records,previous)
    def test_bad_container(self):
        for records in [None,{},'text',(),42]:
            with self.subTest(records=records),self.assertRaises(TypeError): summarize_runs(records)
    def test_bad_entry(self):
        for record in [None,[],23,'pass']:
            with self.subTest(record=record),self.assertRaises(TypeError): summarize_runs([record])
    def test_mixed_bad_entry(self):
        with self.assertRaises(TypeError):summarize_runs([{},7])
    def test_does_not_use_status_alias(self):
        self.assertEqual(summarize_runs([{'status':'pass'}]),{'total':1,'by_status':{'unknown':1}})
    def test_boolean_status(self):
        self.assertEqual(summarize_runs([{'recorded_status':True}]),{'total':1,'by_status':{'unknown':1}})
    def test_unicode_and_extra_fields(self):
        self.assertEqual(summarize_runs([{'recorded_status':' revisión ','ignored':'x'}]),{'total':1,'by_status':{'revisión':1}})
'''


ATTENTION_REQUIREMENT = REQUIREMENT.replace(
    'Devolvé {"total": cantidad de registros, "by_status": diccionario de conteos por estado}.',
    'Devolvé {"total": cantidad de registros, "by_status": diccionario de conteos por estado, "needs_attention": cantidad que requiere atención}. '
    'Requieren atención exactamente los estados normalizados: failed, uncertain, blocked_evidence, blocked_budget, uncertain_operation. '
    'El conteo distingue mayúsculas. Los demás estados, incluido unknown, no requieren atención.')

ATTENTION_TESTS = '''import unittest
import copy
from solution import summarize_runs

class AttentionTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]), {'total':0,'by_status':{},'needs_attention':0})
    def test_all_attention_states(self):
        states=['failed','uncertain','blocked_evidence','blocked_budget','uncertain_operation']
        self.assertEqual(summarize_runs([{'recorded_status':s} for s in states]),
                         {'total':5,'by_status':dict(sorted((s,1) for s in states)),'needs_attention':5})
    def test_repeated_and_mixed(self):
        result=summarize_runs([{'recorded_status':s} for s in ['failed','failed','delivered','planning']])
        self.assertEqual(result,{'total':4,'by_status':{'delivered':1,'failed':2,'planning':1},'needs_attention':2})
    def test_normalization_and_case(self):
        self.assertEqual(summarize_runs([{'recorded_status':s} for s in [' failed ','Failed',' ']]),
                         {'total':3,'by_status':{'Failed':1,'failed':1,'unknown':1},'needs_attention':1})
    def test_unknown_types_and_alias(self):
        rows=[{}, {'recorded_status':None},{'recorded_status':True},{'recorded_status':[]},{'status':'failed'}]
        self.assertEqual(summarize_runs(rows),{'total':5,'by_status':{'unknown':5},'needs_attention':0})
    def test_sorted_unicode_and_input_preserved(self):
        rows=[{'recorded_status':' revisión ','nested':[1,2]},{'recorded_status':'failed'}]
        saved=copy.deepcopy(rows);result=summarize_runs(rows)
        self.assertEqual(rows,saved)
        self.assertEqual(list(result['by_status']),['failed','revisión'])
        self.assertEqual(result['needs_attention'],1)
    def test_bad_container(self):
        for value in [None,{},'text',(),42]:
            with self.subTest(value=value),self.assertRaises(TypeError): summarize_runs(value)
    def test_bad_record(self):
        for value in [None,[],3,'failed']:
            with self.subTest(value=value),self.assertRaises(TypeError): summarize_runs([{},value])
'''


def pilot_spec(contract):
    kind=contract['pipeline']['kind']
    if kind=='remote_summary_v1':
        from integration_requirement import pilot
        return pilot(contract['pipeline']['source'])
    if kind=='status_summary_v1': return REQUIREMENT, TESTS, 'status_summary'
    if kind=='attention_summary_v1': return ATTENTION_REQUIREMENT, ATTENTION_TESTS, 'attention_summary'
    raise ValueError('Piloto no soportado')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_candidate(code):
    """Lenguaje reducido del piloto; Docker sigue siendo la barrera de ejecución."""
    if not isinstance(code,str) or not 1 <= len(code.encode('utf-8')) <= 24000:
        raise ValueError('Tamaño de candidato inválido')
    tree=ast.parse(code)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):
        raise ValueError('Se exige una sola función')
    function=tree.body[0]
    if function.name!='summarize_runs' or function.decorator_list or function.returns or function.type_params:
        raise ValueError('Firma no permitida')
    args=function.args
    if len(args.args)!=1 or args.args[0].arg!='records' or args.args[0].annotation or args.defaults or args.kwonlyargs or args.posonlyargs or args.vararg or args.kwarg:
        raise ValueError('Argumentos no permitidos')
    forbidden=(ast.Import,ast.ImportFrom,ast.Global,ast.Nonlocal,ast.ClassDef,ast.AsyncFunctionDef,
               ast.Lambda,ast.With,ast.AsyncWith,ast.Yield,ast.YieldFrom,ast.Await)
    for node in ast.walk(tree):
        if isinstance(node,forbidden) or isinstance(node,ast.FunctionDef) and node is not function:
            raise ValueError('Construcción fuera del piloto')
        if isinstance(node,ast.Attribute) and node.attr not in {'get','strip','items','keys','values'}:
            raise ValueError('Atributo no permitido')
        if isinstance(node,ast.Name) and node.id.startswith('__'):
            raise ValueError('Nombre no permitido')
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name):
                if node.func.id not in {'isinstance','list','dict','str','len','sorted','TypeError'}:
                    raise ValueError('Llamada no permitida')
            elif not isinstance(node.func,ast.Attribute):
                raise ValueError('Llamada indirecta no permitida')
    return code.strip()+'\n'
