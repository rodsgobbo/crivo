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


def test_a_broken_stage_ends_the_run_and_not_the_worker(env):
    """Estagio que quebra derrubava o executor inteiro, e em ciclo.

    A excecao subia ate o laco do processo; o run continuava em andamento, e a
    partida seguinte o devolvia a fila e morria de novo.
    """
    from crivo.worker.queue import INTERROMPIDO

    connection, queue, config = env
    run_id = queue.enqueue("ana")

    def explodir(_contexto):
        raise KeyError("buscas")

    seguinte = Recorder("nunca")
    stages = [Recorder("coletar", explodir), seguinte]

    contexto = Runner(connection, config, stages).run_once()

    assert contexto is not None, "o executor precisa continuar de pe"
    assert seguinte.calls == 0, "o run para no estagio que quebrou"
    linha = queue.get(run_id)
    assert linha["estado"] == INTERROMPIDO
    assert "coletar" in (linha["motivo_recusa"] or "")


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


def test_a_failing_stage_records_the_reason_in_the_run(env):
    """Contrato trocado em 18/set, contra defeito visto em uso real.

    Este teste exigia que a excecao subisse. Quem chama `run_once` no processo
    de verdade e um laco `while True`, entao subir queria dizer matar o
    executor -- e, como o run continuava em andamento, a partida seguinte o
    devolvia a fila e morria de novo. A causa agora fica no run.
    """
    from crivo.worker.queue import INTERROMPIDO

    connection, queue, config = env
    run_id = queue.enqueue("ana")

    def explode(context):
        raise RuntimeError("estagio quebrou")

    Runner(connection, config, [Recorder("x", explode)]).run_once()

    linha = queue.get(run_id)
    assert linha["estado"] == INTERROMPIDO
    assert "estagio quebrou" in (linha["motivo_recusa"] or "")


def test_repeated_stage_names_are_refused(env):
    connection, _queue, config = env
    with pytest.raises(ValueError) as err:
        Runner(connection, config, [Recorder("igual"), Recorder("igual")])
    assert "igual" in str(err.value)
