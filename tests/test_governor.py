from datetime import datetime, timedelta, timezone

import pytest

from crivo.config import load_config
from crivo.pipeline.governor import (
    AttemptTimeout,
    CollectionBlocked,
    RateGovernor,
)
from crivo.store.migrations import open_database


class Clock:
    """Relogio falso: o tempo so anda quando o teste manda."""

    def __init__(self):
        self.agora = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
        self.dormidas = []

    def now(self):
        return self.agora

    def sleep(self, segundos):
        self.dormidas.append(segundos)

    def avancar(self, segundos):
        self.agora += timedelta(seconds=segundos)


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    clock = Clock()
    governor = RateGovernor(
        connection,
        load_config(),
        sleep=clock.sleep,
        jitter=lambda a, b: (a + b) / 2,
        now=clock.now,
    )
    yield governor, clock, connection
    connection.close()


# ------------------------------------------------------------------ espera
def test_the_first_call_does_not_wait(env):
    governor, clock, _c = env
    governor.run(lambda: "ok")
    assert clock.dormidas == []


def test_later_calls_wait_between_them(env):
    governor, clock, _c = env
    governor.run(lambda: "ok")
    governor.run(lambda: "ok")
    assert len(clock.dormidas) == 1
    config = load_config().collection
    assert config.intervalo_enriquecimento_min_s <= clock.dormidas[0] <= config.intervalo_enriquecimento_max_s


def test_the_wait_comes_from_a_range_not_a_fixed_cadence(env):
    governor, _clock, connection = env
    valores = iter([9.0, 17.0])
    variavel = RateGovernor(
        connection, load_config(), sleep=lambda s: None,
        jitter=lambda a, b: next(valores),
    )
    variavel.run(lambda: "ok")
    variavel.run(lambda: "ok")
    variavel.run(lambda: "ok")
    assert variavel.stats.esperas == [9.0, 17.0]


def test_successful_calls_are_counted(env):
    governor, _clock, _c = env
    governor.run(lambda: "a")
    governor.run(lambda: "b")
    assert governor.stats.chamadas == 2


# ------------------------------------------------------------------ recuo
def test_a_timeout_is_retried_with_a_growing_backoff(env):
    governor, clock, _c = env
    tentativas = {"n": 0}

    def instavel():
        tentativas["n"] += 1
        if tentativas["n"] < 3:
            raise AttemptTimeout("sem resposta")
        return "enfim"

    assert governor.run(instavel) == "enfim"
    recuos = clock.dormidas
    assert len(recuos) == 2
    assert recuos[1] > recuos[0]


def test_a_success_resets_the_consecutive_failure_count(env):
    governor, _clock, _c = env
    estado = {"falhar": True}

    def as_vezes():
        if estado["falhar"]:
            estado["falhar"] = False
            raise AttemptTimeout("sem resposta")
        return "ok"

    governor.run(as_vezes)
    assert governor.stats.fracassos_consecutivos == 0


# ------------------------------------------------------------------ bloqueio
def sempre_falha():
    raise AttemptTimeout("sem resposta")


def test_three_consecutive_failures_record_a_block(env):
    governor, _clock, connection = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    assert connection.execute("SELECT count(*) FROM collection_blocks").fetchone()[0] == 1
    assert governor.stats.bloqueios == 1


def test_after_a_block_every_new_call_is_refused(env):
    governor, _clock, _c = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    with pytest.raises(CollectionBlocked) as err:
        governor.run(lambda: "deveria nem tentar")
    assert "bloqueio de coleta" in str(err.value)


def test_a_refused_call_never_reaches_the_operation(env):
    governor, _clock, _c = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    executou = {"sim": False}

    def marcar():
        executou["sim"] = True

    with pytest.raises(CollectionBlocked):
        governor.run(marcar)
    assert executou["sim"] is False


def test_the_block_becomes_visible_state_not_a_log_line(env):
    governor, _clock, connection = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    bloco = governor.active_block()
    assert bloco["tentativas"] == 3
    assert bloco["ocorrido_em"]


def test_the_block_expires_after_the_recovery_interval(env):
    governor, clock, _c = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    assert governor.active_block() is not None

    clock.avancar(load_config().collection.intervalo_recuperacao_s + 1)
    assert governor.active_block() is None
    assert governor.run(lambda: "liberado") == "liberado"


def test_an_expired_block_is_marked_released(env):
    governor, clock, connection = env
    with pytest.raises(CollectionBlocked):
        governor.run(sempre_falha)
    clock.avancar(load_config().collection.intervalo_recuperacao_s + 1)
    governor.active_block()
    liberado = connection.execute(
        "SELECT liberado_em FROM collection_blocks"
    ).fetchone()[0]
    assert liberado is not None


def test_without_any_block_there_is_nothing_active(env):
    governor, _clock, _c = env
    assert governor.active_block() is None
