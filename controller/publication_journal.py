"""Registro transaccional de una publicación revisada, fuera del checkout.

Sólo el publicador de confianza lo usa; no es una herramienta del modelo.
El lease de Git protege la creación de la referencia. No garantiza idempotencia
de PRs ni autoriza publicaciones automáticas.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path):
        self.path = path
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS publication (
                id INTEGER PRIMARY KEY CHECK(id=1), intent TEXT NOT NULL,
                state TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                version INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                actor TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL)''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
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

    def _row(self, db, version=None):
        row = db.execute('SELECT * FROM publication WHERE id=1').fetchone()
        if row is None:
            raise ValueError('Publicación no preparada')
        if version is not None and row['version'] != version:
            raise ValueError('Versión obsoleta; volver a consultar estado')
        return row

    def _event(self, db, row, kind, actor, evidence):
        timestamp = stamp()
        db.execute('UPDATE publication SET version=?,updated_at=? WHERE id=1',
                   (row['version'] + 1, timestamp))
        db.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                   (row['version'] + 1, timestamp, actor, kind,
                    json.dumps(evidence, sort_keys=True)))

    def prepare(self, intent):
        encoded = json.dumps(intent, sort_keys=True)
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM publication WHERE id=1').fetchone()
            if previous:
                if previous['intent'] != encoded:
                    raise ValueError('La intención original cambió')
                return
            timestamp = stamp()
            db.execute('INSERT INTO publication VALUES(1,?,?,0,0,0,?,?)',
                       (encoded, 'prepared', timestamp, timestamp))
            self._event(db, self._row(db), 'prepared', 'controller', {})

    def status(self):
        with self.transaction() as db:
            row = dict(self._row(db))
            row['intent'] = json.loads(row['intent'])
            row['events'] = [dict(e) for e in db.execute('SELECT * FROM events ORDER BY version')]
            for event in row['events']:
                event['evidence'] = json.loads(event['evidence'])
            return row

    def claim(self, max_attempts=3):
        with self.transaction() as db:
            row = self._row(db)
            if row['paused'] or row['state'] != 'prepared':
                raise ValueError('Publicación pausada o pendiente de reconciliación')
            if row['attempts'] >= max_attempts:
                raise ValueError('Presupuesto de intentos agotado')
            db.execute("UPDATE publication SET state='in_flight',attempts=attempts+1 WHERE id=1")
            self._event(db, row, 'dispatched', 'controller', {})

    def uncertain(self):
        with self.transaction() as db:
            row = self._row(db)
            if row['state'] == 'in_flight':
                db.execute("UPDATE publication SET state='uncertain' WHERE id=1")
                self._event(db, row, 'uncertain', 'controller', {})

    def resolve(self, decision, evidence, actor, version=None):
        # Evidence is constructed by the Git adapter after querying the remote.
        # CLI callers never supply arbitrary evidence or a remote SHA.
        with self.transaction() as db:
            row = self._row(db, version)
            late_after_resolution = (row['state'] == 'prepared' and row['attempts'] > 0
                                     and decision == 'confirm-applied')
            if row['state'] not in {'in_flight', 'uncertain'} and not late_after_resolution:
                raise ValueError('Operación no reconciliable')
            intent = json.loads(row['intent'])
            if evidence.get('repository') != intent['repository'] or evidence.get('branch') != intent['branch']:
                raise ValueError('Evidencia de otro recurso')
            if decision == 'confirm-applied':
                if evidence.get('sha') != intent['commit']:
                    raise ValueError('El SHA remoto no coincide')
                state = 'confirmed'
            elif decision == 'confirm-not-applied':
                if evidence.get('sha') is not None or evidence.get('retry_safety') != 'git_create_only_lease':
                    raise ValueError('No se demostró ausencia y repetición segura')
                state = 'prepared'
            elif decision == 'defer':
                state = 'uncertain'
            else:
                raise ValueError('Resolución inválida')
            db.execute('UPDATE publication SET state=? WHERE id=1', (state,))
            self._event(db, row, decision, actor, evidence)

    def pause(self, paused, version, actor):
        with self.transaction() as db:
            row = self._row(db, version)
            if row['state'] == 'confirmed':
                raise ValueError('Publicación cerrada')
            db.execute('UPDATE publication SET paused=? WHERE id=1', (int(paused),))
            self._event(db, row, 'paused' if paused else 'resumed', actor, {})
