"""Releitura das melhores vagas por modelo de linguagem.

O que se verifica aqui e o contrato: o que acontece sem credencial, o que
acontece com resposta ilegivel, e -- sobretudo -- que a ordem deterministica
sobrevive aos dois casos. A releitura e uma camada por cima, e uma camada que
derruba o que esta embaixo quando falha nao seria camada, seria substituicao.
"""

import pytest

from crivo.config import load_config
from crivo.providers.client import Completion, DeterministicFallback, ModelError
from crivo.scoring.judge import Judge, interpretar, montar_pedido

PERFIL = {
    "headline": "Engineering Manager · SRE",
    "nivel_inferido": "manager",
    "competencias": ["kubernetes", "terraform", "aws"],
}


def vagas(n=3):
    return [
        {
            "job_id": f"li-{i}", "titulo": f"Vaga {i}", "empresa": "Fintech",
            "descricao": "Buscamos Kubernetes." if i else None,
        }
        for i in range(n)
    ]


class ClienteFalso:
    def __init__(self, texto="li-1 | 80 | casa com o historico", erro=None):
        self.texto = texto
        self.erro = erro
        self.pedidos = []

    def assert_owner(self, user_id):
        pass

    def complete(self, task, payload, untrusted=True):
        self.pedidos.append((task, payload))
        if self.erro:
            raise self.erro
        return Completion(
            texto=self.texto, provedor="google-ai-studio", modelo="gemini",
            tokens_entrada=100, tokens_saida=20,
        )


# ------------------------------------------------------------------ leitura
def test_a_well_formed_answer_becomes_scores():
    notas, motivos = interpretar(
        "li-1 | 80 | infra de verdade\nli-2 | 10 | banco de talentos",
        {"li-1", "li-2"},
    )
    assert notas == {"li-1": 80, "li-2": 10}
    assert motivos["li-2"] == "banco de talentos"


def test_an_invented_job_is_ignored():
    """Citar vaga que nao veio no pedido e a alucinacao mais comum aqui.

    Aceitar uma delas faria a releitura pontuar uma vaga que nao existe, e ela
    apareceria no topo do relatorio sem que nada a tivesse coletado.
    """
    notas, _ = interpretar("li-9 | 90 | inventada\nli-1 | 50 | real", {"li-1"})
    assert notas == {"li-1": 50}


def test_prose_around_the_lines_is_skipped():
    """Modelo que explica antes de responder nao pode derrubar a leitura."""
    notas, _ = interpretar(
        "Claro! Aqui esta minha analise:\n\nli-1 | 70 | boa\n\nEspero ter ajudado.",
        {"li-1"},
    )
    assert notas == {"li-1": 70}


def test_a_score_outside_the_range_is_refused():
    notas, _ = interpretar("li-1 | 900 | absurda\nli-2 | 40 | ok", {"li-1", "li-2"})
    assert notas == {"li-2": 40}


# ------------------------------------------------------------------ pedido
def test_the_request_names_the_profile_and_every_job():
    corpo = montar_pedido(PERFIL, vagas(3), 8000)
    assert "Engineering Manager" in corpo
    for i in range(3):
        assert f"li-{i}" in corpo


def test_the_job_id_carries_no_brackets():
    """O modelo copia o formato da listagem.

    Com `[li-x]` na lista ele respondia `[li-x] - 20/100 - ...`, e o colchete
    era dele imitando o pedido.
    """
    corpo = montar_pedido(PERFIL, vagas(2), 8000)
    assert "[li-0]" not in corpo


def test_a_job_without_a_description_says_so_instead_of_omitting():
    """Omitir faria o modelo julgar por titulo achando que leu a descricao."""
    corpo = montar_pedido(PERFIL, vagas(1), 500)
    assert "NAO COLETADA" in corpo


def test_the_whole_request_fits_the_budget():
    """O cliente corta o corpo pelo fim, sem avisar, e o fim tem instrucao.

    A primeira versao mandava o orcamento inteiro de descricao POR VAGA. Vinte
    vagas depois o corpo era cortado em oito mil caracteres, o modelo recebia
    uma lista truncada sem nenhuma instrucao e respondia uma linha so.
    """
    longas = [
        {"job_id": f"li-{i}", "titulo": "Engenheiro", "empresa": "Fintech",
         "descricao": "a" * 9000}
        for i in range(20)
    ]
    assert len(montar_pedido(PERFIL, longas, 8000)) <= 8000


def test_every_job_gets_the_same_slice_of_description():
    """A ultima vaga da lista e julgada com a mesma evidencia que a primeira.

    Cortar pelo fim daria descricao inteira as primeiras e nenhuma as ultimas,
    e o julgamento passaria a depender da posicao.
    """
    longas = [
        {"job_id": f"li-{i}", "titulo": "X", "empresa": "Y",
         "descricao": ("%d" % i) * 4000}
        for i in range(10)
    ]
    corpo = montar_pedido(PERFIL, longas, 8000)
    tamanhos = [
        len(linha) for linha in corpo.splitlines()
        if linha.startswith("  descricao: ")
    ]
    assert len(set(tamanhos)) == 1


