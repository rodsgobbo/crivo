import pytest

from crivo.config import load_config
from crivo.pipeline.enricher import Enricher
from crivo.pipeline.governor import AttemptTimeout, RateGovernor
from crivo.pipeline.quota import QuotaAllocator
from crivo.pipeline.sources.guest import PRESENCIAL, Card, CollectionError
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

DIA = "2026-08-21"


def card(job_id="li-1"):
    return Card(
        job_id=job_id, titulo="SRE Manager", empresa="Fintech",
        url=f"https://exemplo.br/{job_id}", local="Sao Paulo, SP",
        modelo_trabalho=PRESENCIAL, publicada_em="2026-08-01",
    )


class FakeSource:
    """Dublê com a forma real do retorno da rota publica.

    Sinais e contagem de candidatos vem na mesma resposta que a descricao. O
    desenho original supunha que so uma sessao autenticada os alcancava, e um
    dublê que omitisse esses campos manteria essa suposicao viva nos testes.
    """

    def __init__(
        self, erro=None, texto="Sobre a vaga: Kubernetes e Terraform",
        sinais=("early_applicant",), candidatos=42,
    ):
        self.erro = erro
        self.texto = texto
        self.sinais = list(sinais)
        self.candidatos = candidatos
        self.chamadas = []

    def search(self, termo, local, janela_horas, quantidade):
        raise AssertionError("o enriquecedor nao deve buscar")

    def describe(self, job_id, url):
        self.chamadas.append(job_id)
        if self.erro:
            raise self.erro
        return {
            "texto": self.texto, "emails": ["rh@fintech.br"],
            "sinais": self.sinais, "candidatos": self.candidatos,
            "publicada_ha": "Há 5 horas", "criterios": {},
        }


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id, subject in (("ana", "sub-ana"), ("bruno", "sub-bruno")):
        repo.insert(
            "users",
            {"user_id": user_id, "subject_google": subject, "criado_em": "2026-01-01"},
        )
        repo.insert(
            "jobs",
            {
                "job_id": "li-1", "user_id": user_id, "titulo": "SRE Manager",
                "url": "https://exemplo.br/li-1", "estado": "novo",
                "primeira_vez_em": DIA, "ultima_vez_em": DIA,
            },
        )
    config = load_config()
    governor = RateGovernor(connection, config, sleep=lambda s: None)
    yield connection, config, governor
    connection.close()


def enricher(env, source=None, quota=None):
    connection, _config, governor = env
    return Enricher(connection, source or FakeSource(), governor, quota)


# ------------------------------------------------------------------ descricao
def test_a_surviving_job_gets_its_description_stored(env):
    connection, _c, _g = env
    fonte = FakeSource()
    resultado = enricher(env, fonte).enrich("ana", [card()])
    assert resultado.enriquecidas == ["li-1"]
    linha = connection.execute("SELECT * FROM job_descriptions").fetchone()
    assert "Kubernetes" in linha["texto"]
    assert linha["coletada_em"]


def test_contact_emails_from_the_description_are_recorded(env):
    connection, _c, _g = env
    enricher(env).enrich("ana", [card()])
    import json

    emails = json.loads(
        connection.execute("SELECT emails_contato FROM job_descriptions").fetchone()[0]
    )
    assert emails == ["rh@fintech.br"]


def test_a_description_that_cannot_be_obtained_marks_the_job(env):
    fonte = FakeSource(erro=CollectionError("indisponivel"))
    resultado = enricher(env, fonte).enrich("ana", [card()])
    assert resultado.sem_descricao == ["li-1"]
    assert resultado.enriquecidas == []


def test_an_empty_description_is_treated_as_absent(env):
    resultado = enricher(env, FakeSource(texto="   ")).enrich("ana", [card()])
    assert resultado.sem_descricao == ["li-1"]


def test_the_run_continues_after_a_missing_description(env):
    fonte = FakeSource(erro=CollectionError("x"))
    resultado = enricher(env, fonte).enrich("ana", [card("li-1"), card("li-2")])
    assert fonte.chamadas == ["li-1", "li-2"]
    assert len(resultado.sem_descricao) == 2


# ------------------------------------------------------ registro partilhado
def test_a_stored_description_is_reused_without_a_request(env):
    fonte = FakeSource()
    e = enricher(env, fonte)
    e.enrich("ana", [card()])
    resultado = e.enrich("bruno", [card()])
    assert resultado.reusadas == ["li-1"]
    assert fonte.chamadas == ["li-1"]


def test_the_shared_registry_holds_one_row_per_job(env):
    connection, _c, _g = env
    e = enricher(env)
    e.enrich("ana", [card()])
    e.enrich("bruno", [card()])
    assert connection.execute("SELECT count(*) FROM job_descriptions").fetchone()[0] == 1


