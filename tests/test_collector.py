import datetime

import pytest

from crivo.pipeline.collector import CollectionCancelled, Collector
from crivo.pipeline.sources.guest import (
    PRESENCIAL,
    REMOTO,
    Card,
    CollectionError,
    MultiPortalSource,
    SearchOutcome,
    to_card,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository


def linha(**over):
    base = {
        "id": "li-1",
        "site": "linkedin",
        "job_url": "https://linkedin.com/jobs/view/1",
        "title": "SRE Manager",
        "company": "Fintech",
        "location": "Sao Paulo, SP",
        "date_posted": datetime.date(2026, 8, 1),
        "is_remote": True,
        "emails": None,
        "description": None,
        "min_amount": None,
        "max_amount": None,
        "currency": None,
        "interval": None,
    }
    base.update(over)
    return base


class FakeSource:
    """Fonte falsa: devolve o que for programado, sem tocar a rede."""

    def __init__(self, por_busca=None):
        self.por_busca = por_busca or {}
        self.chamadas = []

    def search(self, termo, local, janela_horas, quantidade):
        self.chamadas.append((termo, local, janela_horas, quantidade))
        return self.por_busca.get(termo, SearchOutcome(busca=termo, improdutiva=True))

    def describe(self, job_id, url):
        return {"texto": "descricao", "emails": []}


def card(job_id="li-1", titulo="SRE Manager", **over):
    base = dict(
        job_id=job_id, titulo=titulo, empresa="Fintech",
        url=f"https://linkedin.com/jobs/view/{job_id}", local="Sao Paulo, SP",
        modelo_trabalho=REMOTO, publicada_em="2026-08-01",
    )
    base.update(over)
    return Card(**base)


@pytest.fixture
def collector(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    yield connection
    connection.close()


# ------------------------------------------------------------------ adaptador
def test_a_row_becomes_a_normalized_card():
    convertido = to_card(linha(), busca="SRE Manager")
    assert convertido.job_id == "li-1"
    assert convertido.titulo == "SRE Manager"
    assert convertido.modelo_trabalho == REMOTO
    assert convertido.publicada_em == "2026-08-01"
    assert convertido.busca == "SRE Manager"


def test_a_row_without_url_or_title_is_discarded():
    assert to_card(linha(job_url=None)) is None
    assert to_card(linha(title="")) is None


def test_a_row_without_identifier_derives_one_from_the_url():
    convertido = to_card(linha(id=None))
    assert convertido.job_id
    assert convertido.job_id != "li-1"
    assert to_card(linha(id=None)).job_id == convertido.job_id


def test_the_work_model_is_normalized():
    assert to_card(linha(is_remote=False)).modelo_trabalho == PRESENCIAL
    assert to_card(linha(is_remote=None)).modelo_trabalho is None
    assert to_card(linha(is_remote="true")).modelo_trabalho == REMOTO


def test_missing_tabular_values_become_none():
    ausente = float("nan")
    convertido = to_card(linha(company=ausente, location=ausente))
    assert convertido.empresa is None
    assert convertido.local is None


def test_salary_is_composed_when_present():
    convertido = to_card(
        linha(min_amount=20000, max_amount=28000, currency="BRL", interval="monthly")
    )
    assert "BRL" in convertido.salario and "20000" in convertido.salario


def test_the_discovery_call_never_asks_for_descriptions():
    capturado = {}

    def scrape(**kwargs):
        capturado.update(kwargs)
        return [linha()]

    MultiPortalSource(scrape=scrape).search("SRE Manager", "Brasil", 24, 25)
    assert capturado["linkedin_fetch_description"] is False
    assert capturado["hours_old"] == 24


def test_a_source_error_becomes_a_recorded_failure():
    def scrape(**kwargs):
        raise TimeoutError("sem resposta")

    saida = MultiPortalSource(scrape=scrape).search("SRE", "Brasil", 24, 25)
    assert saida.falha and "TimeoutError" in saida.falha
    assert saida.cards == []


def test_an_empty_result_is_unproductive_not_a_failure():
    saida = MultiPortalSource(scrape=lambda **k: []).search("SRE", "Brasil", 24, 25)
    assert saida.improdutiva is True
    assert saida.falha is None


# ------------------------------------------------ descricao pela rota publica
# Marcacao copiada da origem real, nao redigida a mao. O primeiro fixture deste
# arquivo trazia a contagem de candidatos dentro do proprio `figure`; a origem
# nunca produziu isso -- ela poe a legenda num `figcaption` irmao do icone. O
# padrao lia o `figure`, capturava o espaco em branco ate o `<span>` do icone e
# devolvia None em toda vaga de verdade, com o teste verde o tempo todo.
_TOPCARD = """
  <div class="topcard__flavor-row">
    <span class="posted-time-ago__text posted-time-ago__text--new topcard__flavor--metadata">
      H&aacute; 2 horas
    </span>
    <figure class="num-applicants__figure topcard__flavor--metadata topcard__flavor--bullet">
      <span class="num-applicants__icon num-applicants__icon--clock lazy-load"></span>
      <figcaption class="num-applicants__caption">
        {legenda}
      </figcaption>
    </figure>
  </div>
"""

#: As tres formas que a origem devolveu numa amostra de dez vagas reais.
POUCOS = "Seja um dos 25 primeiros a se candidatar"
MUITOS = "Mais de 200 candidaturas"
SEM_LEGENDA = None


def fragmento(legenda=POUCOS):
    topcard = (
        _TOPCARD.format(legenda=legenda) if legenda is not None
        else _TOPCARD.split("<figure")[0] + "</div>"
    )
    return """
<html><body>
  <h2 class="topcard__title">Mgr Infra</h2>
""" + topcard + """
  <div class="description__text description__text--rich">
    <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
      <p>Buscamos pessoa para liderar <strong>infraestrutura</strong>.</p>
      <ul><li>Kubernetes &amp; Terraform</li><li>Observabilidade</li></ul>
      <p>Envie para vagas@empresa.com.br</p>
    </div>
  </div>
  <ul class="description__job-criteria-list">
    <li><h3 class="description__job-criteria-subheader">N&iacute;vel de experi&ecirc;ncia</h3>
        <span class="description__job-criteria-text">Pleno-s&ecirc;nior</span></li>
    <li><h3 class="description__job-criteria-subheader">Tipo de emprego</h3>
        <span class="description__job-criteria-text">Tempo integral</span></li>
  </ul>
</body></html>
"""


FRAGMENTO = fragmento()


class FakeHTTP:
    """Cliente falso que devolve o fragmento, sem tocar a rede."""

    def __init__(self, corpo=FRAGMENTO, status=200):
        self.corpo = corpo
        self.status = status
        self.pedidos = []

    def get(self, url, **kwargs):
        self.pedidos.append(url)

        class Resposta:
            status_code = self.status
            text = self.corpo

        return Resposta()


def test_the_description_comes_from_the_public_posting_endpoint():
    """A primeira versao passava a URL como termo de busca e nunca devolvia nada.

    A biblioteca de coleta busca por palavra-chave e nao sabe buscar uma vaga
    por identificador. O defeito so apareceu contra a origem real, porque o
    dublê respondia ao contrato e nao ao comportamento da biblioteca.
    """
    http = FakeHTTP()
    resultado = MultiPortalSource(http=http).describe("li-4457375990", "https://x")

    assert http.pedidos == [
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4457375990"
    ]
    assert "Kubernetes" in resultado["texto"]
    assert "Observabilidade" in resultado["texto"]


def test_the_description_excludes_the_page_chrome():
    resultado = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")
    assert "candidaturas" not in resultado["texto"]
    assert "Seja um dos 25" not in resultado["texto"]


def test_markup_entities_are_decoded():
    resultado = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")
    assert "Kubernetes & Terraform" in resultado["texto"]
    assert "&amp;" not in resultado["texto"]


def test_contact_emails_are_extracted_from_the_body():
    resultado = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")
    assert resultado["emails"] == ["vagas@empresa.com.br"]


def test_the_public_route_carries_signals_the_design_expected_from_the_session():
    """Recencia e concorrencia vem sem custo de sessao."""
    resultado = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")
    assert "2 horas" in resultado["publicada_ha"]
    assert resultado["sinais"] == ["early_applicant"]


def test_few_applicants_is_a_signal_and_not_a_count():
    """"Seja um dos 25 primeiros" nao diz que ha 25 candidatos.

    O 25 e o limiar da propria frase. Lido como contagem, ele inverteria o
    sinal: a vaga disputada apareceria com 200 e a vazia com 25.
    """
    resultado = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")
    assert resultado["candidatos"] is None
    assert "early_applicant" in resultado["sinais"]


def test_a_floor_of_applicants_is_read_as_a_number():
    http = FakeHTTP(fragmento(MUITOS))
    assert MultiPortalSource(http=http).describe("li-1", "")["candidatos"] == 200


def test_an_absent_caption_leaves_the_count_unknown():
    http = FakeHTTP(fragmento(SEM_LEGENDA))
    resultado = MultiPortalSource(http=http).describe("li-1", "")
    assert resultado["candidatos"] is None
    assert resultado["sinais"] == []


def test_the_posting_criteria_are_captured():
    criterios = MultiPortalSource(http=FakeHTTP()).describe("li-1", "")["criterios"]
    assert criterios["Tipo de emprego"] == "Tempo integral"


def test_a_high_competition_posting_is_flagged():
    sinais = MultiPortalSource(http=FakeHTTP(fragmento(MUITOS))).describe(
        "li-1", ""
    )["sinais"]
    assert "muitos_candidatos" in sinais
    assert "early_applicant" not in sinais


def test_a_refused_request_is_an_error():
    with pytest.raises(CollectionError) as err:
        MultiPortalSource(http=FakeHTTP(status=429)).describe("li-1", "")
    assert "429" in str(err.value)


def test_a_body_without_the_description_block_is_an_error():
    with pytest.raises(CollectionError) as err:
        MultiPortalSource(http=FakeHTTP("<html><body>nada</body></html>")).describe(
            "li-9", ""
        )
    assert "li-9" in str(err.value)
    assert "mudou de forma" in str(err.value)


# ------------------------------------------------------------------ coleta
def test_cards_are_stored_under_the_run_owner(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})
    resultado = Collector(collector, fonte).collect("ana", ["A"], "Brasil", 24)
    assert resultado.novos == ["li-1"]
    row = collector.execute("SELECT user_id, estado FROM jobs").fetchone()
    assert (row["user_id"], row["estado"]) == ("ana", "novo")


def test_a_repeated_identifier_within_the_run_is_discarded(collector):
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", cards=[card()]),
    })
    resultado = Collector(collector, fonte).collect("ana", ["A", "B"], "Brasil", 24)
    assert len(resultado.cards) == 1
    assert resultado.repetidos == 1
    assert resultado.brutos == 2


