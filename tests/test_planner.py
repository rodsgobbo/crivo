import pytest

from crivo.pipeline.planner import (
    MINIMO_DE_TERMOS,
    PlannerError,
    as_list,
    detect_axes,
    load_filters,
    plan,
)

PERFIL = {
    "headline": "Gerente de Infraestrutura e Cloud",
    "experiencias": [
        {
            "titulo": "Gerente de Infraestrutura",
            "descricao": "Lidero time de SRE com foco em observabilidade",
        }
    ],
    "competencias": ["Kubernetes", "Terraform", "FinOps"],
}


@pytest.fixture
def filters():
    return load_filters()


def test_the_shipped_filters_load(filters):
    assert "SRE" in filters.eixos
    assert filters.niveis["manager"]
    assert filters.rejeicao_titulos


def test_a_missing_filters_file_names_the_path(tmp_path):
    with pytest.raises(PlannerError) as err:
        load_filters(tmp_path / "ausente.toml")
    assert "ausente.toml" in str(err.value)


def test_an_incomplete_filters_file_names_the_section(tmp_path):
    alvo = tmp_path / "filters.toml"
    alvo.write_text("[eixos]\n", encoding="utf-8")
    with pytest.raises(PlannerError) as err:
        load_filters(alvo)
    assert "eixos_en" in str(err.value)


# ------------------------------------------------------------------ eixos
def test_axes_come_from_headline_experience_and_skills(filters):
    eixos = detect_axes(PERFIL, filters)
    assert {"Infraestrutura", "Cloud", "SRE", "Observabilidade", "FinOps"} <= set(eixos)


def test_axes_are_sorted_regardless_of_experience_order(filters):
    invertido = dict(PERFIL)
    invertido["competencias"] = list(reversed(PERFIL["competencias"]))
    assert detect_axes(PERFIL, filters) == detect_axes(invertido, filters)


def test_a_profile_without_matching_terms_has_no_axes(filters):
    assert detect_axes({"headline": "Chef de cozinha"}, filters) == []


# ------------------------------------------------------------------ buscas
def test_every_query_has_at_least_two_terms(filters):
    for busca in plan(PERFIL, "manager", filters):
        assert busca.termos >= MINIMO_DE_TERMOS


def test_both_languages_are_covered(filters):
    buscas = plan(PERFIL, "manager", filters)
    idiomas = {b.idioma for b in buscas}
    assert idiomas == {"pt", "en"}


def test_anchors_are_included(filters):
    textos = as_list(plan(PERFIL, "manager", filters))
    assert "Gerente de Infraestrutura e Cloud" in textos
    assert "SRE Manager" in textos


def test_the_same_input_always_produces_the_same_list(filters):
    primeira = as_list(plan(PERFIL, "manager", filters))
    segunda = as_list(plan(PERFIL, "manager", filters))
    assert primeira == segunda


def test_the_list_has_no_duplicates(filters):
    textos = as_list(plan(PERFIL, "manager", filters))
    assert len(textos) == len(set(t.lower() for t in textos))


def test_the_level_changes_the_labels(filters):
    de_gerente = set(as_list(plan(PERFIL, "manager", filters)))
    de_executivo = set(as_list(plan(PERFIL, "executive", filters)))
    assert de_gerente != de_executivo
    assert any("Head" in t for t in de_executivo)
    assert any("Gerente" in t for t in de_gerente)


def test_an_unknown_level_still_yields_the_anchors_it_has(filters):
    buscas = plan(PERFIL, "nivel-inventado", filters)
    assert all(b.origem == "ancora" for b in buscas) or buscas == []


def test_a_profile_without_axes_falls_back_to_anchors(filters):
    buscas = plan({"headline": "Chef de cozinha"}, "manager", filters)
    assert {b.origem for b in buscas} == {"ancora"}


def test_queries_carry_where_they_came_from(filters):
    origens = {b.origem for b in plan(PERFIL, "manager", filters)}
    assert origens == {"eixo", "ancora"}


def test_the_serializable_form_is_plain_text(filters):
    textos = as_list(plan(PERFIL, "manager", filters))
    assert all(isinstance(t, str) for t in textos)


def test_no_query_mixes_languages(filters):
    """Uma coleta real expos "Database Coordenador": eixo em ingles com rotulo
    em portugues. Nao existe como cargo e cada uma custa uma requisicao."""
    rotulos_pt = set(filters.niveis.get("manager", ()))
    rotulos_en = set(filters.niveis_en.get("manager", ()))
    so_pt = rotulos_pt - rotulos_en
    so_en = rotulos_en - rotulos_pt

    for busca in plan(PERFIL, "manager", filters):
        if busca.origem != "eixo":
            continue
        tem_pt = any(r in busca.texto for r in so_pt)
        tem_en = any(r in busca.texto for r in so_en)
        assert not (tem_pt and tem_en), busca.texto


