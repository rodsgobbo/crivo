"""Geracao deterministica das buscas a partir do perfil-alvo.

Nao ha chamada de modelo aqui, e a ausencia dela e requisito de produto e nao
economia. Dois runs do mesmo perfil com a mesma configuracao precisam produzir a
mesma lista, para que a diferenca de resultado entre dois dias seja atribuivel
ao mercado e nao ao sistema. Uma lista gerada por modelo variaria sozinha e
tornaria impossivel dizer se o mercado mudou ou se o gerador teve outro dia.

Busca de um unico termo e descartada na saida. O casamento da origem e difuso, e
um termo solto como "SRE" traz desde Chief Operating Officer ate Engenheiro de
Estruturas. Duas a quatro palavras combinando funcao e nivel tem precisao
muito maior.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_FILTERS_PATH = Path("config") / "filters.toml"

#: Minimo de termos numa busca aceita.
MINIMO_DE_TERMOS = 2


class PlannerError(Exception):
    """Arquivo de filtros ausente, ilegivel ou incompleto."""


@dataclass(frozen=True)
class Query:
    """Uma busca a executar, com a razao de ela existir."""

    texto: str
    idioma: str
    origem: str
    #: Eixo de funcao que gerou esta busca, quando ela veio do produto
    #: cartesiano. E o que permite ao corte tirar uma de cada eixo em vez de
    #: esgotar os primeiros em ordem alfabetica.
    eixo: str | None = None

    @property
    def termos(self) -> int:
        return len(self.texto.split())


@dataclass(frozen=True)
class Filters:
    """Vocabulario de dominio carregado da configuracao."""

    eixos: dict[str, tuple[str, ...]]
    eixos_en: dict[str, str]
    niveis: dict[str, tuple[str, ...]]
    niveis_en: dict[str, tuple[str, ...]]
    ancoras: dict[str, tuple[str, ...]]
    rejeicao_titulos: tuple[str, ...]
    rejeicao_branda_padrao: str
    rejeicao_branda_excecao: str
    #: Vocabulario que reconhece a trilha certa num titulo, usado pelo
    #: pre-filtro. Vazio e valido e desliga a regra: arquivo de configuracao
    #: escrito antes desta secao continua valendo.
    trilha_genericos: tuple[str, ...] = ()
    trilha_siglas: tuple[str, ...] = ()


def load_filters(path: Path | str = DEFAULT_FILTERS_PATH) -> Filters:
    """Le o vocabulario de dominio usado pelo planejador e pelo pre-filtro."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw: dict[str, Any] = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PlannerError(f"arquivo de filtros nao encontrado: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PlannerError(f"arquivo de filtros ilegivel em {path}: {exc}") from exc

    for secao in ("eixos", "eixos_en", "niveis", "niveis_en", "ancoras", "rejeicao"):
        if secao not in raw:
            raise PlannerError(f"{path}: secao [{secao}] ausente")
    branda = raw["rejeicao"].get("branda", {})
    trilha = raw.get("trilha", {})
    return Filters(
        eixos={k: tuple(v) for k, v in raw["eixos"].items()},
        eixos_en=dict(raw["eixos_en"]),
        niveis={k: tuple(v) for k, v in raw["niveis"].items()},
        niveis_en={k: tuple(v) for k, v in raw["niveis_en"].items()},
        ancoras={k: tuple(v) for k, v in raw["ancoras"].items()},
        rejeicao_titulos=tuple(raw["rejeicao"].get("titulos", ())),
        rejeicao_branda_padrao=branda.get("padrao", ""),
        rejeicao_branda_excecao=branda.get("excecao", ""),
        trilha_genericos=tuple(trilha.get("genericos", ())),
        trilha_siglas=tuple(trilha.get("siglas", ())),
    )


