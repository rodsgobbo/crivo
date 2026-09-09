import json
from datetime import datetime, timedelta, timezone

import pytest

from crivo.config import load_config
from crivo.pipeline.governor import AttemptTimeout, CollectionBlocked, RateGovernor
from crivo.scheduler import AMPLA, INCREMENTAL, Scheduler, SchedulerRefusal
from crivo.store.migrations import open_database
from crivo.store.privacy import EXCLUSAO, EXPORTACAO, PrivacyError, PrivacyService
from crivo.store.repository import Repository
from crivo.worker.queue import CONCLUIDO, RunQueue

# A data real em UTC, nao um literal. As linhas que estes testes semeiam sao
# contadas contra carimbos gravados pelo relogio de verdade, entao um literal so
# passa no dia em que foi escrito -- e este passou a falhar sozinho na virada.
HOJE = datetime.now(timezone.utc).date().isoformat()
AGORA = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.agora = AGORA
        self.dormidas = []

    def now(self):
        return self.agora

    def sleep(self, s):
        self.dormidas.append(s)

    def avancar(self, segundos):
        self.agora += timedelta(seconds=segundos)


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id in ("ana", "bruno", "carla"):
        repo.insert(
            "users",
            {
                "user_id": user_id, "subject_google": f"sub-{user_id}",
                "criado_em": "2026-01-01T00:00:00+00:00",
                "ultima_sessao_em": "2026-08-20T00:00:00+00:00",
            },
        )
    yield connection, load_config(), repo
    connection.close()


def scheduler(env, governor=None):
    connection, config, _repo = env
    return Scheduler(connection, config, governor, hoje=lambda: HOJE)


# ------------------------------------------------------------------ rodizio
def test_a_cycle_enqueues_every_active_user(env):
    resultado = scheduler(env).run_cycle()
    assert len(resultado.enfileirados) == 3


def test_the_recurring_window_is_the_incremental_one(env):
    connection, _c, _r = env
    scheduler(env).run_cycle()
    janelas = {
        linha["janela"] for linha in connection.execute("SELECT janela FROM runs")
    }
    assert janelas == {INCREMENTAL}


def test_who_never_ran_comes_first(env):
    connection, _c, repo = env
    fila = RunQueue(connection)
    run_id = fila.enqueue("ana")
    fila.reserve_next()
    fila.finish(run_id)
    ordem = scheduler(env).rotation()
    assert ordem.index("bruno") < ordem.index("ana")
    assert ordem.index("carla") < ordem.index("ana")


def test_who_waited_longest_comes_before_who_ran_recently(env):
    connection, _c, repo = env
    for user_id, quando in (("ana", "2026-08-01"), ("bruno", "2026-08-19")):
        repo.insert(
            "runs",
            {
                "run_id": f"run-{user_id}", "user_id": user_id, "estado": CONCLUIDO,
                "janela": INCREMENTAL, "solicitado_em": f"{quando}T09:00:00+00:00",
            },
        )
    ordem = scheduler(env).rotation()
    assert ordem.index("ana") < ordem.index("bruno")


def test_nobody_is_served_twice_before_everyone_once(env):
    connection, _c, _r = env
    esc = scheduler(env)
    esc.run_cycle()
    atendidos = {
        linha["user_id"] for linha in connection.execute("SELECT user_id FROM runs")
    }
    assert atendidos == {"ana", "bruno", "carla"}


# ------------------------------------------------------------ concorrencia
def test_a_user_with_an_active_run_is_refused_and_recorded(env):
    connection, _c, _r = env
    RunQueue(connection).enqueue("ana")
    resultado = scheduler(env).run_cycle()
    assert "ana" in resultado.recusados
    assert "ativo" in resultado.recusados["ana"]
    assert len(resultado.enfileirados) == 2


def test_a_stalled_run_is_released_by_the_cycle(env):
    connection, config, _r = env
    fila = RunQueue(connection)
    run_id = fila.enqueue("ana")
    fila.reserve_next()
    connection.execute(
        "UPDATE runs SET heartbeat_em = '2020-01-01T00:00:00+00:00' WHERE run_id = ?",
        (run_id,),
    )
    resultado = scheduler(env).run_cycle()
    assert run_id in resultado.interrompidos
    assert len(resultado.enfileirados) == 3


