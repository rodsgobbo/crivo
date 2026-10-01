import json
from pathlib import Path

import pytest

from crivo.config import load_config
from crivo.report.renderer import ReportRenderer, build_environment
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

HOSTIL = '<script>alert("xss")</script>'


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    repo.insert(
        "runs",
        {
            "run_id": "run-1", "user_id": "ana", "estado": "concluido",
            "janela": "incremental", "solicitado_em": "2026-08-21T09:00:00+00:00",
            "n_brutos": 40, "n_filtrados": 12, "n_novos": 5,
        },
    )
    yield connection, repo
    connection.close()


def add_vaga(
    repo, job_id="li-1", score=82, estado="novo", titulo="SRE Manager",
    descricao="Sobre a vaga: Kubernetes", blocker=None, emails=("rh@fintech.br",),
    lacunas=("observabilidade",), busca=None, empresa="Fintech",
    primeira_vez_em="2026-08-20",
):
    repo.for_user("ana").insert(
        "jobs",
        {
            "job_id": job_id, "titulo": titulo, "empresa": empresa,
            "url": f"https://exemplo.br/{job_id}", "local": "Sao Paulo, SP",
            "modelo_trabalho": "remote", "publicada_em": "2026-08-20",
            "flags": json.dumps(["top_applicant"]), "blocker": blocker,
            "estado": estado, "primeira_vez_em": primeira_vez_em,
            "ultima_vez_em": "2026-08-21", "busca": busca,
        },
    )
    if descricao is not None:
        repo.insert(
            "job_descriptions",
            {
                "job_id": job_id, "texto": descricao,
                "emails_contato": json.dumps(list(emails)),
                "candidatos": 42, "coletada_em": "2026-08-21",
            },
        )
    repo.for_user("ana").insert(
        "scores",
        {
            "job_id": job_id, "run_id": "run-1", "passada": "final",
            "score": score, "componentes": json.dumps({"componentes": {}}),
            "lacunas": json.dumps(list(lacunas)),
            "diferenciais": json.dumps(["kubernetes"]),
            "descricao_disponivel": int(descricao is not None),
            "sinais_sessao_disponiveis": 1, "criado_em": "2026-08-21",
        },
    )


def add_descarte(repo, titulo="Product Manager", motivo="titulo: product manager"):
    repo.for_user("ana").insert(
        "discards",
        {"run_id": "run-1", "job_id": f"d-{titulo}", "titulo": titulo,
         "empresa": "X", "motivo": motivo},
    )


def render(env, **extras):
    connection, _repo = env
    return ReportRenderer(connection, load_config()).render_run("ana", "run-1", **extras)


# ------------------------------------------------------------------ escape
def test_the_template_never_disables_autoescaping():
    gabarito = (
        Path("src/crivo/report/templates/report.html.j2").read_text(encoding="utf-8")
    )
    assert "|safe" not in gabarito
    assert "autoescape false" not in gabarito


def test_autoescaping_is_on_in_the_engine():
    assert build_environment().autoescape is not False


def test_hostile_text_from_the_source_appears_escaped(env):
    connection, repo = env
    add_vaga(repo, titulo=HOSTIL, descricao=f"Descricao com {HOSTIL}")
    pagina = render(env)
    assert "<script>alert" not in pagina
    assert "&lt;script&gt;" in pagina


def test_a_hostile_company_and_email_are_escaped(env):
    connection, repo = env
    add_vaga(repo, emails=(f'{HOSTIL}@x.br',))
    assert "<script>" not in render(env)