def test_the_english_axis_never_meets_a_portuguese_label(filters):
    textos = {b.texto for b in plan(PERFIL, "manager", filters)}
    assert "Database Coordenador" not in textos
    assert not any(
        t.startswith("Database ") and "Coordenador" in t for t in textos
    )


# ------------------------------------------------------ composicao das buscas
def test_a_repeated_word_at_the_seam_is_dropped():
    """"Platform Engineering" + "Engineering Manager" nao vira tres palavras."""
    from crivo.pipeline.planner import compose

    assert compose("Platform Engineering", "Engineering Manager") == (
        "Platform Engineering Manager"
    )


def test_a_seam_without_overlap_keeps_both_sides_whole():
    from crivo.pipeline.planner import compose

    assert compose("Database", "Engineering Manager") == (
        "Database Engineering Manager"
    )


def test_the_overlap_is_measured_in_words_and_not_in_characters():
    """"Data" nao pode comer a letra que distingue "Database Manager"."""
    from crivo.pipeline.planner import compose

    assert compose("Data", "Database Manager") == "Data Database Manager"


def test_a_multi_word_overlap_is_dropped_whole():
    from crivo.pipeline.planner import compose

    assert compose("Site Reliability Engineering", "Engineering Manager") == (
        "Site Reliability Engineering Manager"
    )


def test_the_comparison_ignores_capitalization():
    from crivo.pipeline.planner import compose

    assert compose("Cloud", "cloud Manager") == "Cloud Manager"


def test_no_planned_query_repeats_a_word_at_the_seam():
    """A prova contra o perfil real, e nao contra um exemplo escolhido."""
    from pathlib import Path

    from crivo.pipeline.planner import load_filters, plan

    filtros = load_filters(Path("config/filters.toml"))
    perfil = {
        "headline": "Engineering Manager — SRE, Database, Platform, FinOps",
        "competencias": ["AWS", "Kubernetes", "PostgreSQL", "MongoDB", "FinOps",
                         "Terraform", "Observabilidade", "SRE"],
        "experiencias": [],
    }
    for busca in plan(perfil, "manager", filtros):
        palavras = [p.casefold() for p in busca.texto.split()]
        repetidas = [
            a for a, b in zip(palavras, palavras[1:]) if a == b
        ]
        assert not repetidas, f"{busca.texto!r} repete {repetidas}"


# ------------------------------------------------------- degrau seguinte
def test_the_plan_also_covers_the_level_above(filters):
    """Quem esta em gestao procura gestao, e tambem o degrau seguinte.

    Olhar so para o nivel inferido fazia um Tech Manager nunca ver uma vaga de
    Head -- que e exatamente o movimento de carreira que ele esta tentando
    fazer. Observado num uso real: 43 buscas geradas, nenhuma de nivel
    executivo, e o usuario perguntando por que "Head SRE" nao aparecia.
    """
    from crivo.pipeline.planner import plan

    perfil = {"headline": "Tech Manager SRE", "competencias": ["AWS"]}
    textos = [q.texto for q in plan(perfil, "manager", filters)]
    assert any("Manager" in t for t in textos)
    assert any("Head" in t for t in textos), "o degrau acima precisa entrar"


def test_the_top_level_has_no_step_above(filters):
    from crivo.pipeline.planner import nivel_acima, plan

    assert nivel_acima("executive") is None
    perfil = {"headline": "Head de Infraestrutura", "competencias": ["AWS"]}
    assert plan(perfil, "executive", filters), "o topo continua produzindo buscas"


def test_an_unknown_level_has_no_step_above(filters):
    from crivo.pipeline.planner import nivel_acima

    assert nivel_acima("inventado") is None


# ------------------------------------------------- buscas do proprio usuario
def test_a_search_written_by_the_user_enters_the_plan(filters):
    """O vocabulario de cargo muda mais rapido que a lista de dominio."""
    from crivo.pipeline.planner import plan

    perfil = {"headline": "Tech Manager SRE", "competencias": ["AWS"]}
    queries = plan(perfil, "manager", filters, extras=("Head of SRE",))
    minhas = [q for q in queries if q.origem == "usuario"]
    assert [q.texto for q in minhas] == ["Head of SRE"]


