"""Descarte barato, antes da etapa que consome capacidade de coleta.

O pre-filtro roda inteiro antes que qualquer descricao seja buscada. Essa e a
razao de ele existir: reduzir o numero de vagas que chegam a etapa cara. No run
manual que originou este projeto, quarenta cards viraram doze aqui -- setenta por
cento menos requisicoes na parte que dispara bloqueio.

A distincao entre descartar e marcar e deliberada e nao e simetria perdida. Um
titulo fora de escopo elimina o card, porque o candidato nao vai virar Product
Owner. Uma vaga presencial longe permanece com um blocker que nomeia cidade e
distancia, porque a decisao de mudar de cidade pertence ao candidato e nao ao
filtro. Silenciar essa vaga seria decidir por ele.
"""

from __future__ import annotations

import math
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .sources.guest import Card

DEFAULT_CITIES_PATH = Path("config") / "cidades.toml"

REJEITADO_POR_TITULO = "titulo"

#: Marcado, nao descartado: o titulo sugere engenharia de software generica,
#: mas so a descricao diria se e a vaga de plataforma que o candidato quer.
#: Prefixos que dizem de que natureza e cada aviso. O campo `blocker` guarda os
#: dois tipos concatenados, e sem o prefixo quem le nao consegue distinguir --
#: o scorer tratava qualquer blocker como distancia e descontava 25 pontos de
#: uma vaga que estava do lado, so porque o titulo era de outra trilha.
PREFIXO_GEOGRAFIA = "geografia:"
PREFIXO_TRILHA = "trilha:"

BLOCKER_DE_SOFTWARE = (
    f"{PREFIXO_TRILHA} titulo de engenharia de software sem termo de "
    "infraestrutura; confira a descricao"
)

BLOCKER_SEM_TECNOLOGIA = (
    f"{PREFIXO_TRILHA} titulo sem nenhum termo de tecnologia; provavel "
    "casamento por palavra solta na busca"
)

#: Palavras que a busca por nivel usa e que a origem casa sozinhas, trazendo
#: qualquer carreira que tenha o mesmo degrau hierarquico. Nao sao motivo de
#: nada por si -- estao aqui para explicar por que o titulo generico chega.
_PALAVRAS_DE_NIVEL = (
    "coordenador", "coordenadora", "gerente", "diretor", "diretora",
    "head", "lider", "supervisor", "encarregado", "manager", "director",
)


class PrefilterError(Exception):
    """Tabela de cidades ausente ou ilegivel."""


@dataclass(frozen=True)
class Discard:
    """Card descartado, com o motivo que o eliminou."""

    job_id: str
    titulo: str
    empresa: str | None
    motivo: str


@dataclass
class PrefilterResult:
    """Sobreviventes, descartados e as contagens que vao para o run."""

    mantidos: list[Card] = field(default_factory=list)
    blockers: dict[str, str] = field(default_factory=dict)
    descartados: list[Discard] = field(default_factory=list)

    @property
    def antes(self) -> int:
        return len(self.mantidos) + len(self.descartados)

    @property
    def depois(self) -> int:
        return len(self.mantidos)

    @property
    def a_enriquecer(self) -> list[Card]:
        """Sobreviventes que merecem gastar uma requisicao de descricao.

        Nem todo sobrevivente vale o preco da etapa cara, e as duas decisoes
        sao diferentes. Manter no relatorio custa uma linha; buscar a descricao
        custa de 8 a 20 segundos de governador e um item do orcamento diario --
        e no run e49b12fe isso foi 60 de 154 requisicoes gastas em titulos como
        "Supervisor(a) de Turbinas" e "Coordenador Operacoes de Sinistro",
        cerca de treze minutos do run.

        O aviso de software generico NAO entra neste corte: ele existe
        justamente porque a descricao e quem decide, e economizar a requisicao
        dele seria apagar a pergunta em vez de responde-la. O de titulo sem
        marca de tecnologia entra: ali a descricao nao muda a resposta, porque
        nenhuma descricao faz "Gerente financeiro" virar a vaga de um SRE.
        """
        return [
            card for card in self.mantidos
            if BLOCKER_SEM_TECNOLOGIA not in self.blockers.get(card.job_id, "")
        ]