def test_unproductive_and_failed_searches_are_recorded_apart(collector):
    fonte = FakeSource({
        "vazia": SearchOutcome(busca="vazia", improdutiva=True),
        "quebrada": SearchOutcome(busca="quebrada", falha="TimeoutError: x"),
        "boa": SearchOutcome(busca="boa", cards=[card()]),
    })
    resultado = Collector(collector, fonte).collect(
        "ana", ["vazia", "quebrada", "boa"], "Brasil", 24
    )
    assert resultado.improdutivas == ["vazia"]
    assert list(resultado.falhas) == ["quebrada"]
    assert len(resultado.cards) == 1


def test_a_failed_search_does_not_stop_the_remaining_ones(collector):
    fonte = FakeSource({
        "quebrada": SearchOutcome(busca="quebrada", falha="erro"),
        "boa": SearchOutcome(busca="boa", cards=[card()]),
    })
    Collector(collector, fonte).collect("ana", ["quebrada", "boa"], "Brasil", 24)
    assert len(fonte.chamadas) == 2


def test_a_job_seen_again_is_not_new_and_keeps_its_first_sighting(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})
    c = Collector(collector, fonte)
    c.collect("ana", ["A"], "Brasil", 24)
    primeira = collector.execute("SELECT primeira_vez_em FROM jobs").fetchone()[0]

    segunda = c.collect("ana", ["A"], "Brasil", 24)
    assert segunda.novos == []
    row = collector.execute("SELECT primeira_vez_em, ausencias_elegiveis FROM jobs").fetchone()
    assert row["primeira_vez_em"] == primeira
    assert row["ausencias_elegiveis"] == 0