def test_reuse_does_not_spend_quota(env):
    connection, config, _g = env
    cota = QuotaAllocator(connection, config, hoje=lambda: DIA)
    e = enricher(env, quota=cota)
    e.enrich("ana", [card()])
    consumo_ana = cota.status("ana", DIA).consumida
    e.enrich("bruno", [card()])
    assert cota.status("bruno", DIA).consumida == 0
    assert consumo_ana == 1


# ------------------------------------------------------------------ bloqueio
def test_a_block_stops_the_enrichment_and_marks_the_rest(env):
    fonte = FakeSource(erro=AttemptTimeout("sem resposta"))
    resultado = enricher(env, fonte).enrich("ana", [card("li-1"), card("li-2")])
    assert resultado.bloqueado is True
    assert set(resultado.sem_descricao) == {"li-1", "li-2"}


def test_after_a_block_no_further_request_is_emitted(env):
    fonte = FakeSource(erro=AttemptTimeout("sem resposta"))
    enricher(env, fonte).enrich("ana", [card("li-1"), card("li-2"), card("li-3")])
    # Tres tentativas na primeira vaga, nenhuma nas seguintes.
    assert fonte.chamadas == ["li-1", "li-1", "li-1"]


# ------------------------------------------------- sinais da rota publica
def test_signals_arrive_with_the_description(env):
    """Nao ha segunda chamada nem sessao: sinal e descricao vem juntos."""
    connection, _c, _g = env
    fonte = FakeSource(sinais=["top_applicant", "early_applicant"])
    enricher(env, fonte).enrich("ana", [card()])
    import json

    flags = json.loads(
        connection.execute("SELECT flags FROM jobs WHERE user_id = 'ana'").fetchone()[0]
    )
    assert flags == ["early_applicant", "top_applicant"]
    assert fonte.chamadas == ["li-1"]


def test_the_applicant_count_arrives_with_the_description(env):
    connection, _c, _g = env
    enricher(env, FakeSource(candidatos=87)).enrich("ana", [card()])
    assert connection.execute(
        "SELECT candidatos FROM job_descriptions"
    ).fetchone()[0] == 87


def test_a_job_without_signals_is_still_enriched(env):
    resultado = enricher(env, FakeSource(sinais=())).enrich("ana", [card()])
    assert resultado.enriquecidas == ["li-1"]


def test_a_job_without_signals_keeps_its_previous_flags(env):
    connection, _c, _g = env
    enricher(env, FakeSource(sinais=())).enrich("ana", [card()])
    flags = connection.execute(
        "SELECT flags FROM jobs WHERE user_id = 'ana'"
    ).fetchone()[0]
    assert flags in (None, "[]")


def test_credential_of_a_user_never_reaches_the_collection(env):
    """A coleta so conhece a conta operacional; credencial de usuario nao a alcanca."""
    connection, config, governor = env
    Repository(connection).for_user("ana").insert(
        "provider_credentials",
        {
            "credential_id": "c1", "provedor": "groq",
            "chave_cifrada": b"cifrado", "chave_de_dado_cifrada": b"cifrado",
            "sufixo": "abcd", "ordem": 0, "criado_em": DIA,
        },
    )
    vistos = {}

    class FonteQueRegistra(FakeSource):
        def describe(self, job_id, url):
            vistos["argumentos"] = (job_id, url)
            return super().describe(job_id, url)

    fonte = FonteQueRegistra()
    e = Enricher(connection, fonte, governor, quota=None)
    e.enrich("ana", [card()])

    # O enriquecedor recebe apenas identificador e endereco da vaga.
    assert vistos["argumentos"] == ("li-1", "https://exemplo.br/li-1")
    import inspect

    assinatura = inspect.signature(fonte.describe)
    assert list(assinatura.parameters) == ["job_id", "url"]


def test_a_user_without_quota_is_refused_and_recorded(env):
    connection, config, governor = env
    cota = QuotaAllocator(connection, config, hoje=lambda: DIA)
    Repository(connection).insert(
        "runs",
        {
            "run_id": "run-cota", "user_id": "ana", "estado": "em_andamento",
            "janela": "incremental", "solicitado_em": f"{DIA}T09:00:00+00:00",
        },
    )
    for _ in range(config.collection.orcamento_diario_coleta):
        cota.spend("ana")
    assert cota.can_spend("ana") is False

    fonte = FakeSource()
    resultado = Enricher(connection, fonte, governor, quota=cota).enrich("ana", [card()])
    assert resultado.sem_cota == ["li-1"]
    assert resultado.enriquecidas == []
    assert fonte.chamadas == []


# ------------------------------------------------------- trava de instancia
def _processo(env, source=None):
    from crivo.worker.enricher_process import EnricherProcess

    connection, config, _governor = env
    return EnricherProcess(connection, config, source or FakeSource())


def test_a_second_enricher_is_refused_while_the_first_lives(env):
    """A contencao de taxa depende de haver apenas um."""
    from crivo.worker.enricher_process import AlreadyRunning

    _processo(env).acquire()
    with pytest.raises(AlreadyRunning):
        _processo(env).acquire()


