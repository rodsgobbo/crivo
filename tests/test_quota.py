import pytest

from crivo.config import load_config
from crivo.pipeline.quota import QuotaAllocator
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

HOJE = "2026-08-21"
ONTEM = "2026-08-20"


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id in ("ana", "bruno", "carla"):
        repo.insert(
            "users",
            {
                "user_id": user_id,
                "subject_google": f"sub-{user_id}",
                "criado_em": "2026-01-01",
            },
        )
    yield connection, load_config()
    connection.close()


def allocator(env, dia=HOJE):
    connection, config = env
    return QuotaAllocator(connection, config, hoje=lambda: dia)


def pedir_run(connection, user_id, dia=HOJE, sufixo=""):
    Repository(connection).insert(
        "runs",
        {
            "run_id": f"run-{user_id}-{dia}{sufixo}",
            "user_id": user_id,
            "estado": "enfileirado",
            "janela": "incremental",
            "solicitado_em": f"{dia}T09:00:00+00:00",
        },
    )


# ------------------------------------------------------------------ teto
def test_a_single_active_user_gets_the_whole_budget(env):
    connection, config = env
    pedir_run(connection, "ana")
    cota = allocator(env)
    assert cota.ceiling() == config.collection.orcamento_diario_coleta


def test_the_budget_is_split_among_active_users(env):
    connection, config = env
    pedir_run(connection, "ana")
    pedir_run(connection, "bruno")
    esperado = config.collection.orcamento_diario_coleta // 2
    assert allocator(env).ceiling() == esperado


def test_with_no_runs_the_divisor_is_one_not_zero(env):
    connection, config = env
    assert allocator(env).ceiling() == config.collection.orcamento_diario_coleta


def test_runs_from_other_days_do_not_count(env):
    connection, config = env
    pedir_run(connection, "ana")
    pedir_run(connection, "bruno", dia=ONTEM)
    assert allocator(env).ceiling() == config.collection.orcamento_diario_coleta


def test_the_same_user_with_two_runs_counts_once(env):
    connection, config = env
    pedir_run(connection, "ana")
    pedir_run(connection, "ana", sufixo="-b")
    assert allocator(env).ceiling() == config.collection.orcamento_diario_coleta


# ------------------------------------------------------------------ debito
def test_spending_lowers_what_is_available(env):
    connection, _config = env
    pedir_run(connection, "ana")
    cota = allocator(env)
    antes = cota.status("ana").disponivel
    cota.spend("ana")
    assert cota.status("ana").disponivel == antes - 1


def test_only_the_spending_user_is_debited(env):
    connection, _config = env
    pedir_run(connection, "ana")
    pedir_run(connection, "bruno")
    cota = allocator(env)
    cota.spend("ana")
    assert cota.status("ana").consumida == 1
    assert cota.status("bruno").consumida == 0


def test_a_user_can_spend_until_the_ceiling(env):
    connection, config = env
    pedir_run(connection, "ana")
    cota = allocator(env)
    for _ in range(config.collection.orcamento_diario_coleta):
        assert cota.can_spend("ana") is True
        cota.spend("ana")
    assert cota.can_spend("ana") is False
    assert cota.status("ana").esgotada is True


# ---------------------------------------------------- teto movel, nao saldo
def test_a_user_joining_mid_day_lowers_the_ceiling_for_everyone(env):
    connection, config = env
    pedir_run(connection, "ana")
    cota = allocator(env)
    teto_sozinha = cota.ceiling()

    pedir_run(connection, "bruno")
    assert cota.ceiling() < teto_sozinha


def test_a_lowered_ceiling_never_revokes_what_was_already_spent(env):
    connection, config = env
    pedir_run(connection, "ana")
    cota = allocator(env)
    for _ in range(config.collection.orcamento_diario_coleta):
        cota.spend("ana")
    consumido = cota.status("ana").consumida

    pedir_run(connection, "bruno")
    pedir_run(connection, "carla")

    # O teto encolheu abaixo do que ela ja gastou. O consumido permanece.
    situacao = cota.status("ana")
    assert situacao.consumida == consumido
    assert situacao.teto < consumido
    assert situacao.disponivel == 0
    assert situacao.esgotada is True


# ------------------------------------------------------------------ virada
def test_a_new_day_starts_from_zero(env):
    connection, _config = env
    pedir_run(connection, "ana", dia=ONTEM)
    ontem = allocator(env, dia=ONTEM)
    ontem.spend("ana")
    assert ontem.status("ana").consumida == 1

    pedir_run(connection, "ana")
    assert allocator(env, dia=HOJE).status("ana").consumida == 0


def test_unused_balance_is_not_carried_over(env):
    connection, config = env
    pedir_run(connection, "ana", dia=ONTEM)
    pedir_run(connection, "ana")
    hoje = allocator(env, dia=HOJE)
    assert hoje.status("ana").teto == config.collection.orcamento_diario_coleta


# ------------------------------------------------------------------ relato
def test_consumption_per_user_is_reported(env):
    connection, _config = env
    pedir_run(connection, "ana")
    pedir_run(connection, "bruno")
    cota = allocator(env)
    cota.spend("ana")
    cota.spend("ana")
    cota.spend("bruno")
    assert cota.consumption() == {"ana": 2, "bruno": 1}
    assert cota.total_consumption() == 3


def test_with_nothing_spent_the_report_is_empty(env):
    assert allocator(env).consumption() == {}
    assert allocator(env).total_consumption() == 0