def test_reappearing_resets_the_eligible_absence_counter(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})
    c = Collector(collector, fonte)
    c.collect("ana", ["A"], "Brasil", 24)
    collector.execute("UPDATE jobs SET ausencias_elegiveis = 2")
    c.collect("ana", ["A"], "Brasil", 24)
    assert collector.execute("SELECT ausencias_elegiveis FROM jobs").fetchone()[0] == 0


def test_the_window_reaches_the_source(collector):
    fonte = FakeSource()
    Collector(collector, fonte).collect("ana", ["A"], "Brasil", 720)
    assert fonte.chamadas[0][2] == 720


# ------------------------------------------------------------------ sementes
# ------------------------------------------------------------------ sinais
def test_card_signals_are_recorded_on_the_card(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})
    c = Collector(collector, fonte)
    c.collect("ana", ["A"], "Brasil", 24)
    c.record_signals("ana", "li-1", ["top_applicant", "early_applicant"])
    assert c.signals_for("ana", "li-1") == ["early_applicant", "top_applicant"]


def test_a_card_without_signals_has_an_empty_list(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})
    c = Collector(collector, fonte)
    c.collect("ana", ["A"], "Brasil", 24)
    assert c.signals_for("ana", "li-1") == []


# -------------------------------------------------- bloqueio, parada, avanco
def test_a_block_is_not_recorded_as_an_empty_market(collector):
    """As duas coisas chegavam com a mesma forma, e exigem resposta oposta.

    Uma calibra o planejador -- este termo nao rende. A outra manda parar de
    pedir. Confundi-las fazia o relatorio afirmar que nao havia vagas quando o
    que houve foi um portao fechado.
    """
    fonte = FakeSource({"A": SearchOutcome(busca="A", bloqueada=True, falha="429")})
    resultado = Collector(collector, fonte).collect("ana", ["A"], "Brasil", 24)

    assert resultado.bloqueadas == ["A"]
    assert resultado.improdutivas == []
    assert resultado.bloqueada


