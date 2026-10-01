"""Phase 2 durable synchronization kernel. Live writers are not enabled yet.

Adapter contract: read returns a revision; apply must preserve foreign content
(append-only or server-enforced compare-and-swap). find returns exact receipts,
never a title-based guess. Tests use simulated services, not real acceptance.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from worker_lock import worker_lock


class AuthExpired(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate(intent):
    fields = {'external_id', 'service', 'resource', 'revision', 'action', 'payload', 'links'}
    if not isinstance(intent, dict) or set(intent) != fields:
        raise ValueError('Invalid synchronization intent')
    for key in ('external_id', 'resource', 'revision'):
        if not isinstance(intent[key], str) or not re.fullmatch(r'[A-Za-z0-9_:/.-]{1,180}', intent[key]):
            raise ValueError('Invalid identity')
    if (intent['service'], intent['action']) not in {
        ('notion', 'append_note'), ('linear', 'create_issue'), ('linear', 'update_issue_state'),
        ('linear', 'attach_pr')}:
        raise ValueError('Operation not supported')
    if not isinstance(intent['payload'], dict) or set(intent['payload']) != {'text'}:
        raise ValueError('Invalid payload')
    text = intent['payload']['text']
    if not isinstance(text, str) or not 1 <= len(text.encode()) <= 16000:
        raise ValueError('Invalid text')
    if not isinstance(intent['links'], dict) or set(intent['links']) != {'requirement', 'run_id', 'pr'}:
        raise ValueError('Missing traceability')
    if any(not isinstance(v, str) or not 1 <= len(v) <= 500 for v in intent['links'].values()):
        raise ValueError('Invalid traceability')
    canonical(intent)


class SyncStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sync_ops ('
                       'id TEXT PRIMARY KEY, intent TEXT NOT NULL, state TEXT NOT NULL, '
                       'version INTEGER NOT NULL, paused INTEGER NOT NULL, reason TEXT, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS sync_events ('
                       'seq INTEGER PRIMARY KEY, operation TEXT, version INTEGER, state TEXT, reason TEXT, '
                       'UNIQUE(operation, version))')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db, op):
        row = db.execute('SELECT * FROM sync_ops WHERE id=?', (op,)).fetchone()
        if row is None:
            raise ValueError('Unknown operation')
        return dict(row)

    def _event(self, db, row, state, reason=None, receipt=None):
        version = row['version'] + 1
        db.execute('UPDATE sync_ops SET state=?,version=?,reason=?,receipt=? WHERE id=?',
                   (state, version, reason, canonical(receipt) if receipt else None, row['id']))
        db.execute('INSERT INTO sync_events(operation,version,state,reason) VALUES(?,?,?,?)',
                   (row['id'], version, state, reason))

    def prepare(self, intent):
        validate(intent)
        # Stable across runs: a retry with changed content must conflict.
        op = digest([intent['service'], intent['resource'], intent['external_id'], intent['action']])
        encoded = canonical(intent)
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM sync_ops WHERE id=?', (op,)).fetchone()
            if prior:
                if prior['intent'] != encoded:
                    raise RevisionConflict('Logical operation already has a different intent')
            else:
                db.execute('INSERT INTO sync_ops VALUES(?,?,?,0,0,NULL,NULL)', (op, encoded, 'prepared'))
                self._event(db, self._row(db, op), 'prepared')
        return op

    def get(self, op):
        with self.transaction() as db:
            row = self._row(db, op)
        row['intent'] = json.loads(row['intent'])
        row['receipt'] = json.loads(row['receipt']) if row['receipt'] else None
        return row

    def pause(self, op, paused, version):
        with self.transaction() as db:
            row = self._row(db, op)
            if row['version'] != version or row['state'] == 'confirmed':
                raise RevisionConflict('Stale version or terminal operation')
            db.execute('UPDATE sync_ops SET paused=? WHERE id=?', (int(paused), op))
            self._event(db, row, row['state'], 'paused' if paused else 'resumed')

    def _set(self, op, state, reason=None, receipt=None):
        with self.transaction() as db:
            row = self._row(db, op)
            if row['state'] == 'confirmed':
                raise RevisionConflict('Terminal operation')
            self._event(db, row, state, reason, receipt)

    def _confirm(self, op, intent, receipt):
        expected = {'operation': op, 'intent_hash': digest(intent),
                    'service': intent['service'], 'resource': intent['resource']}
        if not isinstance(receipt, dict) or set(receipt) != set(expected) | {'remote_id'}:
            raise RevisionConflict('Invalid receipt')
        if any(receipt[k] != v for k, v in expected.items()):
            raise RevisionConflict('Receipt belongs to another operation')
        if not isinstance(receipt['remote_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', receipt['remote_id']):
            raise RevisionConflict('Invalid remote identity')
        self._set(op, 'confirmed', receipt=receipt)

    def execute_simulated(self, op, adapter, allowed_resources, enabled=True):
        """Test harness only. This API deliberately rejects production adapters."""
        if type(adapter) is not SimulatedAdapter:
            raise ValueError('Live writers require separate acceptance and authorization')
        with worker_lock(self.path, op) as acquired:
            if not acquired:
                raise RevisionConflict('Operation already executing')
            row = self.get(op)
            intent = row['intent']
            if row['state'] == 'confirmed':
                return row
            if not enabled:
                return row  # Pending intent survives disabling the connector.
            if (intent['service'], intent['resource']) not in allowed_resources:
                raise ValueError('Resource denied')
            if row['paused']:
                raise RevisionConflict('Operation paused')
            # An in-flight record after a crash is reconciled, never re-sent.
            if row['state'] in {'in_flight', 'uncertain'}:
                try:
                    matches = adapter.find(op)
                    if len(matches) != 1:
                        raise RevisionConflict('Remote outcome ambiguous')
                    self._confirm(op, intent, matches[0])
                except Exception:
                    self._set(op, 'uncertain', 'reconciliation_required')
                return self.get(op)
            try:
                revision = adapter.read_revision(intent['resource'])
                if revision != intent['revision']:
                    raise RevisionConflict('Document changed')
            except AuthExpired:
                self._set(op, 'failed', 'credential_expired')
                return self.get(op)
            except RevisionConflict:
                self._set(op, 'failed', 'revision_conflict')
                return self.get(op)
            # Pause and dispatch authorization serialize in this transaction.
            with self.transaction() as db:
                current = self._row(db, op)
                if current['paused'] or current['version'] != row['version']:
                    raise RevisionConflict('Authorization changed')
                self._event(db, current, 'in_flight')
            try:
                receipt = adapter.apply(op, intent)
                self._confirm(op, intent, receipt)
            except Exception:
                self._set(op, 'uncertain', 'reconciliation_required')
            return self.get(op)


class SimulatedAdapter:
    """In-memory remote with append-only writes; no network or credentials."""
    def __init__(self):
        self.revision = 'r1'
        self.foreign_content = 'Human-owned content'
        self.notes = []
        self.receipts = []
        self.expired = False
        self.timeout_after_write = False
        self.calls = 0

    def read_revision(self, resource):
        if self.expired:
            raise AuthExpired('Credential expired')
        return self.revision

    def apply(self, op, intent):
        self.calls += 1
        self.notes.append(intent['payload']['text'])
        receipt = {'operation': op, 'intent_hash': digest(intent), 'service': intent['service'],
                   'resource': intent['resource'], 'remote_id': 'remote-' + str(self.calls)}
        self.receipts.append(receipt)
        if self.timeout_after_write:
            raise TimeoutError('Simulated response lost')
        return receipt

    def find(self, op):
        if self.expired:
            raise AuthExpired('Credential expired')
        return [r for r in self.receipts if r['operation'] == op]
