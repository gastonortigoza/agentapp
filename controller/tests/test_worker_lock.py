import subprocess
import sys
from pathlib import Path

from worker_lock import worker_lock


def test_lock_rejects_same_run_but_allows_other_run(tmp_path):
    database = tmp_path/'db.sqlite'
    with worker_lock(database, 'one') as first:
        assert first
        with worker_lock(database, 'one') as second:
            assert not second
        with worker_lock(database, 'two') as other:
            assert other
    with worker_lock(database, 'one') as after:
        assert after


def test_process_death_releases_lock(tmp_path):
    database = tmp_path/'db.sqlite'
    child = "from worker_lock import worker_lock; import sys,time\nwith worker_lock(sys.argv[1], 'run') as acquired:\n print(acquired,flush=True)\n time.sleep(30)\n"
    proc = subprocess.Popen([sys.executable, '-c', child, str(database)],
                            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'True'
        with worker_lock(database, 'run') as acquired:
            assert not acquired
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()
    with worker_lock(database, 'run') as acquired:
        assert acquired