def test_a_user_search_that_repeats_a_generated_one_is_not_duplicated(filters):
    from crivo.pipeline.planner import plan

    perfil = {"headline": "Tech Manager SRE", "competencias": ["AWS"]}
    gerado = [q.texto for q in plan(perfil, "manager", filters)][0]
    textos = [q.texto for q in plan(perfil, "manager", filters, extras=(gerado,))]
    assert textos.count(gerado) == 1


def test_a_single_word_user_search_is_dropped(filters):
    """Um termo solto devolve o mercado inteiro e gasta uma requisicao."""
    from crivo.pipeline.planner import plan

    perfil = {"headline": "Tech Manager SRE", "competencias": ["AWS"]}
    queries = plan(perfil, "manager", filters, extras=("SRE",))
    assert not [q for q in queries if q.origem == "usuario"]


# -------------------------------------------------- ordem das palavras em pt
def test_portuguese_titles_put_the_role_before_the_axis(filters):
    """"SRE Gerente" nao existe; "Gerente de SRE" existe.

    Ingles justapoe e portugues liga com preposicao. Compondo na ordem inglesa
    o run gerava metade das buscas em portugues torto, e a origem nao casava com
    elas -- o efeito aparecia como resultado magro, nunca como erro.
    """
    from crivo.pipeline.planner import compose

    assert compose("SRE", "Gerente", "pt") == "Gerente de SRE"
    assert compose("Infraestrutura", "Head", "pt") == "Head de Infraestrutura"
    assert compose("Banco de Dados", "Coordenadora", "pt") == (
        "Coordenadora de Banco de Dados"
    )


def test_the_plan_has_no_reversed_portuguese_search(filters):
    perfil = {
        "headline": "Gerente de SRE e Infraestrutura",
        "competencias": ["sre", "infraestrutura", "cloud"],
    }
    torto = ("SRE Gerente", "Infraestrutura Head", "Cloud Diretor")
    textos = [q.texto for q in plan(perfil, "manager", filters)]
    assert not [t for t in textos if t in torto]
    assert "Gerente de SRE" in textos


# ------------------------------------------------------------------- teto
def test_the_limit_never_drops_what_the_user_wrote(filters):
    """O corte come pelo produto cartesiano, nunca pela busca do usuario.

    Cortar justamente o que ele escreveu transformaria o campo de busca em
    enfeite: ele existe porque o usuario sabe do proprio mercado o que o perfil
    nao diz.
    """
    perfil = {
        "headline": "Gerente de SRE",
        "competencias": ["sre", "infraestrutura", "cloud", "devops", "finops"],
    }
    minhas = ("Head of Platform", "Staff SRE")
    queries = plan(perfil, "manager", filters, extras=minhas, limite=5)

    assert len(queries) == 5
    assert set(minhas) <= {q.texto for q in queries}


def test_the_limit_is_stable_between_two_reads(filters):
    perfil = {"headline": "Gerente de SRE", "competencias": ["sre", "cloud"]}
    primeira = [q.texto for q in plan(perfil, "manager", filters, limite=6)]
    segunda = [q.texto for q in plan(perfil, "manager", filters, limite=6)]
    assert primeira == segunda


def test_without_a_limit_nothing_is_cut(filters):
    perfil = {"headline": "Gerente de SRE", "competencias": ["sre", "cloud"]}
    assert len(plan(perfil, "manager", filters)) == len(
        plan(perfil, "manager", filters, limite=None)
    )


# -------------------------------------------------- por onde o teto morde
def _perfil_de_muitos_eixos():
    return {
        "headline": "Gerente de SRE",
        "competencias": [
            "sre", "infraestrutura", "cloud", "devops", "observabilidade",
            "banco de dados", "finops", "plataforma",
        ],
    }


def test_the_limit_keeps_every_axis_instead_of_the_first_few(filters):
    """Cortar por texto e cortar por ordem alfabetica.

    Com teto de vinte e cinco, um perfil de SRE e Infraestrutura ficava com
    Cloud, Database, DevOps e FinOps inteiros e perdia SRE e Infraestrutura por
    completo -- justamente os dois eixos que definem o perfil.
    """
    queries = plan(_perfil_de_muitos_eixos(), "manager", filters, limite=25)
    eixos_no_plano = {q.eixo for q in queries if q.eixo}
    eixos_possiveis = {
        q.eixo for q in plan(_perfil_de_muitos_eixos(), "manager", filters) if q.eixo
    }
    assert eixos_no_plano == eixos_possiveis