def test_after_a_block_the_remaining_searches_are_not_attempted(collector):
    """Cada tentativa a mais contra portao fechado alonga o bloqueio."""
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", bloqueada=True, falha="429"),
        "C": SearchOutcome(busca="C", cards=[card("li-2")]),
    })
    resultado = Collector(collector, fonte).collect(
        "ana", ["A", "B", "C"], "Brasil", 24
    )

    assert [c[0] for c in fonte.chamadas] == ["A", "B"]
    assert resultado.nao_tentadas == ["C"]


def test_what_was_collected_before_a_block_stays_in_the_database(collector):
    """O que ja custou requisicao nao se joga fora quando a coleta para."""
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", bloqueada=True, falha="429"),
    })
    Collector(collector, fonte).collect("ana", ["A", "B"], "Brasil", 24)

    assert collector.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_progress_is_reported_after_each_search(collector):
    """Sem isto a coleta inteira era um salto em que nada no banco mudava."""
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", cards=[card("li-2")]),
    })
    vistos = []
    Collector(collector, fonte).collect(
        "ana", ["A", "B"], "Brasil", 24,
        progresso=lambda r, feitas, total: vistos.append(
            (feitas, total, len(r.cards))
        ),
    )
    assert vistos == [(1, 2, 1), (2, 2, 2)]


def test_a_cancelled_collection_stops_between_searches(collector):
    """Parar no meio de uma busca deixaria requisicao pela metade."""
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", cards=[card("li-2")]),
        "C": SearchOutcome(busca="C", cards=[card("li-3")]),
    })

    def parar(resultado, feitas, total):
        if feitas == 1:
            raise CollectionCancelled("ana")

    with pytest.raises(CollectionCancelled):
        Collector(collector, fonte).collect(
            "ana", ["A", "B", "C"], "Brasil", 24, progresso=parar
        )

    assert [c[0] for c in fonte.chamadas] == ["A"]
    # A vaga da busca que chegou a rodar ja esta gravada: cancelar nao pode
    # sair mais caro do que deixar terminar.
    assert collector.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_every_search_passes_through_the_governor(collector):
    """A coleta era o unico caminho que emitia requisicao sem contencao."""
    from crivo.config import load_config
    from crivo.pipeline.governor import RateGovernor

    esperas = []
    governador = RateGovernor(
        collector, load_config(), sleep=lambda s: esperas.append(s)
    )
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card()]),
        "B": SearchOutcome(busca="B", cards=[card("li-2")]),
        "C": SearchOutcome(busca="C", cards=[card("li-3")]),
    })
    Collector(collector, fonte, governador).collect(
        "ana", ["A", "B", "C"], "Brasil", 24
    )

    # Tres buscas, dois intervalos: a primeira nao espera por nada.
    assert len(esperas) == 2
    assert governador.stats.chamadas == 3