def test_a_list_too_long_for_any_description_says_so(): 
    """Omitir a vaga seria pior: ela sumiria do julgamento sem ninguem saber."""
    muitas = [
        {"job_id": f"li-{i}", "titulo": "Engenheiro de Plataforma",
         "empresa": "Fintech", "descricao": "texto"}
        for i in range(40)
    ]
    corpo = montar_pedido(PERFIL, muitas, 2000)
    assert "NAO ENVIADA" in corpo
    assert corpo.count("li-") >= 40


# --------------------------------------------------------------- execucao
def test_without_a_model_nothing_happens_and_the_run_survives():
    """Sem credencial a ordem deterministica fica de pe, e o run nao falha."""
    resultado = Judge(None, load_config()).judge("ana", PERFIL, vagas())
    assert not resultado.disponivel
    assert "deterministico" in resultado.falha


def test_an_exhausted_chain_is_a_recorded_failure_not_a_crash():
    cliente = ClienteFalso(erro=DeterministicFallback("sem credencial"))
    resultado = Judge(cliente, load_config()).judge("ana", PERFIL, vagas())
    assert not resultado.disponivel
    assert "cadeia esgotada" in resultado.falha


def test_a_model_error_is_a_recorded_failure_not_a_crash():
    cliente = ClienteFalso(erro=ModelError("timeout"))
    resultado = Judge(cliente, load_config()).judge("ana", PERFIL, vagas())
    assert not resultado.disponivel
    assert "falha de modelo" in resultado.falha


def test_an_unreadable_answer_does_not_invent_an_order():
    """Sem nota reconhecivel, a ordem deterministica continua valendo.

    Uma releitura que devolvesse ordem arbitraria por nao ter entendido a
    resposta seria pior que nao reler: o usuario nao teria como saber.
    """
    cliente = ClienteFalso(texto="Nao consegui avaliar essas vagas.")
    resultado = Judge(cliente, load_config()).judge("ana", PERFIL, vagas())
    assert not resultado.disponivel
    assert "sem nenhuma linha reconhecivel" in resultado.falha
    # A proveniencia fica registrada mesmo na falha: a chamada foi paga.
    assert resultado.modelo == "gemini"


def test_only_the_top_reaches_the_model():
    """Cada vaga a mais custa contexto sem mudar o que o usuario faz."""
    cliente = ClienteFalso()
    Judge(cliente, load_config()).judge("ana", PERFIL, vagas(60))
    _tarefa, corpo = cliente.pedidos[0]
    assert len([l for l in corpo.splitlines() if l.startswith("li-")]) == 20


def test_the_request_uses_the_judgement_task_and_not_the_synthesis_one():
    """Cada tarefa tem gabarito proprio; reusar o da sintese pediria prosa."""
    cliente = ClienteFalso()
    Judge(cliente, load_config()).judge("ana", PERFIL, vagas())
    assert cliente.pedidos[0][0] == "julgamento_de_vagas"


def test_the_judgement_task_has_a_system_prompt():
    from crivo.providers.client import build_system

    gabarito = build_system("julgamento_de_vagas")
    assert "vago" in gabarito.lower()
    assert "banco de talentos" in gabarito.lower()


# ------------------------------- o que o modelo real devolveu, e nao o ideal
def test_the_shape_a_real_model_actually_returned_is_read():
    """Resposta observada do gemini-3.6-flash em 26/ago.

    Colchete copiado da listagem, travessao no lugar da barra e denominador na
    nota. Recusar isso descartaria uma resposta correta pela pontuacao dela.
    """
    notas, motivos = interpretar(
        "[li-4459137087] - 20/100 - Vaga de lideranca em Ciencia de Dados e IA",
        {"li-4459137087"},
    )
    assert notas == {"li-4459137087": 20}
    assert "Ciencia de Dados" in motivos["li-4459137087"]


def test_a_markdown_table_row_is_read():
    notas, _ = interpretar("| li-1 | 75 | plataforma e SRE |", {"li-1"})
    assert notas == {"li-1": 75}


def test_a_bulleted_line_is_read():
    notas, _ = interpretar("- li-1: 60: cargo adjacente", {"li-1"})
    assert notas == {"li-1": 60}


def test_the_identifier_is_never_loosened():
    """O formato se afrouxa; a identidade da vaga nao.

    Aceitar um identificador aproximado faria a releitura pontuar uma vaga que
    nao e a que foi julgada.
    """
    notas, _ = interpretar("[li-999] - 90/100 - inventada", {"li-1"})
    assert notas == {}
