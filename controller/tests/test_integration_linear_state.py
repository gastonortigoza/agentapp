import asyncio
import copy
import pytest
import integration_linear as l
import integration_linear_state as states
from integration_sync import SyncStore, RevisionConflict, digest


@pytest.fixture
def setup(tmp_path):
    store=SyncStore(tmp_path/'states.sqlite')
    intent=l.proposal({'external_id':'PLAN-run-1','title':'Step','description':'Human-preserving step',
        'requirement':'https://app.notion.com/p/example','run_id':'run','pr':'pending'})
    op=store.prepare(intent)
    store._confirm(op,intent,{'operation':op,'intent_hash':digest(intent),'service':'linear',
                            'resource':l.PROJECT,'remote_id':'uuid-1'})
    fields=l.ticket_fields(op,intent)
    class Remote:
        def __init__(self):
            self.issue={'id':'uuid-1','projectId':l.PROJECT,'teamId':l.TEAM,
                        'title':fields['title'],'description':fields['description'],
                        'status':'Todo','updatedAt':'2026-09-27T01:00:00Z'}
            self.writes=0
            self.lose=False
            self.apply=True
            self.reads=0
            self.edit=False
        def validate(self,name,args):
            assert name=='save_issue' and set(args)=={'id','state'}
        async def call(self,name,args):
            if name=='get_issue':
                self.reads+=1
                if self.edit and self.reads==2: self.issue['updatedAt']='2026-09-27T02:00:00Z'
                return copy.deepcopy(self.issue)
            assert name=='save_issue'
            self.writes+=1
            if self.apply:
                self.issue['status']=next(n for n,v in states.STATES.items() if v[0]==args['state'])
                self.issue['updatedAt']='2026-09-27T03:00:00Z'
            if self.lose: raise TimeoutError()
            return copy.deepcopy(self.issue)
    return store,store.get(op),Remote()


def update(setup,target='Done',authorize=lambda:None):
    store,creation,remote=setup
    return asyncio.run(states.update_one(store,creation,target,remote,authorize))


def test_no_state_write_without_atomic_condition(setup):
    _,_,remote=setup; before=copy.deepcopy(remote.issue)
    assert update(setup,'In Progress')['reason']=='atomic_state_update_unavailable'
    assert update(setup)['reason']=='atomic_state_update_unavailable'
    assert remote.writes==0 and remote.issue==before


def test_existing_uncertain_applied_reconciles_without_resend(setup):
    store,creation,remote=setup
    row=update(setup)
    store._set(row['id'],'uncertain','reconciliation_required')
    remote.issue['status']='Done'
    assert update(setup)['state']=='confirmed'
    assert remote.writes==0


def test_existing_uncertain_without_effect_never_resends(setup):
    store,creation,remote=setup
    row=update(setup)
    store._set(row['id'],'uncertain','reconciliation_required')
    assert update(setup)['state']=='uncertain'
    assert update(setup)['state']=='uncertain'
    assert remote.writes==0


def test_detected_external_edit_blocks(setup):
    _,_,remote=setup;remote.edit=True
    assert update(setup)['state']=='failed'
    assert remote.writes==0


def test_human_description_is_not_overwritten(setup):
    _,_,remote=setup;remote.issue['description']+='\nHuman content'
    with pytest.raises(RevisionConflict):update(setup)
    assert remote.writes==0


def test_pause_during_preflight_blocks(setup):
    _,_,remote=setup
    def authorize():
        raise RevisionConflict('Run paused')
    with pytest.raises(RevisionConflict):update(setup,authorize=authorize)
    assert remote.writes==0


def test_done_is_not_regressed(setup):
    _,_,remote=setup;remote.issue['status']='Done'
    assert update(setup,'In Progress')['state']=='failed'
    assert remote.writes==0


def test_unmanaged_ticket_is_denied(setup):
    _,creation,remote=setup;creation['intent']['resource']='foreign'
    with pytest.raises(RevisionConflict):update(setup)
    assert remote.writes==0
