"""Consulta local de evidencia histórica, sin importar agentes ni ejecutar modelos."""
import argparse
from collections import deque
import json
from pathlib import Path
import re
import sys
import sqlite3

ROOT = Path(__file__).resolve().parent
LOGS = ROOT.parents[1] / 'logs' / 'agents'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')
STATES = {'pending', 'developing', 'reviewing', 'testing', 'pass', 'unresolved',
          'error', 'blocked_infrastructure'}
KINDS = {'round_start', 'agent_start', 'llm', 'llm_error', 'syntax_error',
         'test', 'flow_error'}
MAX_BYTES = 16 * 1024 * 1024


def safe_path(root, *parts):
    root = Path(root).resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Ruta fuera del directorio de evidencia')
    return path


def validate_id(run_id):
    if not ID.fullmatch(run_id):
        raise ValueError('Identificador de ejecución inválido')


def event_summary(event):
    # No mostrar prompts, código, stdout, stderr ni mensajes externos.
    kind = event.get('kind')
    result = {'kind': kind if isinstance(kind, str) and kind in KINDS else 'other'}
    timestamp = event.get('timestamp')
    if isinstance(timestamp, str) and re.fullmatch(r'[0-9T:+.Z-]{10,40}', timestamp):
        result['timestamp'] = timestamp
    for name in ('round', 'duration_ms', 'eval_count', 'prompt_eval_count', 'exit_code'):
        value = event.get(name)
        if type(value) is int:
            result[name] = value
    if isinstance(event.get('agent'), str) and event['agent'] in {'planner', 'developer', 'tester', 'reviewer'}:
        result['agent'] = event['agent']
    if isinstance(event.get('status'), str) and event['status'] in {'complete', 'pass', 'fail', 'infrastructure_error'}:
        result['status'] = event['status']
    return result


def history(run_id, root=ROOT, logs=LOGS, limit=100):
    validate_id(run_id)
    if not 1 <= limit <= 1000:
        raise ValueError('El límite debe estar entre 1 y 1000')
    path = safe_path(logs, run_id + '.jsonl')
    state_path = safe_path(Path(root) / 'runs', run_id, 'state.json')
    if not path.exists() and not state_path.exists():
        raise ValueError('Ejecución no encontrada')
    warnings = []
    events = deque(maxlen=limit)
    count = 0
    if path.exists():
        if path.stat().st_size > MAX_BYTES:
            warnings.append('Log demasiado grande; consulta omitida')
        else:
            with path.open(encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict) or item.get('run_id') != run_id:
                            raise ValueError()
                        events.append(event_summary(item))
                        count += 1
                    except ValueError:
                        if 'Log incompleto o inválido' not in warnings:
                            warnings.append('Log incompleto o inválido')
    return {'run_id': run_id, 'events': list(events), 'valid_events': count,
            'truncated': count > limit, 'warnings': warnings}