def detect_axes(profile_fields: dict, filters: Filters) -> list[str]:
    """Descobre os eixos de funcao presentes no historico do candidato."""
    fragmentos: list[str] = []
    if profile_fields.get("headline"):
        fragmentos.append(str(profile_fields["headline"]))
    for experiencia in profile_fields.get("experiencias") or []:
        if isinstance(experiencia, dict):
            fragmentos.append(str(experiencia.get("titulo") or ""))
            fragmentos.append(str(experiencia.get("descricao") or ""))
    for competencia in profile_fields.get("competencias") or []:
        fragmentos.append(str(competencia))
    texto = " ".join(fragmentos).lower()

    encontrados = [
        eixo
        for eixo, formas in filters.eixos.items()
        if any(forma.lower() in texto for forma in formas)
    ]
    # Ordenacao estavel por nome do eixo: a ordem de deteccao dependeria da
    # ordem das experiencias, e duas leituras do mesmo perfil precisam coincidir.
    return sorted(encontrados)


def compose(eixo: str, rotulo: str, idioma: str = "en") -> str:
    """Junta eixo e rotulo sem repetir a palavra da emenda.

    Um eixo pode terminar com a mesma palavra com que o rotulo comeca --
    "Platform Engineering" mais "Engineering Manager". Concatenar direto produzia
    "Platform Engineering Engineering Manager", que nao existe como cargo. A
    origem ainda devolvia resultado, entao a consulta malformada nao aparecia
    como erro em lugar nenhum: so aparecia lida por um humano.

    A sobreposicao e removida por palavra e nao por caractere, para que
    "Cloud" e "Cloud Manager" continuem virando "Cloud Manager" sem que
    "Data" e "Database Manager" percam a letra que os distingue.

    Em portugues a ordem se inverte, e isso nao e detalhe de estilo. Ingles
    justapoe -- "SRE Manager" -- e portugues liga com preposicao e poe o cargo
    na frente: "Gerente de SRE". Compondo na ordem inglesa saiam "SRE Gerente",
    "Infraestrutura Head" e "Banco de Dados Coordenadora", que ninguem escreve
    num anuncio e a origem por isso nao casa. Metade das buscas do run era
    disso, e o efeito so aparecia como resultado magro -- nunca como erro.
    """
    if idioma == "pt":
        return f"{rotulo} de {eixo}"
    da_esquerda = eixo.split()
    da_direita = rotulo.split()
    maior = min(len(da_esquerda), len(da_direita))
    for tamanho in range(maior, 0, -1):
        fim = [p.casefold() for p in da_esquerda[-tamanho:]]
        inicio = [p.casefold() for p in da_direita[:tamanho]]
        if fim == inicio:
            da_direita = da_direita[tamanho:]
            break
    return " ".join(da_esquerda + da_direita)


#: Escala de senioridade, do menor para o maior. Duplicada da inferencia de
#: perfil de proposito: o planejador precisa saber o que vem acima de um nivel,
#: e importar `profile` aqui inverteria a dependencia -- e o perfil que alimenta
#: a busca, e nao o contrario.
ESCALA = ("pleno", "senior", "lead", "manager", "executive")


def nivel_acima(nivel: str) -> str | None:
    """O degrau seguinte da carreira, ou `None` no topo."""
    try:
        posicao = ESCALA.index(nivel)
    except ValueError:
        return None
    return ESCALA[posicao + 1] if posicao + 1 < len(ESCALA) else None


#: Prioridade de cada origem quando o plano precisa ser cortado.
#:
#: O que o usuario escreveu nunca cai: ele sabe do proprio mercado o que o
#: perfil nao diz, e cortar justamente isso transformaria o campo de busca em
#: enfeite. Depois vem a ancora, que e frase de cargo que o mercado usa de fato;
#: por ultimo o produto cartesiano de eixo por rotulo, que e o que rende menos
#: por consulta e o que mais infla.
PRIORIDADE_DA_ORIGEM = {"usuario": 0, "ancora": 1, "eixo": 2}


#: Quantas vagas um termo precisa ter trazido antes de ser julgado improdutivo.
#: Abaixo disso a amostra e pequena demais, e um termo bom passaria a ser
#: descartado por azar de um run.
MINIMO_PARA_JULGAR = 5