def test_the_limit_keeps_both_languages(filters):
    """"en" vem antes de "pt", e so por isso o portugues sumia inteiro.

    Metade das vagas boas no Brasil esta anunciada em ingles e a outra metade em
    portugues: deixar um idioma de fora custa metade do mercado.
    """
    queries = plan(_perfil_de_muitos_eixos(), "manager", filters, limite=25)
    # Nas buscas de eixo, e nao no plano inteiro: as ancoras ja trazem os dois
    # idiomas por si mesmas, e olhar so para o total esconderia o produto
    # cartesiano ter ficado inteiro num idioma so.
    idiomas = {q.idioma for q in queries if q.origem == "eixo"}
    assert idiomas == {"pt", "en"}


def test_the_interleaved_plan_is_stable_between_two_reads(filters):
    perfil = _perfil_de_muitos_eixos()
    primeira = [q.texto for q in plan(perfil, "manager", filters, limite=20)]
    segunda = [q.texto for q in plan(perfil, "manager", filters, limite=20)]
    assert primeira == segunda


def test_anchors_and_user_searches_come_before_the_cartesian_product(filters):
    """O rodizio nao pode empurrar para baixo o que rende mais por consulta."""
    queries = plan(
        _perfil_de_muitos_eixos(), "manager", filters,
        extras=("Head of SRE",), limite=12,
    )
    origens = [q.origem for q in queries]
    assert origens[0] == "usuario"
    assert origens.index("eixo") > origens.index("ancora")


# ------------------------------------- rebaixar o que o historico reprovou
def test_a_term_whose_history_is_all_off_track_is_demoted(filters):
    """Medido no run 1418e378: FinOps Director trouxe 10 vagas, 10 fora.

    Quatro dos 25 termos gastavam requisicao contra o limite de taxa e nao
    devolviam nada aproveitavel.
    """
    from crivo.pipeline.planner import improdutivas

    assert improdutivas({"FinOps Director": (10, 10)}) == {"FinOps Director"}


def test_a_small_sample_is_not_enough_to_condemn_a_term(filters):
    """Um termo bom nao pode ser descartado por azar de um run."""
    from crivo.pipeline.planner import improdutivas

    assert improdutivas({"SRE Manager": (2, 2)}) == frozenset()


def test_a_term_that_sometimes_hits_survives(filters):
    from crivo.pipeline.planner import improdutivas

    assert improdutivas({"Cloud Director": (10, 5)}) == frozenset()


def test_the_demoted_term_leaves_room_for_one_that_works(filters):
    perfil = {
        "headline": "Gerente de SRE",
        "competencias": ["sre", "infraestrutura", "cloud", "finops", "devops"],
    }
    sem = [q.texto for q in plan(perfil, "manager", filters, limite=10)]
    ruim = sem[-1]
    com = [
        q.texto for q in plan(
            perfil, "manager", filters, limite=10, rendimento={ruim: (20, 20)}
        )
    ]
    assert ruim not in com
    assert len(com) == len(sem)


def test_a_search_the_user_wrote_is_never_demoted(filters):
    """Ele sabe do proprio mercado o que o perfil nao diz.

    Desautoriza-lo pelo passado transformaria o campo de busca em sugestao --
    e ele pode estar procurando algo que ainda vai aparecer.
    """
    perfil = {"headline": "Gerente de SRE", "competencias": ["sre", "cloud"]}
    minha = "Head of Reliability"
    queries = plan(
        perfil, "manager", filters, extras=(minha,), limite=5,
        rendimento={minha: (30, 30)},
    )
    assert minha in [q.texto for q in queries]


def test_without_history_nothing_is_demoted(filters):
    """Estado de toda instalacao nova."""
    perfil = {"headline": "Gerente de SRE", "competencias": ["sre", "cloud"]}
    assert (
        [q.texto for q in plan(perfil, "manager", filters, limite=8)]
        == [q.texto for q in plan(
            perfil, "manager", filters, limite=8, rendimento={}
        )]
    )


def test_a_demoted_term_comes_back_when_the_plan_fits_whole(filters):
    """Rebaixar e nao apagar: o mercado muda e o termo volta sozinho.

    Apagado da configuracao, ele so voltaria se alguem lembrasse.
    """
    perfil = {"headline": "Gerente de SRE", "competencias": ["sre"]}
    todas = [q.texto for q in plan(perfil, "manager", filters)]
    com_historico = [
        q.texto for q in plan(
            perfil, "manager", filters, limite=len(todas),
            rendimento={t: (20, 20) for t in todas},
        )
    ]
    assert sorted(com_historico) == sorted(todas)
