"""Exclusión local por base y run; el sistema operativo libera al morir el proceso."""
from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path


@contextmanager
def worker_lock(database, run_id):
    database = Path(database).resolve()
    key = hashlib.sha256((os.path.normcase(str(database))+'\0'+run_id).encode()).hexdigest()
    directory = database.parent/'worker-locks'
    directory.mkdir(exist_ok=True)
    # No borrar: otro proceso podría estar usando el mismo archivo/inodo.
    with (directory/(key+'.lock')).open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
        try:
            yield acquired
        finally:
            if acquired:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