#: Proporcao de vagas fora da trilha a partir da qual o termo e rebaixado.
#: Nao e 1.0 porque um acerto isolado em vinte tentativas nao redime a consulta,
#: e nao e mais baixo porque termo com alguma pontaria ainda rende.
PROPORCAO_IMPRODUTIVA = 0.9


def improdutivas(rendimento: dict[str, tuple[int, int]]) -> frozenset[str]:
    """Termos cujo historico so trouxe vaga de outra trilha.

    `rendimento` mapeia o texto da busca para (vagas trazidas, vagas fora da
    trilha), somando todos os runs do usuario.

    Rebaixar em vez de apagar da configuracao: o mercado muda, e um termo que
    nao rende hoje pode render em tres meses. Rebaixado, ele volta sozinho
    assim que a lista couber inteira; apagado, so voltaria se alguem lembrasse.
    """
    return frozenset(
        termo
        for termo, (total, fora) in rendimento.items()
        if total >= MINIMO_PARA_JULGAR and fora / total >= PROPORCAO_IMPRODUTIVA
    )


def plan(
    profile_fields: dict,
    nivel: str,
    filters: Filters,
    extras: tuple[str, ...] = (),
    limite: int | None = None,
    rendimento: dict[str, tuple[int, int]] | None = None,
) -> list[Query]:
    """Devolve a lista de buscas do run, ordenada de forma estavel.

    A busca cobre o nivel inferido e o imediatamente acima. Quem esta em gestao
    procura gestao, mas tambem procura o degrau seguinte -- e olhar so para o
    proprio nivel fazia um Tech Manager nunca ver uma vaga de Head, que e
    exatamente o movimento de carreira que ele esta tentando fazer. Dois niveis
    e o limite: o terceiro traz cargo que ninguem alcanca de uma vez.

    `extras` sao buscas escritas pelo proprio usuario. Elas existem porque o
    vocabulario de cargo muda mais rapido que qualquer lista de dominio, e
    porque quem procura sabe coisas sobre o proprio mercado que o perfil nao
    diz. Entram como qualquer outra busca, e a origem fica registrada.

    `limite` corta o plano, e existe porque o produto cartesiano cresce como
    produto cartesiano. Um perfil com onze eixos detectados gerava noventa
    buscas, cada uma valendo uma a tres requisicoes a origem: perto de duzentas
    requisicoes em rajada, que e exatamente o que o governador de taxa foi
    escrito para nao deixar acontecer. Buscar mais nao e achar mais quando a
    origem para de responder no meio.
    """
    eixos = detect_axes(profile_fields, filters)
    buscas: dict[str, Query] = {}

    for alvo in (nivel, nivel_acima(nivel)):
        if alvo is None:
            continue
        rotulos_pt = filters.niveis.get(alvo, ())
        rotulos_en = filters.niveis_en.get(alvo, ())
        # Cada idioma combina o proprio eixo com os proprios rotulos. Cruzar os
        # dois produzia frases que nao existem como cargo.
        for eixo in eixos:
            for rotulo in rotulos_pt:
                _add(buscas, compose(eixo, rotulo, "pt"), "pt", "eixo", eixo)
            eixo_en = filters.eixos_en.get(eixo, eixo)
            for rotulo in rotulos_en:
                _add(buscas, compose(eixo_en, rotulo, "en"), "en", "eixo", eixo)

        for ancora in filters.ancoras.get(alvo, ()):
            _add(buscas, ancora, _idioma(ancora), "ancora")

    for extra in extras:
        _add(buscas, extra, _idioma(extra), "usuario")

    # Chave textual e nao ordem de insercao: a saida precisa ser identica entre
    # execucoes, e a ordem de insercao depende de detalhes do dicionario.
    ordenadas = sorted(buscas.values(), key=_ordem)
    if not limite:
        return ordenadas
    return _rebaixar(
        _intercalar_por_eixo(ordenadas), improdutivas(rendimento or {})
    )[:limite]


