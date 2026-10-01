"""Operación explícita del controlador y propuestas de publicación revisadas."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess

from controller import Store, read_runs
import controller_gate
import manifest

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT/'.state/controller.sqlite'


def tracked_inputs(root=ROOT):
    paths = {*root.glob('*.py'), *root.glob('*.cmd'), *(root/'tests').glob('*.py'),
             *(root/'schemas').glob('*.json'), root/'pyproject.toml', root/'uv.lock',
             root/'config/agents.yaml',root/'config/pilot-manifest.json',root/'config/phase3-input-lock.json',
             *(root/'fixtures/phase3-contract-v1').glob('*.json')}
    return sorted(str(p.relative_to(root)).replace('\\','/') for p in paths)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    phase3=sub.add_parser('phase3-prepare',help='Prepare pinned phase3 input/file plan; no code execution')
    phase3.add_argument('--bundle',type=Path,default=ROOT/'fixtures/phase3-contract-v1')
    phase3.add_argument('--plan',type=Path,help='Optional typed planner output to validate')
    review=sub.add_parser('phase3-review',help='Bounded local correction/review of a file plan; no product execution')
    review.add_argument('--run-id',required=True)
    review.add_argument('--seed',type=Path,help='New run only: original typed candidate, possibly defective')
    review.add_argument('--export',type=Path)
    review.add_argument('--section',choices=['api','data','subscriptions','manifest'],help='New run only: review a typed documentary section against the frozen contract')
    check = sub.add_parser('manifest-check')
    check.add_argument('manifest',nargs='?',default=str(ROOT/'config/pilot-manifest.json'))
    run = sub.add_parser('run-stub')
    run.add_argument('--until',choices=['planning','implementing','validating','awaiting_review','delivered'],default='delivered')
    run.add_argument('--parent-id')
    run.add_argument('--run-id',help='Continuar una simulación existente después de reanudarla')
    local=sub.add_parser('run-local')
    local.add_argument('--run-id')
    local.add_argument('--requirement-id', help='Read a structured requirement from the authorized Notion page; pin an immutable snapshot')
    local.add_argument('--connected', action='store_true', help='Nueva ejecución con tickets en el proyecto AgentApp autorizado')
    local.add_argument('--pilot', choices=['status-summary','attention-summary'], default='status-summary')
    local.add_argument('--until',choices=['planning','implementing','validating','awaiting_review','delivered'],default='delivered')
    for name in ('pause','resume'):
        cmd = sub.add_parser(name)
        cmd.add_argument('run_id')
        cmd.add_argument('--version',type=int,required=True,help='Versión mostrada por status --json; evita actuar sobre estado obsoleto')
    for name in ('publication-status','publication-resolve','publication-pause','publication-resume'):
        cmd=sub.add_parser(name)
        cmd.add_argument('job_id')
        if name!='publication-status':cmd.add_argument('--version',type=int,required=True)
        if name=='publication-resolve':
            cmd.add_argument('--decision',required=True,choices=['confirm-applied','confirm-not-applied','defer'])
    for name in ('pr-prepare','pr-open','pr-status','pr-resolve','pr-pause','pr-resume'):
        cmd=sub.add_parser(name)
        cmd.add_argument('job_id')
        if name=='pr-prepare':
            cmd.add_argument('--title',required=True)
            cmd.add_argument('--body-file',type=Path,required=True)
        if name=='pr-open':cmd.add_argument('--approved-digest',required=True)
        if name in {'pr-resolve','pr-pause','pr-resume'}:cmd.add_argument('--version',type=int,required=True)
        if name=='pr-resolve':cmd.add_argument('--decision',required=True,choices=['confirm-applied','confirm-not-applied','defer'])
    delivery=sub.add_parser('delivery-prepare')
    delivery.add_argument('run_id')
    delivery.add_argument('--branch',required=True)
    ci=sub.add_parser('ci-check')
    ci.add_argument('job_id')
    args = parser.parse_args(argv)
    try:
        if args.command=='phase3-review':
            controller_gate.require_green()
            import phase3_review
            journal=phase3_review.Journal()
            if args.section and not args.seed:raise ValueError('Section is bound by a new seed; omit --section when resuming')
            if args.seed:journal.create(args.run_id,phase3_review.preparation.read_json(args.seed),section=args.section)
            result=phase3_review.run(journal,args.run_id)
            if args.export:phase3_review.export(journal,args.run_id,args.export)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['state'] in {'reviewed_plan','reviewed_contract_section'} else 2
        elif args.command=='phase3-prepare':
            controller_gate.require_green()
            import phase3_prepare
            plan=phase3_prepare.read_json(args.plan) if args.plan else None
            result=phase3_prepare.snapshot(args.bundle,ROOT/'.state/phase3/snapshots',plan)
        elif args.command=='ci-check':
            from github_ci import inspect
            result=inspect(args.job_id)
        elif args.command=='delivery-prepare':
            from controller_delivery import prepare
            result=prepare(DATABASE,args.run_id,args.branch)
        elif args.command.startswith('pr-'):
            import github_pr
            if args.command=='pr-prepare':
                if args.body_file.stat().st_size>48000:raise ValueError('Descripción demasiado grande')
                result=github_pr.prepare(args.job_id,args.title,args.body_file.read_text(encoding='utf-8'))
            elif args.command=='pr-open':result=github_pr.open_pr(args.job_id,args.approved_digest)
            elif args.command=='pr-status':result=github_pr.status(args.job_id)
            else:result=github_pr.control(args.job_id,args.decision if args.command=='pr-resolve'
                                         else args.command.removeprefix('pr-'),args.version)
        elif args.command.startswith('publication-'):
            from github_publish import publication_status,publication_control
            result=(publication_status(args.job_id) if args.command=='publication-status' else
                    publication_control(args.job_id,args.decision if args.command=='publication-resolve'
                                        else args.command.removeprefix('publication-'),args.version))
        elif args.command == 'self-test':
            result = {'suite_identity':controller_gate.self_test(),'scope':'controller_local'}
        elif args.command == 'manifest-check':
            contract = manifest.load(args.manifest,ROOT)
            manifest.verify_base(contract,ROOT)
            result = {'valid':True,'manifest_hash':manifest.identity(contract),'execution_authorized':False}
        elif args.command == 'run-stub':
            controller_gate.require_green()
            if args.run_id and args.parent_id:
                raise ValueError('Elegir ejecución existente o padre de una nueva')
            if args.run_id:
                if not DATABASE.exists():
                    raise ValueError('Ejecución inexistente')
                row = Store(DATABASE).get(args.run_id)
            else:
                contract = manifest.load(ROOT/'config/pilot-manifest.json',ROOT)
                row = Store(DATABASE).create(contract,ROOT,tracked_inputs(),args.parent_id)
            from controlled_flow import ControlledStubFlow
            result = ControlledStubFlow().kickoff(inputs={'run_id':row['id'],'database':str(DATABASE),'until':args.until})
            metrics = Store(DATABASE).metrics(row['id'])
            destination = ROOT/'.state/metrics'
            destination.mkdir(exist_ok=True)
            (destination/(row['id']+'.json')).write_text(json.dumps(metrics,indent=2),encoding='utf-8')
        elif args.command=='run-local':
            controller_gate.require_green()
            from local_control import LocalStore
            from local_flow import ControlledLocalFlow,export_run
            store=LocalStore(DATABASE)
            if args.run_id:
                if args.connected or args.pilot!='status-summary' or args.requirement_id:
                    raise ValueError('No se amplían permisos de una ejecución existente')
                row=store.get(args.run_id)
                if row['execution_kind']!='local':raise ValueError('Run no local')
            else:
                contract=manifest.load(ROOT/'config/local-pilot.json',ROOT)
                if args.requirement_id:
                    if not args.connected or args.pilot!='status-summary':
                        raise ValueError('Use --connected --requirement-id without --pilot')
                    import asyncio
                    from integration_requirement import capture, bind
                    contract=bind(contract,asyncio.run(capture(args.requirement_id)))
                if args.pilot=='attention-summary' and not args.connected:
                    raise ValueError('El nuevo piloto requiere --connected y contrato 1.2')
                if args.connected and not args.requirement_id:
                    from integration_linear import PROJECT
                    contract['schema_version']='1.2'
                    contract['allowed_resources']['linear_projects']=[PROJECT]
                    contract['allowed_resources']['notion_pages']=['3e567de3-6dff-81c6-a414-c87fa86f8a1e']
                    if args.pilot=='attention-summary':
                        from local_pilot import pilot_spec,digest
                        contract['pipeline']['kind']='attention_summary_v1'
                        requirement,tests,_=pilot_spec(contract)
                        contract['pipeline']['requirement_sha256']=digest(requirement)
                        contract['pipeline']['tests_sha256']=digest(tests)
                        contract['project']['name']='resumen-atencion-fase2'
                    manifest.validate(contract, ROOT)
                row=store.create(contract,ROOT,[*tracked_inputs(),'config/local-pilot.json'])
            result=ControlledLocalFlow().kickoff(inputs={'run_id':row['id'],'database':str(DATABASE),'until':args.until})
            if result.get('busy'):
                print(json.dumps(result,ensure_ascii=False,indent=2))
                return 2
            result['evidence_directory']=str(export_run(store,row['id']))
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if not result.get('error_type') and result['state'] in {'delivered',args.until,'paused'} else 2
        else:
            if not DATABASE.exists():
                raise ValueError('No hay ejecuciones del controlador')
            store = Store(DATABASE)
            if args.command == 'pause':
                row = store.pause(args.run_id,args.version)
            else:
                row = store.resume(args.run_id,args.version,ROOT)
            result = {'run_id':row['id'],'state':row['state'],'version':row['version'],'scope':row['execution_kind']}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except ValueError as exc:
        print('Operación bloqueada: '+str(exc))
        return 2
    except RuntimeError:
        print('Operación bloqueada: no se pudo verificar el estado remoto. Se conserva el registro para reconciliar.')
        return 2
    except (OSError,sqlite3.Error,subprocess.TimeoutExpired):
        print('Operación bloqueada por almacenamiento, proceso o tiempo de espera; revisar entorno local.')
        return 2