# ------------------------------------------------------------ recuperacao
def bloquear(connection, config, clock):
    governor = RateGovernor(
        connection, config, sleep=clock.sleep, jitter=lambda a, b: 0, now=clock.now
    )

    def falha():
        raise AttemptTimeout("sem resposta")

    with pytest.raises(CollectionBlocked):
        governor.run(falha)
    return governor


def test_a_block_postpones_the_cycle_for_everyone(env):
    connection, config, _r = env
    clock = Clock()
    governor = bloquear(connection, config, clock)
    resultado = scheduler(env, governor).run_cycle()
    assert resultado.adiado_por_bloqueio is True
    assert resultado.enfileirados == []
    assert "bloqueio de coleta" in resultado.motivo


def test_the_cycle_resumes_after_the_recovery_interval(env):
    connection, config, _r = env
    clock = Clock()
    governor = bloquear(connection, config, clock)
    clock.avancar(config.collection.intervalo_recuperacao_s + 1)
    resultado = scheduler(env, governor).run_cycle()
    assert resultado.adiado_por_bloqueio is False
    assert len(resultado.enfileirados) == 3


def test_without_a_governor_the_gate_is_open(env):
    assert scheduler(env).recovery_gate() is None


# ------------------------------------------------------------ run imediato
def test_an_immediate_run_uses_the_wide_window(env):
    connection, _c, _r = env
    run_id = scheduler(env).request_immediate("ana")
    linha = connection.execute(
        "SELECT janela FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert linha["janela"] == AMPLA


def test_the_daily_limit_of_immediate_runs_is_enforced(env):
    connection, config, _r = env
    esc = scheduler(env)
    fila = RunQueue(connection)
    for _ in range(config.run.runs_imediatos_por_dia):
        run_id = esc.request_immediate("ana")
        fila.reserve_next()
        fila.finish(run_id)
    with pytest.raises(SchedulerRefusal) as err:
        esc.request_immediate("ana")
    assert "por dia" in str(err.value)


def test_a_run_interrupted_before_collecting_does_not_consume_the_quota(env):
    """Reiniciar o servidor no meio de um run nao pode custar uma busca.

    O run morre sem coletar nada, e a falha e do sistema e nao de quem clicou.
    Cobrar por ela fazia o usuario perder um terco da cota diaria por um
    reinicio -- foi exatamente o que aconteceu num uso real.
    """
    connection, config, _r = env
    esc = scheduler(env)
    run_id = esc.request_immediate("ana")
    RunQueue(connection).reserve_next()
    RunQueue(connection).reap_stalled(0)
    connection.execute(
        "UPDATE runs SET n_brutos = 0 WHERE run_id = ?", (run_id,)
    )

    assert esc.immediate_remaining("ana") == config.run.runs_imediatos_por_dia


def test_a_run_interrupted_after_collecting_still_consumes_the_quota(env):
    """Capacidade de coleta consumida e cobrada, mesmo sem relatorio no fim."""
    connection, config, _r = env
    esc = scheduler(env)
    run_id = esc.request_immediate("ana")
    RunQueue(connection).reserve_next()
    RunQueue(connection).reap_stalled(0)
    connection.execute(
        "UPDATE runs SET n_brutos = 300 WHERE run_id = ?", (run_id,)
    )

    assert esc.immediate_remaining("ana") == config.run.runs_imediatos_por_dia - 1


def test_an_immediate_run_is_refused_during_a_block(env):
    connection, config, _r = env
    clock = Clock()
    governor = bloquear(connection, config, clock)
    with pytest.raises(SchedulerRefusal) as err:
        scheduler(env, governor).request_immediate("ana")
    assert "bloqueio de coleta" in str(err.value)


def test_an_immediate_run_is_refused_while_another_is_active(env):
    connection, _c, _r = env
    esc = scheduler(env)
    esc.request_immediate("ana")
    with pytest.raises(SchedulerRefusal):
        esc.request_immediate("ana")


def test_a_refused_attempt_does_not_consume_the_daily_quota(env):
    """Insistir num botao que o sistema recusou nao pode custar o dia.

    A recusa por run ativo nao coleta nada. Se ela contasse, tres cliques
    seguidos -- o comportamento natural de quem nao entendeu a recusa --
    esgotariam a cota sem que uma unica busca tivesse acontecido.
    """
    connection, config, _r = env
    esc = scheduler(env)
    fila = RunQueue(connection)

    run_id = esc.request_immediate("ana")
    for _ in range(config.run.runs_imediatos_por_dia + 2):
        with pytest.raises(SchedulerRefusal):
            esc.request_immediate("ana")

    assert esc.immediate_remaining("ana") == config.run.runs_imediatos_por_dia - 1

    fila.reserve_next()
    fila.finish(run_id)
    assert esc.request_immediate("ana")


def test_the_window_in_hours_follows_the_kind(env):
    connection, config, _r = env
    esc = scheduler(env)
    assert esc.window_hours(AMPLA) == config.collection.janela_ampla_horas
    assert esc.window_hours(INCREMENTAL) == config.collection.janela_incremental_horas


# ------------------------------------------------------------- privacidade
def povoar(connection, repo, user_id="ana"):
    scope = repo.for_user(user_id)
    scope.insert(
        "resumes",
        {"resume_id": f"r-{user_id}", "texto": "curriculo", "origem": "pdf",
         "hash_conteudo": "h", "importado_em": HOJE},
    )
    scope.insert(
        "jobs",
        {"job_id": "li-1", "titulo": "SRE", "url": "https://x", "estado": "novo",
         "primeira_vez_em": HOJE, "ultima_vez_em": HOJE},
    )
    # O registro de descricao e compartilhado: uma linha por vaga em todo o
    # sistema, e nao uma por usuario. Povoar dois usuarios com a mesma vaga
    # reusa a linha existente, que e exatamente o comportamento pretendido.
    if not repo.select("job_descriptions", where="job_id = ?", params=("li-1",)):
        repo.insert(
            "job_descriptions",
            {"job_id": "li-1", "texto": "descricao comum", "coletada_em": HOJE},
        )


def privacy(env):
    connection, config, _repo = env
    return PrivacyService(connection, config, agora=lambda: AGORA)


def test_deleting_an_account_removes_every_user_row(env):
    connection, _c, repo = env
    povoar(connection, repo)
    servico = privacy(env)
    servico.delete_account("ana")
    assert servico.remaining_rows("ana") == {}


def test_deleting_an_account_preserves_the_shared_description(env):
    connection, _c, repo = env
    povoar(connection, repo)
    privacy(env).delete_account("ana")
    assert connection.execute(
        "SELECT count(*) FROM job_descriptions"
    ).fetchone()[0] == 1


def test_deleting_an_account_does_not_touch_another_user(env):
    connection, _c, repo = env
    povoar(connection, repo, "ana")
    povoar(connection, repo, "bruno")
    privacy(env).delete_account("ana")
    assert connection.execute(
        "SELECT count(*) FROM resumes WHERE user_id = 'bruno'"
    ).fetchone()[0] == 1


def test_the_proof_of_deletion_survives_the_deletion(env):
    connection, _c, repo = env
    povoar(connection, repo)
    servico = privacy(env)
    servico.delete_account("ana")
    operacoes = servico.operations("ana")
    assert [o.tipo for o in operacoes] == [EXCLUSAO]
    assert operacoes[0].instante


def test_deleting_an_unknown_account_is_refused(env):
    with pytest.raises(PrivacyError):
        privacy(env).delete_account("inventado")


# ------------------------------------------------------------- exportacao
def test_the_export_carries_the_subject_data(env):
    connection, _c, repo = env
    povoar(connection, repo)
    pacote = privacy(env).export_account("ana")
    assert pacote["dados"]["resumes"][0]["texto"] == "curriculo"
    assert pacote["dados"]["jobs"][0]["job_id"] == "li-1"


def test_the_export_omits_credential_material(env):
    connection, _c, repo = env
    povoar(connection, repo)
    repo.for_user("ana").insert(
        "linkedin_connections",
        {"campos_identidade": "{}", "escopos_concedidos": "[]",
         "campos_indisponiveis": "[]", "estado": "ativa",
         "token_cifrado": b"segredo", "conectada_em": HOJE},
    )
    texto = privacy(env).export_json("ana")
    assert "token_cifrado" not in texto
    assert "segredo" not in texto


def test_the_export_never_includes_another_user(env):
    connection, _c, repo = env
    povoar(connection, repo, "ana")
    povoar(connection, repo, "bruno")
    pacote = privacy(env).export_account("ana")
    donos = {linha["user_id"] for linha in pacote["dados"]["resumes"]}
    assert donos == {"ana"}


def test_the_export_is_recorded(env):
    connection, _c, repo = env
    povoar(connection, repo)
    servico = privacy(env)
    servico.export_account("ana")
    assert [o.tipo for o in servico.operations("ana")] == [EXPORTACAO]


# --------------------------------------------------------------- retencao
def test_an_inactive_user_has_resume_and_extraction_purged(env):
    connection, config, repo = env
    povoar(connection, repo)
    connection.execute(
        "UPDATE users SET ultima_sessao_em = '2020-01-01T00:00:00+00:00' "
        "WHERE user_id = 'ana'"
    )
    expurgados = privacy(env).purge_inactive()
    assert "ana" in expurgados
    assert connection.execute(
        "SELECT count(*) FROM resumes WHERE user_id = 'ana'"
    ).fetchone()[0] == 0


def test_an_active_user_is_not_purged(env):
    connection, _c, repo = env
    povoar(connection, repo)
    assert privacy(env).purge_inactive() == []
    assert connection.execute("SELECT count(*) FROM resumes").fetchone()[0] == 1


def test_purging_keeps_the_account_itself(env):
    connection, _c, repo = env
    povoar(connection, repo)
    connection.execute(
        "UPDATE users SET ultima_sessao_em = '2020-01-01T00:00:00+00:00'"
    )
    privacy(env).purge_inactive()
    assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 3


# ------------------------------------------------------------------ aviso
def test_the_disclosure_names_what_is_kept_and_for_how_long(env):
    connection, config, _r = env
    aviso = privacy(env).disclosure()
    assert "curriculo" in aviso
    assert str(config.retention.periodo_sem_sessao_dias) in aviso
    assert "exportar ou excluir" in aviso


# ------------------------------------------------------------- alcance
def test_the_default_reach_is_the_configured_wide_window(env):
    connection, config, _r = env
    esc = scheduler(env)
    run_id = esc.request_immediate("ana")
    linha = connection.execute(
        "SELECT janela, janela_horas FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert linha["janela"] == AMPLA
    assert linha["janela_horas"] == config.collection.janela_ampla_horas


@pytest.mark.parametrize("alcance,horas", [("30d", 720), ("7d", 168), ("1d", 24)])
def test_the_chosen_reach_is_stored_with_the_run(env, alcance, horas):
    """A escolha precisa sobreviver ao proprio run.

    Sem grava-la, reler um run antigo aplicaria o alcance configurado hoje a
    uma busca feita com outro -- e o relatorio passaria a afirmar uma janela
    que nunca foi usada.
    """
    connection, _c, _r = env
    run_id = scheduler(env).request_immediate("ana", alcance)
    linha = connection.execute(
        "SELECT janela, janela_horas FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    # A janela continua `ampla`: ela diz que o run foi pedido por alguem, e e
    # por ela que a cota diaria conta.
    assert linha["janela"] == AMPLA
    assert linha["janela_horas"] == horas


def test_an_unknown_reach_is_refused_by_name(env):
    with pytest.raises(SchedulerRefusal) as err:
        scheduler(env).request_immediate("ana", "seis meses")
    assert "seis meses" in str(err.value)


def test_the_stored_reach_wins_over_the_configuration(env):
    esc = scheduler(env)
    assert esc.window_hours(AMPLA, 168) == 168
    assert esc.window_hours(AMPLA, None) == esc._config.collection.janela_ampla_horas