def _rebaixar(ordenadas: list[Query], ruins: frozenset[str]) -> list[Query]:
    """Manda para o fim os termos que o historico mostrou improdutivos.

    Medido no run `1418e378`: `FinOps Director` trouxe dez vagas e as dez eram
    de outra trilha; `Coordenador de FinOps` trouxe cinco e as cinco tambem.
    Quatro dos vinte e cinco termos gastavam requisicao contra o limite de taxa
    e nao devolviam nada aproveitavel.

    O que o usuario escreveu nunca e rebaixado, mesmo com historico ruim. Ele
    sabe do proprio mercado o que o perfil nao diz, e pode estar procurando algo
    que ainda vai aparecer -- desautoriza-lo pelo passado transformaria o campo
    de busca em sugestao.
    """
    if not ruins:
        return ordenadas
    bons = [q for q in ordenadas if q.origem == "usuario" or q.texto not in ruins]
    rebaixados = [
        q for q in ordenadas if q.origem != "usuario" and q.texto in ruins
    ]
    return bons + rebaixados


def _ordem(q: Query) -> tuple:
    return (
        PRIORIDADE_DA_ORIGEM.get(q.origem, len(PRIORIDADE_DA_ORIGEM)),
        q.idioma,
        q.texto,
    )


def _rodizio(itens: list[Query], chave) -> list[Query]:
    """Reordena para que um corte tire um de cada grupo, e nao um grupo inteiro.

    Os grupos entram na ordem em que aparecem, e cada um cede um item por volta.
    A lista resultante e determinista porque a entrada ja vem ordenada.
    """
    grupos: dict = {}
    for item in itens:
        grupos.setdefault(chave(item), []).append(item)
    filas = list(grupos.values())
    saida: list[Query] = []
    while any(filas):
        for fila in filas:
            if fila:
                saida.append(fila.pop(0))
    return saida


def _intercalar_por_eixo(ordenadas: list[Query]) -> list[Query]:
    """Reordena para que o corte tire uma busca de cada eixo, e nao um eixo.

    Cortar a lista ordenada por texto e cortar por ordem alfabetica, e ordem
    alfabetica nao tem relacao nenhuma com o que o perfil e. Com teto de vinte e
    cinco, um perfil de SRE e Infraestrutura ficava com Cloud, Database, DevOps
    e FinOps inteiros e perdia SRE e Infraestrutura por completo -- os dois
    eixos que definem o perfil. Pelo mesmo motivo perdia o portugues inteiro,
    porque "en" vem antes de "pt". Nao era o teto que estava errado; era a ordem
    em que ele mordia.

    O rodizio e duplo: entre eixos, e entre idiomas dentro de cada eixo. Metade
    das vagas boas no Brasil esta anunciada em ingles e a outra metade em
    portugues, entao deixar um idioma de fora custa metade do mercado.
    """
    por_eixo: dict[str | None, list[Query]] = {}
    for q in ordenadas:
        por_eixo.setdefault(q.eixo, []).append(q)

    # O que nao tem eixo -- busca do usuario e ancora -- nao entra no rodizio:
    # ja esta no topo por prioridade de origem e nao e o que infla a lista.
    sem_eixo = por_eixo.pop(None, [])
    com_eixo = [_rodizio(fila, lambda q: q.idioma) for fila in por_eixo.values()]

    saida = list(sem_eixo)
    while any(com_eixo):
        for fila in com_eixo:
            if fila:
                saida.append(fila.pop(0))
    return saida


def _add(
    destino: dict, texto: str, idioma: str, origem: str, eixo: str | None = None
) -> None:
    limpo = " ".join(texto.split())
    if len(limpo.split()) < MINIMO_DE_TERMOS:
        return
    chave = limpo.lower()
    if chave not in destino:
        destino[chave] = Query(
            texto=limpo, idioma=idioma, origem=origem, eixo=eixo
        )


_MARCAS_PT = (" de ", " da ", " do ", " e ")


def _idioma(texto: str) -> str:
    return "pt" if any(marca in f" {texto.lower()} " for marca in _MARCAS_PT) else "en"


def as_list(queries: list[Query]) -> list[str]:
    """Forma serializavel, para gravacao no registro do run."""
    return [q.texto for q in queries]