def load_cities(path: Path | str = DEFAULT_CITIES_PATH) -> dict[str, dict]:
    """Le a tabela de coordenadas usada no calculo de distancia."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PrefilterError(f"tabela de cidades nao encontrada: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PrefilterError(f"tabela de cidades ilegivel em {path}: {exc}") from exc
    return raw.get("cidades", {})


def normalize(texto: str | None) -> str:
    """Reduz acentuacao e caixa, para que a busca por cidade seja tolerante."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def haversine_km(a: dict, b: dict) -> float:
    """Distancia em linha reta entre dois pontos, em quilometros."""
    raio = 6371.0
    dlat = math.radians(b["lat"] - a["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(a["lat"]))
        * math.cos(math.radians(b["lat"]))
        * math.sin(dlon / 2) ** 2
    )
    return round(2 * raio * math.asin(math.sqrt(h)), 1)


def find_city(local: str | None, cidades: dict[str, dict]) -> tuple[str, dict] | None:
    """Encontra a cidade citada no texto de localizacao da vaga."""
    alvo = normalize(local)
    if not alvo:
        return None
    # Nomes mais longos primeiro: "sao bernardo do campo" precisa vencer
    # "sao paulo" quando os dois aparecem como substring do mesmo texto.
    for nome in sorted(cidades, key=len, reverse=True):
        if nome in alvo:
            return nome, cidades[nome]
    return None


class Prefilter:
    """Aplica rejeicao por titulo e avaliacao geografica sobre os cards."""

    def __init__(self, filters, config, cidades: dict[str, dict] | None = None) -> None:
        self._padroes = [
            re.compile(p, re.IGNORECASE) for p in filters.rejeicao_titulos
        ]
        self._branda = (
            re.compile(filters.rejeicao_branda_padrao, re.IGNORECASE)
            if filters.rejeicao_branda_padrao
            else None
        )
        self._excecao = (
            re.compile(filters.rejeicao_branda_excecao, re.IGNORECASE)
            if filters.rejeicao_branda_excecao
            else None
        )
        self._raio = config.profile.raio_deslocamento_km
        self._cidades = cidades if cidades is not None else load_cities()
        # Vocabulario que reconhece a trilha certa. Os eixos entram junto do
        # que a secao [trilha] declara: quem procura SRE tem "SRE" como marca de
        # tecnologia tanto quanto tem "tecnologia".
        self._marcas_de_trilha = tuple(
            normalize(t) for t in (
                *filters.trilha_genericos,
                *(forma for formas in filters.eixos.values() for forma in formas),
                *filters.eixos_en.values(),
            ) if normalize(t)
        )
        self._siglas_de_trilha = frozenset(
            normalize(s) for s in filters.trilha_siglas if normalize(s)
        )

    def evaluate(self, cards: list[Card], cidade_do_perfil: str | None) -> PrefilterResult:
        """Avalia todos os cards. Nenhuma descricao e buscada aqui."""
        resultado = PrefilterResult()
        origem = self._origem(cidade_do_perfil)

        for card in cards:
            motivo = self._motivo_de_descarte(card.titulo)
            if motivo:
                resultado.descartados.append(
                    Discard(
                        job_id=card.job_id, titulo=card.titulo,
                        empresa=card.empresa, motivo=motivo,
                    )
                )
                continue
            resultado.mantidos.append(card)
            avisos = [
                aviso for aviso in (
                    self._blocker(card, origem),
                    self._aviso_de_software(card.titulo),
                    self._aviso_de_outra_trilha(card.titulo),
                )
                if aviso
            ]
            if avisos:
                resultado.blockers[card.job_id] = "; ".join(avisos)
        return resultado

    # ------------------------------------------------------------ titulo
    def _motivo_de_descarte(self, titulo: str) -> str | None:
        for padrao in self._padroes:
            achado = padrao.search(titulo)
            if achado:
                return f"{REJEITADO_POR_TITULO}: {achado.group(0)}"
        return None

    def _aviso_de_software(self, titulo: str) -> str | None:
        """Marca engenharia de software generica, em vez de elimina-la."""
        if not (self._branda and self._branda.search(titulo)):
            return None
        if self._excecao and self._excecao.search(titulo):
            return None
        return BLOCKER_DE_SOFTWARE

    def _aviso_de_outra_trilha(self, titulo: str) -> str | None:
        """Marca titulo sem nenhuma marca de tecnologia.

        Regra invertida em relacao a `[rejeicao]`, e a inversao e o ponto. Uma
        lista de negacao cresce um caso por vez e perde para a variacao
        seguinte: ela ja tinha "(gerente|supervisor|coordenador|tecnico) de
        manutencao" e ainda assim deixou passar "LIDER DE MANUTENCAO ELETRICA",
        que pontuou 76% num run real. Reconhecer o que serve e finito;
        enumerar o que nao serve nao e.

        Marca em vez de descartar, pelo mesmo motivo que a vaga presencial
        distante e marcada: o titulo sozinho pode enganar nos dois sentidos, e a
        descricao -- que o pre-filtro ainda nao tem -- e quem decide. O teto de
        nota tira do topo sem esconder.

        Sem vocabulario configurado a regra nao opina. Um arquivo de filtros
        anterior a secao `[trilha]` faria toda vaga parecer fora da trilha, o
        que e pior do que nao ter a regra.
        """
        if not self._marcas_de_trilha and not self._siglas_de_trilha:
            return None
        alvo = normalize(titulo)
        if not alvo:
            return None
        if any(marca in alvo for marca in self._marcas_de_trilha):
            return None
        # Sigla precisa casar palavra inteira: "ti" como pedaco esta dentro de
        # "gestao", "otimizacao" e "logistica".
        palavras = set(re.findall(r"[a-z0-9]+", alvo))
        if palavras & self._siglas_de_trilha:
            return None
        return BLOCKER_SEM_TECNOLOGIA

    # ---------------------------------------------------------- geografia
    def _origem(self, cidade_do_perfil: str | None):
        encontrada = find_city(cidade_do_perfil, self._cidades)
        return encontrada[1] if encontrada else None

    def _blocker(self, card: Card, origem) -> str | None:
        if card.remoto:
            # Vaga remota nunca e avaliada geograficamente.
            return None
        return blocker_geografico(card.local, origem, self._cidades, self._raio)


def blocker_geografico(local, origem, cidades: dict, raio: int) -> str | None:
    """Aviso de deslocamento de uma vaga presencial, ou `None` se nao ha.

    Fora da classe porque a passada final precisa do mesmo calculo: a vaga que
    se anuncia remota e exige escritorio so se revela quando a descricao chega,
    e ai o pre-filtro ja passou. Duas implementacoes da mesma distancia
    discordariam sobre a mesma vaga, que e o defeito de §1.7 outra vez.
    """
    if origem is None:
        return None
    destino = find_city(local, cidades)
    if destino is None:
        if not normalize(local):
            return None
        return (
            f"{PREFIXO_GEOGRAFIA} presencial em {local}, distancia "
            "desconhecida; cidade fora da tabela de referencia"
        )
    _nome, coordenadas = destino
    distancia = haversine_km(origem, coordenadas)
    if distancia <= raio:
        return None
    return (
        f"{PREFIXO_GEOGRAFIA} presencial em {local}, cerca de "
        f"{distancia:.0f} km acima do raio de {raio} km"
    )
