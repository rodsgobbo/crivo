import json
import time

import pytest

from crivo.config import load_config
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.worker.queue import (
    AGUARDANDO_ENRIQUECIMENTO,
    CONCLUIDO,
    EM_ANDAMENTO,
    ENFILEIRADO,
    INTERROMPIDO,
    QueueError,
    RECUSADO,
    RunQueue,
)


@pytest.fixture
def queue(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id, subject in (("ana", "sub-ana"), ("bruno", "sub-bruno")):
        repo.insert(
            "users",
            {
                "user_id": user_id,
                "subject_google": subject,
                "criado_em": "2026-01-01T00:00:00+00:00",
            },
        )
    yield RunQueue(connection)
    connection.close()


def test_an_enqueued_run_starts_pending(queue):
    run_id = queue.enqueue("ana")
    assert queue.get(run_id)["estado"] == ENFILEIRADO
    assert queue.pending_count() == 1


def test_reserving_marks_the_run_as_running(queue):
    run_id = queue.enqueue("ana")
    reserved = queue.reserve_next()
    assert reserved["run_id"] == run_id
    assert reserved["estado"] == EM_ANDAMENTO
    assert reserved["iniciado_em"] is not None


def test_an_empty_queue_reserves_nothing(queue):
    assert queue.reserve_next() is None


def test_a_reserved_run_is_not_handed_out_twice(queue):
    queue.enqueue("ana")
    assert queue.reserve_next() is not None
    assert queue.reserve_next() is None


def test_runs_are_served_oldest_first(queue):
    first = queue.enqueue("ana")
    time.sleep(1.05)
    second = queue.enqueue("bruno")
    assert queue.reserve_next()["run_id"] == first
    assert queue.reserve_next()["run_id"] == second


def test_a_second_run_for_the_same_user_is_refused(queue):
    queue.enqueue("ana")
    with pytest.raises(QueueError) as err:
        queue.enqueue("ana")
    assert "ana" in str(err.value)


def test_the_refused_attempt_is_itself_recorded(queue):
    queue.enqueue("ana")
    with pytest.raises(QueueError):
        queue.enqueue("ana")
    recusados = [
        r
        for r in queue._connection.execute("SELECT * FROM runs WHERE estado = ?", (RECUSADO,))
    ]
    assert len(recusados) == 1
    assert "ainda ativo" in recusados[0]["motivo_recusa"]


def test_another_user_is_not_blocked_by_an_active_run(queue):
    queue.enqueue("ana")
    assert queue.enqueue("bruno") is not None


def test_suspending_and_resuming_moves_the_run_between_states(queue):
    run_id = queue.enqueue("ana")
    queue.reserve_next()
    queue.suspend(run_id)
    assert queue.get(run_id)["estado"] == AGUARDANDO_ENRIQUECIMENTO
    assert queue.reserve_next() is None
    queue.resume(run_id)
    assert queue.reserve_next()["run_id"] == run_id


def test_an_abandoned_run_returns_to_the_queue(queue):
    """Reiniciar o worker no meio de um estagio nao pode prender o run.

    O processo de runs e unico: se ha run em andamento quando ele sobe, o dono
    morreu. Antes desta recuperacao o run so era liberado pelo teto de duracao
    -- seis horas em que todo pedido novo do usuario era recusado por
    concorrencia com um run que ninguem executava.
    """
    run_id = queue.enqueue("ana")
    queue.reserve_next()
    assert queue.get(run_id)["estado"] == EM_ANDAMENTO

    recuperados = queue.reclaim_abandoned()

    assert recuperados == [run_id]
    assert queue.get(run_id)["estado"] == ENFILEIRADO
    # Volta reservavel, e nao interrompido: os estagios concluidos ficam
    # gravados e a retomada continua de onde parou.
    assert queue.reserve_next()["run_id"] == run_id


def test_nothing_is_reclaimed_when_no_run_was_abandoned(queue):
    queue.enqueue("ana")
    assert queue.reclaim_abandoned() == []


def test_finishing_records_the_counters(queue):
    run_id = queue.enqueue("ana")
    queue.reserve_next()
    queue.finish(run_id, n_brutos=40, n_filtrados=12, n_novos=5, buscas=["SRE Manager"])
    row = queue.get(run_id)
    assert row["estado"] == CONCLUIDO
    assert (row["n_brutos"], row["n_filtrados"], row["n_novos"]) == (40, 12, 5)
    assert json.loads(row["buscas"]) == ["SRE Manager"]


def test_an_unknown_counter_is_refused(queue):
    run_id = queue.enqueue("ana")
    with pytest.raises(QueueError) as err:
        queue.finish(run_id, n_inventado=1)
    assert "n_inventado" in str(err.value)


def test_the_effective_configuration_is_stored_with_the_run(queue):
    config = load_config()
    run_id = queue.enqueue("ana", config_efetiva=config)
    stored = json.loads(queue.get(run_id)["config_efetiva"])
    assert stored["report"]["limiar_destaque"] == config.report.limiar_destaque


def test_a_stalled_run_is_interrupted_and_frees_the_slot(queue):
    run_id = queue.enqueue("ana")
    queue.reserve_next()
    assert queue.reap_stalled(max_duration_s=0) == [run_id]
    assert queue.get(run_id)["estado"] == INTERROMPIDO
    assert queue.enqueue("ana") is not None


def test_a_live_run_is_not_interrupted(queue):
    queue.enqueue("ana")
    queue.reserve_next()
    assert queue.reap_stalled(max_duration_s=3600) == []


def test_an_unknown_window_is_refused(queue):
    with pytest.raises(QueueError) as err:
        queue.enqueue("ana", janela="ontem")
    assert "ontem" in str(err.value)


def test_updating_an_unknown_run_is_refused(queue):
    with pytest.raises(QueueError):
        queue.heartbeat("nao-existe")


# ------------------------------------------------------------ cancelamento
def test_cancelling_records_a_request_and_does_not_end_the_run(queue):
    """Quem clica nao esta executando nada, e nao pode encerrar por baixo.

    Encerrar o run da face web deixaria requisicao pela metade no processo que
    esta coletando, e ele voltaria em seguida gravando sobre um run que a web ja
    considera morto.
    """
    run_id = queue.enqueue("ana")
    assert queue.request_cancel(run_id, "ana") is True
    assert queue.cancel_requested(run_id) is True
    assert queue.get(run_id)["estado"] == ENFILEIRADO


def test_a_finished_run_cannot_be_cancelled(queue):
    run_id = queue.enqueue("ana")
    queue.finish(run_id)
    assert queue.request_cancel(run_id, "ana") is False
    assert queue.cancel_requested(run_id) is False


def test_a_run_of_another_user_cannot_be_cancelled(queue):
    """Sem isto, adivinhar um identificador bastaria para parar run alheio."""
    run_id = queue.enqueue("ana")
    assert queue.request_cancel(run_id, "bruno") is False
    assert queue.cancel_requested(run_id) is False


def test_cancelling_twice_is_reported_as_already_requested(queue):
    """A segunda chamada nao pode mover o carimbo do primeiro pedido."""
    run_id = queue.enqueue("ana")
    assert queue.request_cancel(run_id, "ana") is True
    primeiro = queue.get(run_id)["cancelado_em"]
    assert queue.request_cancel(run_id, "ana") is False
    assert queue.get(run_id)["cancelado_em"] == primeiro


def test_a_cancelled_run_ends_interrupted_with_the_reason_written(queue):
    """Nao ha estado novo: o que interessa e que acabou sem concluir."""
    run_id = queue.enqueue("ana")
    queue.request_cancel(run_id, "ana")
    queue.cancel(run_id, n_brutos=12)

    linha = queue.get(run_id)
    assert linha["estado"] == INTERROMPIDO
    assert linha["n_brutos"] == 12
    assert "cancelado" in linha["motivo_recusa"]