def test_a_standing_block_turns_the_search_into_a_blocked_outcome(collector):
    """Bloqueio ja registrado recusa a chamada, e isso nao pode virar excecao.

    O run precisa terminar com o que tem e dizer que foi barrado. Deixar
    `CollectionBlocked` subir derrubaria o estagio e o usuario veria um traceback
    em vez de um relatorio parcial.
    """
    from crivo.config import load_config
    from crivo.pipeline.governor import RateGovernor

    governador = RateGovernor(collector, load_config(), sleep=lambda _s: None)
    governador.record_block(3)
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card()])})

    resultado = Collector(collector, fonte, governador).collect(
        "ana", ["A"], "Brasil", 24
    )

    assert resultado.bloqueadas == ["A"]
    assert fonte.chamadas == []


# ---------------------------------------- o 429 que a biblioteca engolia
def _raspador_que_e_barrado(mensagem="429 Response - Blocked by LinkedIn"):
    """Imita a biblioteca: registra o erro no proprio log e devolve vazio.

    Este e o comportamento real dela, e e o que tornava o bloqueio invisivel:
    o valor de retorno de uma coleta barrada e identico ao de uma janela sem
    vagas. O dublê precisa reproduzir isso, e nao levantar excecao -- se
    levantasse, o teste passaria por um caminho que a origem nunca percorre.
    """
    import logging

    log = logging.getLogger("JobSpy:LinkedIn")
    log.propagate = False

    def raspar(**_kwargs):
        log.error(mensagem)
        return []

    return raspar


def test_a_swallowed_block_becomes_a_blocked_outcome():
    fonte = MultiPortalSource(scrape=_raspador_que_e_barrado())
    saida = fonte.search("SRE Manager", "Brasil", 24, 25)

    assert saida.bloqueada is True
    assert saida.improdutiva is False
    assert "429" in saida.falha


def test_an_empty_window_is_still_reported_as_unproductive():
    """Vazio de verdade nao pode virar bloqueio: ele calibra o planejador."""
    fonte = MultiPortalSource(scrape=lambda **_k: [])
    saida = fonte.search("cargo que nao existe", "Brasil", 24, 25)

    assert saida.improdutiva is True
    assert saida.bloqueada is False


def test_the_listener_does_not_outlive_the_call():
    """Ouvinte pendurado para sempre acusaria bloqueio de uma busca em outra."""
    import logging

    fonte = MultiPortalSource(scrape=_raspador_que_e_barrado())
    fonte.search("A", "Brasil", 24, 25)
    antes = len(logging.getLogger("JobSpy:LinkedIn").handlers)

    fonte.search("B", "Brasil", 24, 25)
    assert len(logging.getLogger("JobSpy:LinkedIn").handlers) == antes