def test_an_abandoned_lock_is_taken_over(env):
    """Um processo morto nao pode impedir a partida seguinte para sempre.

    A trava guardava so a propria existencia, e um enriquecedor derrubado a
    forca -- ou junto com o terminal -- deixava a marca para tras. A partida
    seguinte se recusava a subir citando um concorrente que nao existia, e a
    unica saida era apagar a linha na mao, sabendo onde ela mora.
    """
    from crivo.store.repository import Repository
    from crivo.worker.enricher_process import CHAVE_DE_INSTANCIA

    connection, _config, _governor = env
    _processo(env).acquire()
    Repository(connection).execute(
        "UPDATE schema_meta SET aplicada_em = ? WHERE chave = ?",
        ("2020-01-01T00:00:00+00:00", CHAVE_DE_INSTANCIA),
    )

    _processo(env).acquire()  # nao levanta: o detentor anterior esta calado

    linhas = Repository(connection).select(
        "schema_meta", where="chave = ?", params=(CHAVE_DE_INSTANCIA,)
    )
    assert len(linhas) == 1, "a trava continua unica depois da tomada"


def test_a_heartbeat_keeps_the_lock_alive(env):
    from crivo.store.repository import Repository
    from crivo.worker.enricher_process import CHAVE_DE_INSTANCIA, AlreadyRunning

    connection, _config, _governor = env
    processo = _processo(env)
    processo.acquire()
    Repository(connection).execute(
        "UPDATE schema_meta SET aplicada_em = ? WHERE chave = ?",
        ("2020-01-01T00:00:00+00:00", CHAVE_DE_INSTANCIA),
    )
    processo.heartbeat()

    with pytest.raises(AlreadyRunning):
        _processo(env).acquire()


def test_an_unreadable_timestamp_does_not_block_forever(env):
    """Trava que ninguem consegue interpretar nao pode travar a partida."""
    from crivo.store.repository import Repository
    from crivo.worker.enricher_process import CHAVE_DE_INSTANCIA

    connection, _config, _governor = env
    _processo(env).acquire()
    Repository(connection).execute(
        "UPDATE schema_meta SET aplicada_em = ? WHERE chave = ?",
        ("ontem de manha", CHAVE_DE_INSTANCIA),
    )
    _processo(env).acquire()


def test_the_lock_is_released_on_a_clean_exit(env):
    from crivo.store.repository import Repository
    from crivo.worker.enricher_process import CHAVE_DE_INSTANCIA

    connection, _config, _governor = env
    processo = _processo(env)
    processo.serve_forever(intervalo_ocioso_s=0, ciclos=1)
    assert not Repository(connection).select(
        "schema_meta", where="chave = ?", params=(CHAVE_DE_INSTANCIA,)
    )


# ------------------------------------------- recencia que era jogada fora
def test_the_posting_age_is_stored_instead_of_discarded(env):
    """O parser ja a extraia e o enriquecedor a perdia.

    A requisicao ja foi paga; o que faltava era guardar. Sem este campo nao ha
    como conferir se a janela pedida foi respeitada -- numa janela de 24h o card
    da busca chega sem data nenhuma.
    """
    connection, _config, _gov = env
    enricher(env).enrich("ana", [card("li-1")])

    linha = connection.execute(
        "SELECT publicada_ha FROM job_descriptions WHERE job_id = 'li-1'"
    ).fetchone()
    assert linha["publicada_ha"] == "Há 5 horas"


def test_the_stored_age_backfills_the_job_date(env):
    """"Há 5 horas" vira hoje na vaga que a busca trouxe sem data."""
    import datetime

    connection, _config, _gov = env
    enricher(env).enrich("ana", [card("li-1")])

    data = connection.execute(
        "SELECT publicada_em FROM jobs WHERE job_id = 'li-1' AND user_id = 'ana'"
    ).fetchone()["publicada_em"]
    assert data == str(datetime.date.today())


def test_a_date_from_the_search_wins_over_the_derived_one(env):
    """Data vinda da busca e afirmacao da origem; a derivada e aproximada."""
    connection, _config, _gov = env
    connection.execute(
        "UPDATE jobs SET publicada_em = '2026-01-15' WHERE job_id = 'li-1'"
    )
    enricher(env).enrich("ana", [card("li-1")])

    assert connection.execute(
        "SELECT publicada_em FROM jobs WHERE job_id = 'li-1' AND user_id = 'ana'"
    ).fetchone()["publicada_em"] == "2026-01-15"


def test_an_unreadable_age_leaves_the_date_alone(env):
    connection, _config, _gov = env
    fonte = FakeSource()
    original = fonte.describe

    def sem_data(job_id, url):
        dados = original(job_id, url)
        dados["publicada_ha"] = "recentemente"
        return dados

    fonte.describe = sem_data
    enricher(env, fonte).enrich("ana", [card("li-1")])

    assert connection.execute(
        "SELECT publicada_em FROM jobs WHERE job_id = 'li-1' AND user_id = 'ana'"
    ).fetchone()["publicada_em"] is None