def test_a_hostile_synthesis_is_escaped(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(env, sintese=f"Analise {HOSTIL}")
    assert "<script>alert" not in pagina


# ------------------------------------------------------------------ vagas
def test_jobs_are_ordered_by_score_descending(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", score=40, titulo="Vaga fraca")
    add_vaga(repo, job_id="li-2", score=91, titulo="Vaga forte")
    pagina = render(env)
    assert pagina.index("Vaga forte") < pagina.index("Vaga fraca")


def test_a_measured_job_outranks_an_unmeasured_one(env):
    """Score sem descricao e score com descricao nao medem a mesma coisa.

    Sem descricao o componente de competencias devolve o valor neutro, que
    significa "nao foi possivel ler". Com descricao ele devolve a proporcao
    real, e uma vaga exigente fica abaixo do neutro. Ordenar so pelo numero
    colocava a vaga que ninguem leu na frente da que foi medida.
    """
    connection, repo = env
    add_vaga(repo, job_id="li-1", score=78, titulo="Nunca lida", descricao=None)
    add_vaga(repo, job_id="li-2", score=54, titulo="Lida e medida")
    pagina = render(env)
    assert pagina.index("Lida e medida") < pagina.index("Nunca lida")


def test_unmeasured_jobs_keep_their_order_among_themselves(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", score=40, titulo="Fraca sem leitura", descricao=None)
    add_vaga(repo, job_id="li-2", score=71, titulo="Forte sem leitura", descricao=None)
    pagina = render(env)
    assert pagina.index("Forte sem leitura") < pagina.index("Fraca sem leitura")


def test_an_unmeasured_job_says_so_on_the_page(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", score=78, titulo="Nunca lida", descricao=None)
    assert "sem leitura da descri" in render(env)


def test_each_job_shows_link_place_model_date_and_score(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(env)
    for esperado in ("https://exemplo.br/li-1", "Sao Paulo, SP", "remote",
                     "2026-08-20", "82%"):
        assert esperado in pagina


def test_the_full_description_is_shown_when_available(env):
    connection, repo = env
    add_vaga(repo, descricao="Sobre a vaga: Kubernetes e Terraform")
    assert "Kubernetes e Terraform" in render(env)


def test_contacts_blockers_and_gaps_are_shown(env):
    connection, repo = env
    add_vaga(repo, blocker="presencial em Recife, 2100 km")
    pagina = render(env)
    assert "rh@fintech.br" in pagina
    assert "presencial em Recife" in pagina
    assert "observabilidade" in pagina


def test_a_job_without_a_description_says_it_was_inferred(env):
    connection, repo = env
    add_vaga(repo, descricao=None)
    assert "sem leitura da descri" in render(env)


def test_discarded_jobs_are_listed_with_their_reason(env):
    connection, repo = env
    add_vaga(repo)
    add_descarte(repo)
    pagina = render(env)
    assert "Product Manager" in pagina
    assert "titulo: product manager" in pagina


# ------------------------------------------------------------- destaques
def test_a_new_job_above_the_threshold_is_highlighted(env):
    connection, repo = env
    add_vaga(repo, score=82, estado="novo")
    assert "em destaque" in render(env)


def test_a_job_below_the_threshold_is_not_highlighted(env):
    connection, repo = env
    add_vaga(repo, score=40, estado="novo")
    assert "em destaque" not in render(env)


def test_an_already_seen_job_is_not_highlighted(env):
    connection, repo = env
    add_vaga(repo, score=95, estado="visto")
    assert "em destaque" not in render(env)


# --------------------------------------------------------- nova ou ja vista
def pontuar_em_run_anterior(repo, job_id="li-1", run_id="run-0", score=70):
    """Deixa a vaga com score de um run anterior, como um run repetido deixa."""
    repo.for_user("ana").insert(
        "runs",
        {
            "run_id": run_id, "estado": "concluido", "janela": "incremental",
            "solicitado_em": "2026-08-20T09:00:00+00:00",
        },
    )
    repo.for_user("ana").insert(
        "scores",
        {
            "job_id": job_id, "run_id": run_id, "passada": "final",
            "score": score, "componentes": json.dumps({"componentes": {}}),
            "lacunas": "[]", "diferenciais": "[]",
            "descricao_disponivel": 1, "sinais_sessao_disponiveis": 1,
            "criado_em": "2026-08-20",
        },
    )


def test_a_job_that_never_appeared_before_is_marked_new(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(env)
    assert 'class="novidade nova"' in pagina
    assert 'class="novidade vista"' not in pagina


def test_a_job_scored_in_an_earlier_run_is_marked_already_seen(env):
    connection, repo = env
    add_vaga(repo)
    pontuar_em_run_anterior(repo)
    pagina = render(env)
    assert 'class="novidade vista"' in pagina
    assert "já vista" in pagina
    assert 'class="novidade nova"' not in pagina


def test_the_user_state_does_not_make_a_repeated_job_look_new(env):
    """Estado e decisao de quem le, e nao idade da vaga no acervo.

    Uma vaga que o usuario nunca tocou continua `novo` por quantos runs forem.
    Antes deste criterio o relatorio imprimia esse estado cru, e a vaga que ja
    tinha aparecido em dez relatorios chegava ao decimo primeiro dizendo "novo".
    """
    connection, repo = env
    add_vaga(repo, estado="novo")
    pontuar_em_run_anterior(repo)
    assert "já vista" in render(env)


def test_a_decided_job_shows_the_decision_instead_of_the_novelty(env):
    connection, repo = env
    add_vaga(repo, estado="aplicado")
    pontuar_em_run_anterior(repo)
    pagina = render(env)
    assert 'class="novidade decidida"' in pagina
    assert "já vista" not in pagina


# ------------------------------------------------------- estados degradados
def test_a_run_with_no_survivors_says_so_with_the_counts(env):
    connection, _repo = env
    pagina = render(env)
    assert "Nenhuma vaga sobreviveu" in pagina
    assert "40" in pagina


def test_a_synthesis_failure_is_visible(env):
    connection, repo = env
    add_vaga(repo)
    connection.execute(
        "UPDATE runs SET falha_sintese = 'cadeia esgotada' WHERE run_id = 'run-1'"
    )
    pagina = render(env)
    assert "não pôde ser produzida" in pagina
    assert "cadeia esgotada" in pagina


def test_jobs_left_without_description_by_quota_are_reported(env):
    connection, repo = env
    add_vaga(repo)
    connection.execute("UPDATE runs SET cota_esgotada = 7 WHERE run_id = 'run-1'")
    pagina = render(env)
    assert "7 vaga(s) ficaram sem descrição" in pagina
    assert "não por falha" in pagina


def test_deterministic_mode_is_announced(env):
    connection, repo = env
    add_vaga(repo)
    config = load_config()
    object.__setattr__(config.synthesis, "modo_deterministico", True)
    pagina = ReportRenderer(connection, config).render_run("ana", "run-1")
    assert "Modo determinístico ativo" in pagina


# ---------------------------------------------------- higiene e ranking
def test_hygiene_problems_are_shown(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(
        env,
        problemas_higiene=[{
            "tipo": "cargos_atuais_simultaneos", "campo": "experiencias",
            "trecho": "A — X; B — Y", "detalhe": "dois cargos como atuais",
        }],
    )
    assert "cargos_atuais_simultaneos" in pagina
    assert "dois cargos como atuais" in pagina


def test_no_hygiene_problem_says_so_explicitly(env):
    connection, repo = env
    add_vaga(repo)
    assert "Nenhum problema detectado" in render(env)


def test_the_skill_ranking_is_shown(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(env, ranking_competencias=[("kubernetes", 8), ("terraform", 5)])
    assert "kubernetes" in pagina and "8" in pagina


def _linha_da_competencia(pagina, termo):
    return pagina.split(f"<td>{termo}</td>", 1)[1].split("</tr>", 1)[0]


def test_the_ranking_marks_what_the_profile_lacks(env):
    connection, repo = env
    add_vaga(repo)
    pagina = render(
        env,
        ranking_competencias=[("kubernetes", 8), ("terraform", 5)],
        competencias_ausentes=["terraform"],
    )
    assert "falta" in _linha_da_competencia(pagina, "terraform")
    assert "falta" not in _linha_da_competencia(pagina, "kubernetes")
    assert "1 de 2" in pagina


def test_without_a_profile_the_ranking_accuses_nothing(env):
    """Sem perfil para comparar, "falta" seria acusacao sem pergunta."""
    connection, repo = env
    add_vaga(repo)
    pagina = render(env, ranking_competencias=[("terraform", 5)])
    assert "falta" not in _linha_da_competencia(pagina, "terraform")
    assert "No seu perfil" not in pagina


def test_an_empty_ranking_says_why(env):
    connection, repo = env
    add_vaga(repo)
    assert "Nenhuma vaga com descrição disponível" in render(env)


# ------------------------------------------------------------------ leitura
def test_the_report_emits_no_network_request(env):
    connection, repo = env
    add_vaga(repo)
    import socket

    original = socket.socket

    def proibido(*args, **kwargs):
        raise AssertionError("o relatorio nao pode tocar a rede")

    socket.socket = proibido
    try:
        render(env)
    finally:
        socket.socket = original


def test_an_unknown_run_is_refused(env):
    connection, _repo = env
    with pytest.raises(ValueError):
        ReportRenderer(connection, load_config()).render_run("ana", "inventado")


def test_the_report_never_shows_secret_material(env):
    """Testemunho, credencial e cookie nao alcancam a pagina."""
    connection, repo = env
    add_vaga(repo)
    repo.for_user("ana").insert(
        "provider_credentials",
        {
            "credential_id": "c1", "provedor": "groq",
            "chave_cifrada": b"gsk-segredo-do-usuario",
            "chave_de_dado_cifrada": b"chave-de-dado",
            "sufixo": "ario", "ordem": 0, "criado_em": "2026-08-21",
        },
    )
    repo.for_user("ana").insert(
        "linkedin_connections",
        {
            "campos_identidade": "{}", "escopos_concedidos": "[]",
            "campos_indisponiveis": "[]", "estado": "ativa",
            "token_cifrado": b"testemunho-secreto", "conectada_em": "2026-08-21",
        },
    )
    repo.insert(
        "sessions",
        {
            "session_id": "s1", "user_id": "ana",
            "testemunho_hash": "hash-da-sessao",
            "criada_em": "2026-08-21", "expira_em": "2026-09-21",
        },
    )
    pagina = render(env)
    for proibido in ("gsk-segredo-do-usuario", "chave-de-dado",
                     "testemunho-secreto", "hash-da-sessao"):
        assert proibido not in pagina


# ------------------------------------------------------------- filtragem
def _vaga(**kw):
    base = {
        "job_id": "j1", "titulo": "SRE", "empresa": "Acme", "url": "u",
        "local": "SP", "modelo": "on-site", "publicada_em": "2026-08-01",
        "score": 60, "estado": "novo", "blocker": None, "sinais": [],
        "lacunas": [], "diferenciais": [], "descricao": None,
        "descricao_disponivel": False,
    }
    base.update(kw)
    return base


def test_no_filter_means_no_filtering():
    """Filtro ausente e "nao me pronunciei", e nao "esconda tudo".

    Confundir os dois faria a pagina abrir vazia por omissao, que e a pior
    forma de erro numa tela cuja unica funcao e mostrar resultado.
    """
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a"), _vaga(job_id="b", modelo="remote")]
    assert _aplicar_filtros(vagas, {}) == vagas
    assert _aplicar_filtros(vagas, {"score_minimo": None, "so_remoto": False}) == vagas


def test_the_minimum_score_cuts_below_the_line():
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a", score=80), _vaga(job_id="b", score=40)]
    restantes = _aplicar_filtros(vagas, {"score_minimo": "50"})
    assert [v["job_id"] for v in restantes] == ["a"]


def test_only_read_descriptions_keeps_the_measured_ones():
    """Score sem descricao e desconhecido, e nao baixo."""
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a", descricao_disponivel=True), _vaga(job_id="b")]
    restantes = _aplicar_filtros(vagas, {"so_com_descricao": True})
    assert [v["job_id"] for v in restantes] == ["a"]


def test_remote_and_out_of_radius_are_independent_filters():
    from crivo.report.renderer import _aplicar_filtros

    vagas = [
        _vaga(job_id="remota", modelo="remote"),
        _vaga(job_id="longe", blocker="846 km"),
        _vaga(job_id="perto"),
    ]
    assert [v["job_id"] for v in _aplicar_filtros(vagas, {"so_remoto": True})] == ["remota"]
    assert [
        v["job_id"] for v in _aplicar_filtros(vagas, {"ocultar_fora_do_raio": True})
    ] == ["remota", "perto"]


def test_filtering_never_reorders():
    """A ordem vem da consulta e tem razao propria: descricao lida primeiro."""
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id=str(n), score=100 - n) for n in range(5)]
    restantes = _aplicar_filtros(vagas, {"score_minimo": "97"})
    assert [v["job_id"] for v in restantes] == ["0", "1", "2", "3"]


def test_the_title_filter_ignores_accents_and_case():
    """Digitar o acento certo nao pode ser requisito para achar o proprio cargo."""
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a", titulo="Gerência de Infraestrutura")]
    for digitado in ("gerencia", "GERÊNCIA", "Gerencia"):
        assert _aplicar_filtros(vagas, {"cargo": digitado}), digitado


def test_the_title_filter_matches_words_in_any_order():
    """Quem procura "manager sre" quer achar "SRE Manager"."""
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a", titulo="SRE Engineering Manager")]
    assert _aplicar_filtros(vagas, {"cargo": "manager sre"})
    assert _aplicar_filtros(vagas, {"cargo": "sre manager"})
    assert not _aplicar_filtros(vagas, {"cargo": "sre analista"})


def test_an_empty_title_filter_does_not_filter():
    from crivo.report.renderer import _aplicar_filtros

    vagas = [_vaga(job_id="a"), _vaga(job_id="b")]
    assert _aplicar_filtros(vagas, {"cargo": ""}) == vagas
    assert _aplicar_filtros(vagas, {"cargo": "   "}) == vagas


# --------------------------------------------------- recomendacao de acao
def test_every_job_gets_one_of_three_decisions():
    """Ordenar nao e decidir: o relatorio entrega acao, e nao so um numero."""
    from crivo.report.renderer import APLICAR, DESCARTAR, INDICACAO, recomendar

    for score in range(0, 101, 5):
        acao, porque = recomendar(_vaga(score=score), limiar=70)
        assert acao in (APLICAR, INDICACAO, DESCARTAR)
        assert porque, "toda recomendacao precisa dizer por que"


def test_another_track_is_discarded_regardless_of_score():
    """Uma vaga de outra trilha nao vira boa por casar palavras-chave."""
    from crivo.report.renderer import DESCARTAR, recomendar

    alta = _vaga(score=95, tetos={"outra_trilha": 65})
    assert recomendar(alta, limiar=70)[0] == DESCARTAR


def test_a_mandatory_gap_asks_for_a_referral_and_names_it():
    """Indicacao e o que contorna filtro eliminatorio -- e o motivo diz qual."""
    from crivo.report.renderer import INDICACAO, recomendar

    acao, porque = recomendar(
        _vaga(score=72, tetos={"requisito_eliminatorio": 75}, lacunas=["azure"]),
        limiar=70,
    )
    assert acao == INDICACAO
    assert "azure" in porque


def test_being_a_top_applicant_sends_you_straight_to_the_application():
    """O sinal que so a sessao Premium logada calcula muda a acao, nao a nota.

    Ele e o unico que fala do candidato em vez da vaga: nao conta quem entrou
    na fila, diz onde voce entra nela.
    """
    from crivo.report.renderer import APLICAR, recomendar

    acao, porque = recomendar(_vaga(score=58, sinais=["top_applicant"]), limiar=70)
    assert acao == APLICAR
    assert "melhores candidatos" in porque


def test_a_top_applicant_outranks_a_crowded_queue():
    """Os dois sinais falam da mesma fila e dizem coisas opostas sobre ela.

    Duzentos candidatos mandariam pedir indicacao; estar entre os melhores
    responde justamente a pergunta que a indicacao existe para contornar.
    """
    from crivo.report.renderer import APLICAR, INDICACAO, recomendar

    cheia = _vaga(score=72, sinais=["muitos_candidatos"])
    assert recomendar(cheia, limiar=70)[0] == INDICACAO

    cheia_mas_no_topo = _vaga(
        score=72, sinais=["muitos_candidatos", "top_applicant"]
    )
    assert recomendar(cheia_mas_no_topo, limiar=70)[0] == APLICAR


def test_a_top_applicant_does_not_override_a_mandatory_gap():
    """O LinkedIn nao sabe do requisito que o anuncio marcou como obrigatorio.

    Ordenar candidatos nao e o mesmo que ler o que a vaga exige, e uma lacuna
    eliminatoria continua pedindo indicacao.
    """
    from crivo.report.renderer import INDICACAO, recomendar

    acao, _ = recomendar(
        _vaga(score=72, sinais=["top_applicant"],
              tetos={"requisito_eliminatorio": 75}, lacunas=["ingles fluente"]),
        limiar=70,
    )
    assert acao == INDICACAO


def test_an_already_applied_job_is_never_recommended_again():
    from crivo.report.renderer import JA_APLICADA, recomendar

    assert recomendar(_vaga(score=95, estado="aplicado"), limiar=70)[0] == JA_APLICADA


# --------------------------------------------------- empresas contratando
def _dias_atras(dias):
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc) - timedelta(days=dias)
    ).isoformat(timespec="seconds")


def _empresas(connection, dias=30):
    return ReportRenderer(connection, load_config())._empresas_contratando(
        "ana", dias
    )


def test_a_company_with_several_openings_is_listed(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", titulo="SRE Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(1))
    add_vaga(repo, job_id="li-2", titulo="Platform Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(2))
    # Mesmo cargo republicado: identificador novo, vaga que ja existia.
    add_vaga(repo, job_id="li-3", titulo="SRE Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(3))
    # Uma vaga so nao e sinal de contratacao.
    add_vaga(repo, job_id="li-4", titulo="Head de Dados", empresa="Solo",
             primeira_vez_em=_dias_atras(1))

    linhas = _empresas(connection)

    assert [linha["empresa"] for linha in linhas] == ["Acme"]
    assert linhas[0]["vagas"] == 2


def test_an_opening_outside_the_window_is_not_hiring_today(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", titulo="SRE Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(40))
    add_vaga(repo, job_id="li-2", titulo="Platform Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(45))

    assert _empresas(connection) == []


def test_the_report_shows_who_is_hiring(env):
    connection, repo = env
    add_vaga(repo, job_id="li-1", titulo="SRE Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(1))
    add_vaga(repo, job_id="li-2", titulo="Platform Manager", empresa="Acme",
             primeira_vez_em=_dias_atras(2))

    pagina = render(env)

    assert "Empresas contratando" in pagina
    assert "Acme" in pagina


# ------------------------------------------------- rendimento das buscas
def test_the_search_yield_puts_the_worst_term_first():
    """Esta tabela nao serve para escolher vaga, e sim o que tirar da busca.

    O que se remove esta no fim de qualquer lista ordenada por qualidade, entao
    ela inverte a ordem do resto do relatorio de proposito.
    """
    from crivo.report.renderer import _rendimento

    vagas = [
        _vaga(job_id="a", busca="SRE Manager", score=70),
        _vaga(job_id="b", busca="SRE Manager", score=50),
        _vaga(job_id="c", busca="Coordenador", score=60,
              tetos={"outra_trilha": 65}),
        _vaga(job_id="d", busca="Coordenador", score=55,
              tetos={"outra_trilha": 65}),
    ]
    linhas = _rendimento(vagas)
    assert [l["termo"] for l in linhas] == ["Coordenador", "SRE Manager"]
    assert linhas[0]["fora_da_trilha"] == 2
    assert linhas[1]["fora_da_trilha"] == 0
    assert linhas[1]["melhor"] == 70


def test_a_job_without_a_recorded_search_is_left_out_of_the_yield():
    """Vaga anterior a coluna nao tem origem, e inventar uma seria pior."""
    from crivo.report.renderer import _rendimento

    assert _rendimento([_vaga(job_id="a", busca=None)]) == []


def test_the_report_names_the_search_that_found_each_job(env):
    connection, repo = env
    add_vaga(repo, busca="Head de Infraestrutura")
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "Head de Infraestrutura" in html
    assert "achada por" in html


# ----------------------------------------- janela que a origem nao cumpre
def test_a_job_published_before_the_window_is_marked():
    """A origem nao cumpre o filtro de recencia que a busca envia.

    Num run de 24 horas apareceu uma vaga publicada 22 dias antes, entre as
    melhores pontuadas, sem nenhuma marca. O sistema tinha o dado gravado e nao
    o usava para conferir o que ele mesmo tinha pedido.
    """
    import datetime

    from crivo.report.renderer import marcar_fora_da_janela

    hoje = datetime.date(2026, 8, 26)
    vagas = [
        _vaga(job_id="velha", publicada_em="2026-08-04"),
        _vaga(job_id="nova", publicada_em="2026-08-26"),
        _vaga(job_id="ontem", publicada_em="2026-08-25"),
    ]
    marcar_fora_da_janela(vagas, 24, hoje)
    assert vagas[0]["fora_da_janela"] is True
    assert vagas[1]["fora_da_janela"] is False
    assert vagas[2]["fora_da_janela"] is False


def test_a_job_without_a_date_is_not_accused():
    """Nao se afirma que esta fora de uma janela que nao se sabe se cumpre.

    Marcar o que nao tem data acusaria quase todo run -- numa janela estreita a
    origem devolve a maioria das vagas sem data nenhuma -- e a marca perderia
    o sentido.
    """
    import datetime

    from crivo.report.renderer import marcar_fora_da_janela

    vagas = [_vaga(job_id="sem", publicada_em=None)]
    marcar_fora_da_janela(vagas, 24, datetime.date(2026, 8, 26))
    assert "fora_da_janela" not in vagas[0]


def test_a_wide_window_accuses_nothing_recent():
    import datetime

    from crivo.report.renderer import marcar_fora_da_janela

    vagas = [_vaga(job_id="a", publicada_em="2026-08-04")]
    marcar_fora_da_janela(vagas, 720, datetime.date(2026, 8, 26))
    assert vagas[0]["fora_da_janela"] is False


def test_without_a_recorded_window_nothing_is_marked(env):
    """Run anterior a coluna `janela_horas` nao declara alcance nenhum."""
    import datetime

    from crivo.report.renderer import marcar_fora_da_janela

    vagas = [_vaga(job_id="a", publicada_em="2020-01-01")]
    marcar_fora_da_janela(vagas, None, datetime.date(2026, 8, 26))
    assert "fora_da_janela" not in vagas[0]


def test_the_report_warns_at_the_top_when_the_window_leaked(env):
    connection, repo = env
    connection.execute("UPDATE runs SET janela_horas = 24 WHERE run_id = 'run-1'")
    add_vaga(repo, job_id="li-velha", titulo="Engineering Lead")
    connection.execute(
        "UPDATE jobs SET publicada_em = '2026-01-05' WHERE job_id = 'li-velha'"
    )
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "fora da janela" in html
    assert "republicado pode carregar data antiga" in html


# ------------------------------------------- ordem depois da releitura
def test_the_model_score_decides_the_order_where_it_exists():
    """O cálculo determinístico erra numa coisa e erra caro.

    Por ser proporção, ele premia anúncio vago: num run real um "Banco de
    Talentos" ficou acima de um "Especialista de SRE". Onde o modelo releu, é a
    leitura dele que ordena.
    """
    from crivo.report.renderer import ordenar

    vagas = [
        _vaga(job_id="vago", score=70, nota_do_modelo=10, descricao_disponivel=True),
        _vaga(job_id="sre", score=67, nota_do_modelo=90, descricao_disponivel=True),
    ]
    assert [v["job_id"] for v in ordenar(vagas)] == ["sre", "vago"]


def test_a_reread_job_comes_before_one_never_considered():
    """Não ter nota do modelo é "não chegou a ser lida", não "foi reprovada".

    O modelo só lê o topo, então ausência aqui não é julgamento negativo.
    """
    from crivo.report.renderer import ordenar

    vagas = [
        _vaga(job_id="nao-lida", score=80, descricao_disponivel=True),
        _vaga(job_id="relida", score=50, nota_do_modelo=55, descricao_disponivel=True),
    ]
    assert [v["job_id"] for v in ordenar(vagas)] == ["relida", "nao-lida"]


def test_without_any_reread_the_deterministic_order_is_untouched():
    """Sem credencial de modelo o relatório é exatamente o de antes."""
    from crivo.report.renderer import ordenar

    vagas = [
        _vaga(job_id="a", score=54, descricao_disponivel=True),
        _vaga(job_id="b", score=78, descricao_disponivel=False),
        _vaga(job_id="c", score=90, descricao_disponivel=True),
    ]
    assert [v["job_id"] for v in ordenar(vagas)] == ["c", "a", "b"]


def test_the_description_rule_survives_below_the_reread_tier():
    """Score sem descrição e score com descrição não medem a mesma coisa.

    A regra é anterior à releitura e continua valendo abaixo dela: sem descrição
    o componente de competências devolve o valor neutro, que significa "não foi
    possível ler".
    """
    from crivo.report.renderer import ordenar

    vagas = [
        _vaga(job_id="sem-leitura", score=78, descricao_disponivel=False),
        _vaga(job_id="medida", score=54, descricao_disponivel=True),
    ]
    assert [v["job_id"] for v in ordenar(vagas)] == ["medida", "sem-leitura"]


def test_the_report_shows_where_the_score_came_from(env):
    """Duas notas que não medem a mesma coisa não podem parecer a mesma."""
    connection, repo = env
    add_vaga(repo, job_id="li-1", score=70, titulo="Banco de Talentos")
    connection.execute(
        "UPDATE scores SET nota_do_modelo = 12, motivo_do_modelo = ? "
        "WHERE job_id = 'li-1'",
        ("cadastro reserva, nao e vaga",),
    )
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "12%" in html
    assert "relida" in html
    assert "cadastro reserva, nao e vaga" in html
    assert "o cálculo por competências dava 70%" in html
