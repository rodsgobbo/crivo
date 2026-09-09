"""Montagem da requisicao de sintese: dados estruturados, nunca marcacao bruta.

O que sobe para o modelo e um recorte ja filtrado e pontuado, e nao o que a
origem devolveu. A diferenca nao e estetica: marcacao bruta gastaria a maior
parte do orcamento de entrada com ruido e traria para dentro da requisicao todo
o conteudo que a pagina carregava, incluindo o que nao tem nada a ver com a vaga.

O recorte tambem e o que mantem o custo previsivel. Sao as vagas de maior score
ate o limite configurado, e nao o run inteiro.

Cada descricao coletada entra truncada e envolvida por delimitador de dado nao
confiavel, o que e responsabilidade do cliente de modelo. Aqui monta-se o corpo;
la aplica-se a contencao.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: Chaves que descrevem uma vaga na requisicao. Lista fechada: o que nao esta
#: aqui nao sobe, e e assim que marcacao bruta fica de fora por construcao.
CAMPOS_DA_VAGA = (
    "id",
    "titulo",
    "empresa",
    "url",
    "local",
    "modelo",
    "publicada_em",
    "score",
    "componentes",
    "sinais",
    "lacunas",
    "diferenciais",
    "blocker",
    "descricao_disponivel",
    "descricao",
)

TAREFA = """## TAREFA

Bloco 1 — VERDADES DESCONFORTAVEIS (3 a 5 itens, no topo)
Diga primeiro o que o candidato nao quer ouvir, baseado nos numeros do contexto.
Classifique cada item como certo, provavel ou suposicao.

Bloco 2 — VAGAS, em ordem decrescente de score
Para cada uma: titulo e empresa; o link EXATO da entrada; local, modelo e data;
por que combina, ancorado em trechos concretos quando houver descricao; a
aderencia informada na entrada, sem recalcular; o que ajustar, derivado das
lacunas; e o trade-off ou blocker, sem suavizar.
Quando descricao_disponivel for falso, diga que a avaliacao foi inferida de
titulo, empresa e sinais, sem leitura da descricao.

Bloco 3 — DESCARTADAS
Uma linha por vaga com o criterio do descarte.

Bloco 4 — COMPETENCIAS MAIS PEDIDAS
Separe em nucleo obrigatorio, diferenciadores e lacunas do candidato.

Bloco 5 — AJUSTES DE POSICIONAMENTO
No maximo 6 itens, por impacto dividido por esforco. Cada um com o que mudar,
por que e qual o custo. Inclua ao menos um que nao seja sobre o perfil.

FORMATO: markdown, sem emoji decorativo, sem introducao. Comece pelo Bloco 1."""


@dataclass(frozen=True)
class SynthesisPayload:
    """Corpo da requisicao e o conjunto que a resposta pode citar."""

    corpo: str
    ids_enviados: frozenset[str]
    urls_enviadas: frozenset[str]
    empresas_enviadas: frozenset[str]
    vagas: int = 0
    descartadas: int = 0


def _vaga_para_envio(vaga: dict, limite_descricao: int) -> dict:
    """Reduz uma vaga aos campos da lista fechada."""
    recorte = {campo: vaga.get(campo) for campo in CAMPOS_DA_VAGA}
    descricao = recorte.get("descricao")
    if descricao:
        recorte["descricao"] = str(descricao)[:limite_descricao]
    recorte["descricao_disponivel"] = bool(descricao)
    return recorte


def build(
    perfil: dict,
    vagas: list[dict],
    descartadas: list[dict],
    contexto: dict,
    limite_vagas: int,
    limite_descricao: int,
) -> SynthesisPayload:
    """Monta o corpo da requisicao com o recorte de maior score."""
    ordenadas = sorted(
        vagas, key=lambda v: (-int(v.get("score") or 0), str(v.get("id") or ""))
    )[:limite_vagas]
    recorte = [_vaga_para_envio(v, limite_descricao) for v in ordenadas]

    corpo = "\n\n".join([
        "## PERFIL",
        _json(perfil),
        "## VAGAS PRE-FILTRADAS E PONTUADAS",
        _json(recorte),
        "## VAGAS DESCARTADAS",
        _json(descartadas),
        "## CONTEXTO",
        _json(contexto),
        TAREFA,
    ])
    return SynthesisPayload(
        corpo=corpo,
        ids_enviados=frozenset(str(v["id"]) for v in recorte if v.get("id")),
        urls_enviadas=frozenset(str(v["url"]) for v in recorte if v.get("url")),
        empresas_enviadas=frozenset(
            str(v["empresa"]) for v in recorte if v.get("empresa")
        ),
        vagas=len(recorte),
        descartadas=len(descartadas),
    )


def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, indent=2, default=str, sort_keys=True)