# ------------------------------------------------- proveniencia da vaga
def test_the_search_that_found_a_job_is_recorded(collector):
    """Sem isto, "por que esta vaga apareceu?" so tinha resposta no codigo."""
    fonte = FakeSource({"SRE Manager": SearchOutcome(
        busca="SRE Manager", cards=[card(busca="SRE Manager")]
    )})
    Collector(collector, fonte).collect("ana", ["SRE Manager"], "Brasil", 24)
    assert collector.execute(
        "SELECT busca FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["busca"] == "SRE Manager"


def test_a_reappearance_does_not_rewrite_where_the_job_came_from(collector):
    """A pergunta e de origem, e reaparecer nao muda de onde algo veio."""
    c = Collector(collector, FakeSource({"primeira": SearchOutcome(
        busca="primeira", cards=[card(busca="primeira")]
    )}))
    c.collect("ana", ["primeira"], "Brasil", 24)

    c2 = Collector(collector, FakeSource({"segunda": SearchOutcome(
        busca="segunda", cards=[card(busca="segunda")]
    )}))
    resultado = c2.collect("ana", ["segunda"], "Brasil", 24)

    assert resultado.novos == [], "a vaga ja existia"
    assert collector.execute(
        "SELECT busca FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["busca"] == "primeira"


# ------------------------------------------------- recencia por extenso
def test_the_posting_age_becomes_a_date():
    """O card chega sem data na janela estreita; a pagina do anuncio tem."""
    import datetime

    from crivo.pipeline.sources.guest import data_de_publicacao

    hoje = datetime.date(2026, 8, 26)
    assert data_de_publicacao("há 1 dia", hoje) == "2026-08-25"
    assert data_de_publicacao("há 3 dias", hoje) == "2026-08-23"
    assert data_de_publicacao("há 2 semanas", hoje) == "2026-08-12"
    assert data_de_publicacao("3 weeks ago", hoje) == "2026-08-05"
    assert data_de_publicacao("Reposted 5 days ago", hoje) == "2026-08-21"


def test_an_age_in_hours_is_today_and_not_unknown():
    """Perder isto e perder exatamente a vaga mais fresca."""
    import datetime

    from crivo.pipeline.sources.guest import data_de_publicacao

    hoje = datetime.date(2026, 8, 26)
    assert data_de_publicacao("há 2 horas", hoje) == "2026-08-26"
    assert data_de_publicacao("há 20 minutos", hoje) == "2026-08-26"


def test_an_unreadable_age_stays_unknown():
    """Data inventada e pior que nenhuma: e sobre ela que a janela decide."""
    import datetime

    from crivo.pipeline.sources.guest import data_de_publicacao

    hoje = datetime.date(2026, 8, 26)
    assert data_de_publicacao(None, hoje) is None
    assert data_de_publicacao("", hoje) is None
    assert data_de_publicacao("ontem", hoje) is None
    assert data_de_publicacao("há muito tempo", hoje) is None


# --------------------------------------- data que envelhecia sozinha
def test_a_reappearing_job_gets_its_publication_date_refreshed(collector):
    """Anuncio republicado ganha data nova e mantem o identificador.

    Sem esta atualizacao a vaga guardava para sempre a data da primeira vez que
    a vimos. Numa busca de 24 horas isso fez dez vagas parecerem violacao da
    janela quando eram republicacoes com data nossa vencida -- e a conclusao
    errada foi tirada a partir disso.
    """
    c1 = Collector(collector, FakeSource({"a": SearchOutcome(
        busca="a", cards=[card(publicada_em="2026-08-04")]
    )}))
    c1.collect("ana", ["a"], "Brasil", 720)

    c2 = Collector(collector, FakeSource({"b": SearchOutcome(
        busca="b", cards=[card(publicada_em="2026-08-26")]
    )}))
    c2.collect("ana", ["b"], "Brasil", 24)

    assert collector.execute(
        "SELECT publicada_em FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["publicada_em"] == "2026-08-26"


def test_a_card_without_a_date_does_not_erase_the_one_we_have(collector):
    """Ausencia aqui e a origem calando, e nao afirmando que nao ha data.

    O LinkedIn omite a data justamente nos anuncios recentes, entao apagar
    apagaria a boa informacao de uma coleta anterior.
    """
    c1 = Collector(collector, FakeSource({"a": SearchOutcome(
        busca="a", cards=[card(publicada_em="2026-08-04")]
    )}))
    c1.collect("ana", ["a"], "Brasil", 720)

    c2 = Collector(collector, FakeSource({"b": SearchOutcome(
        busca="b", cards=[card(publicada_em=None)]
    )}))
    c2.collect("ana", ["b"], "Brasil", 24)

    assert collector.execute(
        "SELECT publicada_em FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["publicada_em"] == "2026-08-04"


def test_the_first_sighting_is_still_never_rewritten(collector):
    """Reaparecer atualiza a data do anuncio, nao quando o conhecemos."""
    c1 = Collector(collector, FakeSource({"a": SearchOutcome(
        busca="a", cards=[card(publicada_em="2026-08-04")]
    )}))
    c1.collect("ana", ["a"], "Brasil", 720)
    primeira = collector.execute(
        "SELECT primeira_vez_em FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["primeira_vez_em"]

    c2 = Collector(collector, FakeSource({"b": SearchOutcome(
        busca="b", cards=[card(publicada_em="2026-08-26")]
    )}))
    c2.collect("ana", ["b"], "Brasil", 24)

    assert collector.execute(
        "SELECT primeira_vez_em FROM jobs WHERE job_id = 'li-1'"
    ).fetchone()["primeira_vez_em"] == primeira
