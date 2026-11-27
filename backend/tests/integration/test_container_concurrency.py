import threading

from app.core.container import get_container, reset_container
import sqlalchemy as sa

from app.infrastructure.database.session import get_database


def test_concurrent_first_requests_build_one_container_and_seed_once():
    reset_container()
    results, errors = [], []
    barrier = threading.Barrier(12)

    def go():
        try:
            barrier.wait()
            results.append(get_container())
        except Exception as e:  # pragma: no cover - failure path is the assertion
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors and len({id(c) for c in results}) == 1
    with get_database().begin() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM skills")).scalar_one() == len(results[0].registry.repo.list(limit=999)[0])
