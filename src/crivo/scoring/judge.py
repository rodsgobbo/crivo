"""Releitura das melhores vagas por modelo de linguagem.

O calculo deterministico ordena bem por nivel, geografia e trilha, e erra numa
coisa so -- mas erra caro. O componente de competencias e uma proporcao: quanto
do que a vaga pede o perfil cobre. Anuncio generico pede pouco e o perfil cobre
quase tudo; vaga exigente lista doze tecnologias e o perfil casa seis. Quanto
menos especifico o anuncio, maior a nota.

Medido num run real: um "Banco de Talentos | Tecnologia da Informacao" pontuou
70% e um "ESPECIALISTA DE SRE" pontuou 67%. Nenhum peso conserta isso, porque a
falha nao esta no peso e sim em confundir "cobre tudo o que foi pedido" com
"serve para esta pessoa".

Por isso o modelo entra aqui e nao no lugar do calculo. Ele le apenas o topo --
onde a decisao acontece -- e reordena. O resto do relatorio continua
deterministico, reprodutivel e gratuito.

Sem credencial de modelo nada acontece e a ordem deterministica permanece. Isso
nao e degradacao silenciosa: o run registra que nao houve releitura, e o
relatorio diz de onde veio a ordem que esta mostrando.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..providers.client import DeterministicFallback, ModelError

logger = logging.getLogger(__name__)

#: Quantas vagas o modelo le. O topo e onde a decisao acontece, e cada vaga a
#: mais custa contexto sem mudar o que o usuario faz: ninguem se candidata a
#: quadragesima colocada.
TOPO_PADRAO = 20

#: Nota que o modelo atribui, de 0 a 100, e o motivo em uma linha.
#:
#: Tolerante de proposito. O gabarito pede `id|nota|motivo`, e um modelo real
#: devolveu `[li-123] - 20/100 - motivo`: colchete copiado da listagem,
#: travessao no lugar da barra e denominador na nota. Recusar isso descartaria
#: uma resposta correta por causa da pontuacao dela, e a alternativa -- inventar
#: nota quando nao le -- seria pior. O que nao se afrouxa e o identificador,
#: conferido contra o que foi pedido.
_LINHA = re.compile(
    r"""^\s*
    [-*\[\|\s]*                      # marcador de lista, colchete ou pipe inicial
    (?P<id>[\w-]+)
    \]?                              # colchete de fechamento, se veio
    \s*[|\-–—:]\s*                   # barra, travessao ou dois-pontos
    (?P<nota>\d{1,3})
    (?:\s*/\s*100)?                  # "20/100" e a mesma coisa que "20"
    \s*[|\-–—:]\s*
    (?P<motivo>.+?)
    \s*\|?\s*$                       # pipe final de linha de tabela
    """,
    re.VERBOSE,
)

TAREFA = "julgamento_de_vagas"


@dataclass
class JudgementResult:
    """O que a releitura produziu, ou por que nao produziu."""

    notas: dict[str, int] = field(default_factory=dict)
    motivos: dict[str, str] = field(default_factory=dict)
    provedor: str | None = None
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    falha: str | None = None

    @property
    def disponivel(self) -> bool:
        return bool(self.notas)


#: Piso de descricao por vaga. Abaixo disso o trecho nao diz o suficiente para
#: julgar, e valeria mais mandar nenhuma.
MINIMO_POR_VAGA = 240

#: Espaco reservado ao cabecalho do candidato dentro do orcamento.
ESPACO_DO_PERFIL = 600


def montar_pedido(perfil: dict, vagas: list[dict], orcamento: int) -> str:
    """Monta o corpo enviado ao modelo, dentro do orcamento de caracteres.

    `orcamento` e o teto que o cliente de modelo aplica ao texto de origem antes
    de envia-lo. Ele corta pelo fim, sem avisar.

    O primeiro pedido escrito aqui ignorava isso: mandava ate o orcamento
    inteiro de descricao POR VAGA, e vinte vagas depois o corpo era cortado em
    oito mil caracteres. O que ficava de fora era o fim -- e num pedido de
    modelo o fim e onde moram as instrucoes. O modelo recebia uma lista truncada
    sem nenhuma instrucao, improvisava o formato e respondia uma linha so.

    Por isso o formato da resposta vive no gabarito de sistema, que nao passa
    por truncamento, e o que se corta aqui e apenas descricao -- em fatia igual
    para todas, para que a ultima vaga da lista seja julgada com o mesmo tanto
    de evidencia que a primeira.

    Vaga sem espaco para descricao entra so com titulo e empresa, dito
    explicitamente. Omiti-la seria pior: ela sumiria do julgamento sem que
    ninguem soubesse.
    """
    cabecalho = [
        "## CANDIDATO",
        f"Cargo atual: {perfil.get('headline') or 'nao informado'}",
        f"Nivel: {perfil.get('nivel_inferido') or 'nao informado'}",
        "Competencias: " + ", ".join(
            str(x) for x in (perfil.get("competencias") or [])[:40]
        ),
        "",
        "## VAGAS",
    ]
    por_vaga = _fatia_por_vaga(vagas, orcamento)
    corpo = []
    for vaga in vagas:
        descricao = (vaga.get("descricao") or "").strip()
        corpo.append(
            f"{vaga['job_id']} {vaga.get('titulo') or ''} — "
            f"{vaga.get('empresa') or 'empresa nao informada'}"
        )
        if not descricao:
            corpo.append("  descricao: NAO COLETADA")
        elif por_vaga >= MINIMO_POR_VAGA:
            corpo.append("  descricao: " + descricao[:por_vaga])
        else:
            corpo.append("  descricao: NAO ENVIADA (lista longa demais)")
    return "\n".join(cabecalho + corpo)


def _fatia_por_vaga(vagas: list[dict], orcamento: int) -> int:
    """Quantos caracteres de descricao cabem para cada vaga."""
    if not vagas:
        return 0
    # Cada vaga custa a propria linha de titulo antes de qualquer descricao.
    fixo = sum(
        len(str(v.get("titulo") or "")) + len(str(v.get("empresa") or "")) + 40
        for v in vagas
    )
    livre = max(0, orcamento - ESPACO_DO_PERFIL - fixo)
    return livre // len(vagas)


def interpretar(texto: str, conhecidos: set[str]) -> tuple[dict, dict]:
    """Le a resposta do modelo, ignorando o que nao reconhece.

    Identificador que nao veio no pedido e descartado em silencio: e a forma
    mais comum de o modelo alucinar aqui, e aceitar um deles faria a releitura
    pontuar uma vaga que nao existe.
    """
    notas: dict[str, int] = {}
    motivos: dict[str, str] = {}
    for linha in (texto or "").splitlines():
        achado = _LINHA.match(linha)
        if not achado:
            continue
        job_id = achado.group("id")
        if job_id not in conhecidos:
            logger.info("julgamento citou vaga desconhecida %r; ignorada", job_id)
            continue
        nota = int(achado.group("nota"))
        if not 0 <= nota <= 100:
            continue
        notas[job_id] = nota
        motivos[job_id] = achado.group("motivo")
    return notas, motivos


class Judge:
    """Pede ao modelo uma segunda leitura das melhores vagas."""

    def __init__(self, client, config) -> None:
        self._client = client
        # O mesmo teto que o cliente de modelo aplica ao texto de origem.
        # Montar o pedido maior que isso faz o corte cair nas instrucoes.
        self._orcamento = config.synthesis.limite_caracteres_texto_externo
        self._deterministico = config.synthesis.modo_deterministico
        self._topo = config.report.vagas_relidas

    def judge(self, user_id: str, perfil: dict, vagas: list[dict]) -> JudgementResult:
        """Reavalia o topo. Sem modelo, devolve vazio sem falhar o run."""
        if self._deterministico or self._client is None:
            return JudgementResult(falha="modo deterministico")
        alvo = [v for v in vagas[: self._topo]]
        if not alvo:
            return JudgementResult(falha="nenhuma vaga a reler")

        self._client.assert_owner(user_id)
        corpo = montar_pedido(perfil, alvo, self._orcamento)
        try:
            resposta = self._client.complete(TAREFA, corpo)
        except DeterministicFallback as exc:
            return JudgementResult(falha=f"cadeia esgotada: {exc}")
        except ModelError as exc:
            return JudgementResult(falha=f"falha de modelo: {exc}")

        notas, motivos = interpretar(
            resposta.texto, {v["job_id"] for v in alvo}
        )
        if not notas:
            # Resposta ilegivel nao vira ordem inventada: sem nota, a ordem
            # deterministica fica de pe e o run diz que a releitura falhou.
            return JudgementResult(
                provedor=resposta.provedor, modelo=resposta.modelo,
                tokens_entrada=resposta.tokens_entrada,
                tokens_saida=resposta.tokens_saida,
                falha="resposta sem nenhuma linha reconhecivel",
            )
        return JudgementResult(
            notas=notas, motivos=motivos,
            provedor=resposta.provedor, modelo=resposta.modelo,
            tokens_entrada=resposta.tokens_entrada,
            tokens_saida=resposta.tokens_saida,
        )
