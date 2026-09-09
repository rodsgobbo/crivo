import pytest

from crivo.config import load_config
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.worker.queue import (
    AGUARDANDO_ENRIQUECIMENTO,
    CONCLUIDO,
    RunQueue,
)
from crivo.worker.runner import RunContext, Runner, Suspend


class Recorder:
    def __init__(self, name, action=None):
        self.name = name
        self.calls = 0
        self._action = action

    def run(self, context: RunContext) -> None:
        self.calls += 1
        context.dados.setdefault("ordem", []).append(self.name)
        if self._action:
            self._action(context)


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {
            "user_id": "ana",
            "subject_google": "sub-ana",
            "criado_em": "2026-01-01T00:00:00+00:00",
        },
    )
    yield connection, RunQueue(connection), load_config()
    connection.close()


def test_an_empty_queue_yields_nothing(env):
    connection, _queue, config = env
    assert Runner(connection, config, [Recorder("a")]).run_once() is None


def test_stages_execute_in_the_declared_order(env):
    connection, queue, config = env
    run_id = queue.enqueue("ana")
    stages = [Recorder("planejar"), Recorder("coletar"), Recorder("pontuar")]
    context = Runner(connection, config, stages).run_once()
    assert context.dados["ordem"] == ["planejar", "coletar", "pontuar"]
    assert queue.get(run_id)["estado"] == CONCLUIDO


def test_the_scope_is_bound_to_the_run_owner(env):
    connection, queue, config = env
    queue.enqueue("ana")
    captured = {}
    stages = [Recorder("x", lambda c: captured.update(user=c.scope.user_id))]
    Runner(connection, config, stages).run_once()
    assert captured["user"] == "ana"


def test_counters_collected_by_stages_reach_the_run_record(env):
    connection, queue, config = env
    run_id = queue.enqueue("ana")
    stages = [Recorder("x", lambda c: c.contadores.update(n_brutos=40, n_filtrados=12))]
    Runner(connection, config, stages).run_once()
    row = queue.get(run_id)
    assert (row["n_brutos"], row["n_filtrados"]) == (40, 12)


def test_a_suspending_stage_releases_the_worker(env):
    connection, queue, config = env

    def suspende(context):
        raise Suspend

    run_id = queue.enqueue("ana")
    later = Recorder("depois")
    stages = [Recorder("antes"), Recorder("suspende", suspende), later]
    Runner(connection, config, stages).run_once()
    assert queue.get(run_id)["estado"] == AGUARDANDO_ENRIQUECIMENTO
    assert later.calls == 0


def test_a_resumed_run_does_not_repeat_finished_stages(env):
    connection, queue, config = env
    state = {"suspender": True}

    def talvez_suspende(context):
        if state["suspender"]:
            state["suspender"] = False
            raise Suspend

    run_id = queue.enqueue("ana")
    antes = Recorder("antes")
    ponto = Recorder("ponto", talvez_suspende)
    depois = Recorder("depois")
    runner = Runner(connection, config, [antes, ponto, depois])

    runner.run_once()
    assert (antes.calls, ponto.calls, depois.calls) == (1, 1, 0)

    queue.resume(run_id)
    runner.run_once()

    assert antes.calls == 1
    assert (ponto.calls, depois.calls) == (2, 1)
    assert queue.get(run_id)["estado"] == CONCLUIDO


def test_stage_progress_survives_a_new_runner_instance(env):
    connection, queue, config = env

    def suspende(context):
        raise Suspend

    run_id = queue.enqueue("ana")
    Runner(connection, config, [Recorder("antes"), Recorder("p", suspende)]).run_once()

    queue.resume(run_id)
    antes = Recorder("antes")
    fresh = Runner(connection, config, [antes, Recorder("p")])
    fresh.run_once()
    assert antes.calls == 0


def test_a_failing_stage_propagates(env):
    connection, queue, config = env
    queue.enqueue("ana")

    def explode(context):
        raise RuntimeError("estagio quebrou")

    with pytest.raises(RuntimeError):
        Runner(connection, config, [Recorder("x", explode)]).run_once()


def test_repeated_stage_names_are_refused(env):
    connection, _queue, config = env
    with pytest.raises(ValueError) as err:
        Runner(connection, config, [Recorder("igual"), Recorder("igual")])
    assert "igual" in str(err.value)