def status(root=ROOT, logs=LOGS):
    runs = Path(root) / 'runs'
    ids = {p.name for p in runs.iterdir() if p.is_dir() and ID.fullmatch(p.name)} if runs.exists() else set()
    ids.update(p.stem for p in Path(logs).glob('*.jsonl') if ID.fullmatch(p.stem))
    result = []
    for run_id in sorted(ids):
        record = {'run_id': run_id, 'recorded_status': 'unknown',
                  'activity': 'unknown', 'remaining_budget': None}
        try:
            path = safe_path(runs, run_id, 'state.json')
            if path.exists():
                if path.stat().st_size > MAX_BYTES:
                    raise ValueError('Estado demasiado grande')
                state = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(state, dict) or state.get('run_id') != run_id:
                    raise ValueError('Estado inválido o de otra ejecución')
                value = state.get('status')
                record['recorded_status'] = value if isinstance(value, str) and value in STATES else 'unknown'
                if type(state.get('current_round')) is int:
                    record['round'] = state['current_round']
            detail = history(run_id, root, logs, limit=1)
            record['last_event'] = detail['events'][-1] if detail['events'] else None
            record['warnings'] = detail['warnings']
        except (ValueError, OSError):
            record['warnings'] = ['Evidencia ausente, inválida o inaccesible']
        if record['recorded_status'] == 'blocked_infrastructure':
            record['next_action'] = 'Revisar disponibilidad del ejecutor Docker'
        result.append(record)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'closeout':
        from integration_closeout import main as closeout_main
        return closeout_main(argv[1:])
    if argv and argv[0] == 'linear-sync-states':
        from integration_linear_state import main as states_main
        return states_main(argv[1:])
    if argv and argv[0] == 'coolify-inventory':
        from integration_coolify import main as coolify_main
        return coolify_main(argv[1:])
    if argv and argv[0] == 'linear-ticket':
        from integration_linear import main as linear_main
        return linear_main(argv[1:])
    if argv and argv[0] == 'notion-note':
        from integration_notion import main as notion_main
        return notion_main(argv[1:])
    if argv and argv[0] in {'integration-status', 'integration-probe', 'integration-login'}:
        from integration_cli import main as integration_main
        return integration_main(argv)
    if argv and argv[0] in {'self-test','manifest-check','run-stub','run-local','pause','resume',
                           'phase3-prepare','phase3-review','phase3-check-execution',
                           'phase3-build-manifest','phase3-run-sandbox','phase3-cleanup-sandbox',
                           'publication-status','publication-resolve','publication-pause','publication-resume',
                           'pr-prepare','pr-open','pr-status','pr-resolve','pr-pause','pr-resume','delivery-prepare','ci-check'}:
        from controller_cli import main as control_main
        return control_main(argv)
    parser = argparse.ArgumentParser(description=__doc__,epilog='Controlador: manifest-check, self-test, run-stub, run-local, pause, resume; entregas: delivery-prepare; PR: pr-prepare, pr-open, pr-status, pr-resolve, pr-pause, pr-resume.')
    commands = parser.add_subparsers(dest='command', required=True)
    show = commands.add_parser('status')
    show.add_argument('--json', action='store_true')
    show.add_argument('--summary', action='store_true', help='Totales por estado registrado, conservando avisos')
    log = commands.add_parser('log')
    log.add_argument('run_id')
    log.add_argument('--limit', type=int, default=100)
    log.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        database = ROOT/'.state/controller.sqlite'
        controlled = []
        if database.exists():
            from controller import read_runs
            if args.command == 'log':
                validate_id(args.run_id)
                if not 1 <= args.limit <= 1000:
                    raise ValueError('Límite inválido')
            controlled = read_runs(database, args.run_id if args.command == 'log' else None)
        if args.command == 'status':
            controlled_ids = {row['run_id'] for row in controlled}
            data = controlled + [row for row in status(ROOT, LOGS) if row['run_id'] not in controlled_ids]
            if args.summary:
                from status_summary import summarize_runs
                records = data
                data = {**summarize_runs(records),
                        'by_mode': {mode: summarize_runs([row for row in records if row.get('mode', 'legacy') == mode])
                                    for mode in ('local', 'stub', 'legacy')},
                        'warnings': [{'run_id': row['run_id'], 'warnings': row['warnings']}
                                     for row in records if row.get('warnings')]}
        elif controlled:
            data = {'run_id':args.run_id,'events':controlled[-args.limit:],
                    'valid_events':len(controlled),'truncated':len(controlled)>args.limit,'warnings':[]}
        else:
            data = history(args.run_id, ROOT, LOGS, limit=args.limit)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.command == 'status':
            print('Estado registrado | simulaciones identificadas como stub; actividad de workers no verificada')
            if args.summary:
                print(f"Total: {data['total']}")
                for mode, summary in data['by_mode'].items():
                    print(f"{mode}: {summary['total']} | " + ', '.join(f'{state}: {count}' for state, count in summary['by_status'].items()))
                for item in data['warnings']:
                    for warning in item['warnings']:
                        print(f"Aviso ({item['run_id']}): {warning}")
                return 0
            for row in data:
                print(f"{row['run_id']}  {row['recorded_status']}")
                if 'elapsed_ms' in row:
                    print(f"  Tiempo transcurrido: {row['elapsed_ms']} ms | último evento: {(row.get('last_event') or {}).get('kind', 'sin eventos')}")
                if row.get('mode') == 'stub':
                    print(f"  Simulación local | versión {row['version']} | tiempo disponible {row['remaining_budget']['active_ms']} ms")
                elif row.get('mode')=='local':
                    print(f"  Agentes locales | versión {row['version']} | tiempo disponible {row['remaining_budget']['active_ms']} ms")
                for warning in row.get('warnings', []):
                    print('  Aviso: ' + warning)
                if row.get('next_action'):
                    print('  Acción: ' + row['next_action'])
            if not data:
                print('Sin ejecuciones registradas')
        else:
            for event in data['events']:
                print(json.dumps(event, ensure_ascii=False))
            for warning in data['warnings']:
                print('Aviso: ' + warning)
            if data['truncated']:
                print(f"Mostrando los últimos {args.limit} eventos de {data['valid_events']}")
        return 0
    except (ValueError, OSError, sqlite3.Error):
        print('No se pudo consultar: identificador, límite o evidencia inválidos/inaccesibles.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
