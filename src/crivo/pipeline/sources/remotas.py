"""Fontes de vaga remota que dizem quem pode ser contratado.

Elas existem porque o LinkedIn sem login nao responde a pergunta que importa a
quem mora no Brasil e quer receber em moeda forte. A busca dele ignora o filtro
de remoto -- tres frentes internacionais trouxeram 267 vagas presenciais contra
1 remota em 14/10/2026 -- e, mesmo quando a vaga e remota, nao diz de onde a
empresa contrata.

As fontes aqui foram escolhidas por medicao, e nao por fama. Em 16/10/2026, uma
chamada a cada uma, contando vagas do perfil (gestao, SRE, plataforma, infra)
que aceitam contratar do Brasil:

    Remote OK ........... 99 vagas, 27 do perfil,  1 aceita o Brasil
    Himalayas ........... 20 vagas,  9 do perfil,  0 aceitam o Brasil
    Remotive (gratis) ... 15 vagas no total, sempre as mesmas, nenhuma do perfil
    Jobicy (geo=latam) .. 50 vagas, 14 do perfil, 10 aceitam o Brasil
    We Work Remotely .... 22 vagas, 10 do perfil, 10 aceitam o Brasil

Entram as duas ultimas. As demais trazem vaga remota restrita aos EUA, que e o
mesmo problema das frentes do LinkedIn com outra roupa.

Tres coisas separam estas fontes da coleta do LinkedIn, e as tres sao a razao de
existirem num modulo proprio:

- Uma chamada por fonte, e nao uma por termo. Os termos de uso pedem pouca
  frequencia, e o conjunto devolvido ja e pequeno: o filtro de relevancia e o
  pre-filtro do crivo, aplicado depois, igual para todas as origens.
- A descricao vem junto. Ela e gravada no registro na coleta, e o enriquecedor
  a reaproveita sem requisicao -- estas vagas nao passam pela etapa lenta.
- A elegibilidade e decidida aqui. Vaga remota que nao contrata do Brasil e
  descartada na fonte, ao contrario da vaga presencial distante, que o
  pre-filtro so marca. A diferenca e de natureza: mudar de cidade e decisao de
  quem procura; a folha de pagamento de outro pais, nao.

Os dois provedores pedem credito com link para a vaga original. O titulo no
relatorio ja e esse link, e a origem aparece como "achada por Jobicy" ou "achada
por We Work Remotely" no proprio cartao.
"""

from __future__ import annotations

import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from .guest import REMOTO, Card, SearchOutcome
from .guest import _sem_marcacao as sem_marcacao

logger = logging.getLogger(__name__)

#: Marcas, no texto de "quem pode ser contratado", que incluem quem mora no
#: Brasil. Comparadas em minusculas e por pedaco: "Anywhere in the World",
#: "LATAM, Canada, USA" e "Americas" entram; "USA Only" e "Europe" nao.
ACEITA_O_BRASIL = (
    "anywhere", "worldwide", "global", "latam", "latin america", "americas",
    "south america", "brazil", "brasil",
)

#: Uma requisicao lenta nao pode segurar o run: a fonte e opcional, e falha nela
#: vira contribuicao vazia, nunca run parado.
PRAZO_S = 20

USER_AGENT = "crivo (triagem pessoal de vagas; https://github.com/rodsgobbo/crivo)"


def aceita_o_brasil(onde: str | None) -> bool:
    """A restricao de contratacao inclui quem mora no Brasil?

    Texto vazio responde `True`: as duas fontes so publicam vaga remota, e a
    ausencia de restricao declarada e o caso de "qualquer lugar".
    """
    texto = (onde or "").strip().lower()
    if not texto:
        return True
    return any(marca in texto for marca in ACEITA_O_BRASIL)


@dataclass
class ColetaRemota:
    """O que uma fonte remota trouxe: cards, descricoes e o que foi recusado."""

    fonte: str
    cards: list[Card] = field(default_factory=list)
    descricoes: dict[str, str] = field(default_factory=dict)
    inelegiveis: int = 0
    falha: str | None = None
    #: Veio do cache dentro do intervalo, e nao de uma chamada nova.
    do_cache: bool = False

    def como_busca(self) -> SearchOutcome:
        """O mesmo formato de uma busca, para a coleta absorver igual."""
        if self.falha:
            return SearchOutcome(busca=self.fonte, falha=self.falha)
        if not self.cards:
            return SearchOutcome(busca=self.fonte, improdutiva=True)
        return SearchOutcome(busca=self.fonte, cards=list(self.cards))


class FonteRemota:
    """Base das fontes: uma chamada, e o resultado convertido em cards."""

    nome = ""
    rotulo = ""

    def __init__(self, http: Callable[..., Any] | None = None) -> None:
        self._http = http

    def coletar(self) -> ColetaRemota:
        try:
            resposta = self._get(self.endereco(), self.parametros())
            vagas = self.interpretar(resposta)
        except Exception as exc:
            logger.warning("fonte remota %s falhou: %s", self.rotulo, exc)
            return ColetaRemota(fonte=self.rotulo, falha=f"{type(exc).__name__}: {exc}")

        coleta = ColetaRemota(fonte=self.rotulo)
        for card, descricao, onde in vagas:
            if not aceita_o_brasil(onde):
                coleta.inelegiveis += 1
                continue
            coleta.cards.append(card)
            if descricao:
                coleta.descricoes[card.job_id] = descricao
        logger.info(
            "fonte remota %s: %s vaga(s) que contratam do Brasil, %s fora",
            self.rotulo, len(coleta.cards), coleta.inelegiveis,
        )
        return coleta

    # ------------------------------------------------------------ por fonte
    def endereco(self) -> str:  # pragma: no cover - abstrato
        raise NotImplementedError

    def parametros(self) -> dict[str, Any]:
        return {}

    def interpretar(self, resposta) -> list[tuple[Card, str, str]]:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------ interno
    def _get(self, url: str, params: dict[str, Any]):
        if self._http is not None:
            return self._http(url, params)
        import httpx  # pragma: no cover - exige rede

        resposta = httpx.get(  # pragma: no cover - exige rede
            url, params=params, timeout=PRAZO_S, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        resposta.raise_for_status()  # pragma: no cover - exige rede
        return resposta  # pragma: no cover - exige rede


class Jobicy(FonteRemota):
    """API publica do Jobicy, ja filtrada no servidor por quem contrata da LATAM.

    `geo=latam` e o que faz esta fonte servir: a elegibilidade e decidida do lado
    deles, e a verificacao local so confirma. Medido em 16/10/2026: das 14 vagas
    do perfil em 50, 10 contratavam de "Anywhere" ou "LATAM".
    """

    nome = "jobicy"
    rotulo = "Jobicy"

    def endereco(self) -> str:
        return "https://jobicy.com/api/v2/remote-jobs"

    def parametros(self) -> dict[str, Any]:
        return {"count": 50, "geo": "latam"}

    def interpretar(self, resposta) -> list[tuple[Card, str, str]]:
        dados = resposta.json() if hasattr(resposta, "json") else resposta
        vagas = []
        for item in dados.get("jobs") or []:
            identificador = str(item.get("id") or "").strip()
            titulo = sem_marcacao(item.get("jobTitle") or "")
            url = (item.get("url") or "").strip()
            if not identificador or not titulo or not url:
                continue
            onde = sem_marcacao(item.get("jobGeo") or "")
            card = Card(
                job_id=f"jy-{identificador}",
                titulo=titulo,
                empresa=sem_marcacao(item.get("companyName") or "") or None,
                url=url,
                local=onde or "Anywhere",
                modelo_trabalho=REMOTO,
                publicada_em=_data_iso(item.get("pubDate")),
                salario=_salario_jobicy(item),
                busca=self.rotulo,
            )
            descricao = sem_marcacao(item.get("jobDescription") or item.get("jobExcerpt") or "")
            vagas.append((card, descricao, onde))
        return vagas


class WeWorkRemotely(FonteRemota):
    """Feed RSS de uma categoria do We Work Remotely.

    RSS existe para ser consumido, e o `robots.txt` deles nao o restringe. A
    categoria DevOps/Sysadmin e a do perfil: medido em 16/10/2026, 10 das 22
    vagas eram do perfil e todas contratavam de "Anywhere in the World".
    """

    nome = "weworkremotely"
    rotulo = "We Work Remotely"
    categoria = "remote-devops-sysadmin-jobs"

    def endereco(self) -> str:
        return f"https://weworkremotely.com/categories/{self.categoria}.rss"

    def interpretar(self, resposta) -> list[tuple[Card, str, str]]:
        texto = resposta.text if hasattr(resposta, "text") else resposta
        vagas = []
        for item in ET.fromstring(texto).iter("item"):
            url = (item.findtext("link") or item.findtext("guid") or "").strip()
            bruto = sem_marcacao(item.findtext("title") or "")
            if not url or not bruto:
                continue
            # O titulo do feed vem como "Empresa: Cargo".
            empresa, separador, titulo = bruto.partition(": ")
            if not separador:
                empresa, titulo = "", bruto
            onde = sem_marcacao(item.findtext("region") or "")
            card = Card(
                # O feed nao tem identificador numerico; o endereco da vaga e
                # unico e estavel, e o resumo dele vira o identificador.
                job_id="wwr-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
                titulo=titulo.strip(),
                empresa=empresa.strip() or None,
                url=url,
                local=onde or "Anywhere in the World",
                modelo_trabalho=REMOTO,
                publicada_em=_data_rss(item.findtext("pubDate")),
                busca=self.rotulo,
            )
            vagas.append((card, sem_marcacao(item.findtext("description") or ""), onde))
        return vagas


#: Registro das fontes conhecidas, pelo nome usado na configuracao.
FONTES = {fonte.nome: fonte for fonte in (Jobicy, WeWorkRemotely)}


# --------------------------------------------------------------- cadencia
class CadenciaRemota:
    """Uma chamada por fonte a cada N horas, com o resultado guardado entre elas.

    Os provedores pedem pouca frequencia, e o conteudo deles nao muda mais rapido
    que isso. O cache e o que permite a segunda busca do dia ainda mostrar as
    vagas remotas no relatorio, sem uma chamada nova.

    Vive em `schema_meta`, sob `fonte_remota:<nome>`: o valor e a coleta
    serializada, e `aplicada_em` e o instante da chamada.
    """

    def __init__(self, connection, intervalo_horas: int) -> None:
        self._connection = connection
        self._intervalo = timedelta(hours=intervalo_horas)

    def coletar(self, fonte: FonteRemota, agora: datetime | None = None) -> ColetaRemota:
        agora = agora or datetime.now(timezone.utc)
        chave = f"fonte_remota:{fonte.nome}"
        linha = self._connection.execute(
            "SELECT valor, aplicada_em FROM schema_meta WHERE chave = ?", (chave,)
        ).fetchone()
        if linha is not None:
            quando = datetime.fromisoformat(linha[1])
            if quando.tzinfo is None:
                quando = quando.replace(tzinfo=timezone.utc)
            if agora - quando < self._intervalo:
                coleta = _desserializar(fonte.rotulo, linha[0])
                coleta.do_cache = True
                return coleta

        coleta = fonte.coletar()
        # Falha nao entra no cache: senao uma queda de minutos esconderia a
        # fonte por horas.
        if coleta.falha is None:
            self._connection.execute(
                "INSERT INTO schema_meta (chave, valor, aplicada_em) VALUES (?, ?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
                "aplicada_em = excluded.aplicada_em",
                (chave, _serializar(coleta), agora.isoformat(timespec="seconds")),
            )
            self._connection.commit()
        return coleta


def _serializar(coleta: ColetaRemota) -> str:
    return json.dumps(
        {
            "cards": [asdict(c) for c in coleta.cards],
            "descricoes": coleta.descricoes,
            "inelegiveis": coleta.inelegiveis,
        },
        ensure_ascii=False,
    )


def _desserializar(rotulo: str, valor: str) -> ColetaRemota:
    dados = json.loads(valor)
    cards = []
    for bruto in dados.get("cards") or []:
        bruto["flags"] = tuple(bruto.get("flags") or ())
        cards.append(Card(**bruto))
    return ColetaRemota(
        fonte=rotulo, cards=cards,
        descricoes=dict(dados.get("descricoes") or {}),
        inelegiveis=int(dados.get("inelegiveis") or 0),
    )


# --------------------------------------------------------------- formatos
def _data_iso(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    return texto[:10] if len(texto) >= 10 else None


def _data_rss(valor: str | None) -> str | None:
    try:
        return parsedate_to_datetime(valor).date().isoformat() if valor else None
    except (TypeError, ValueError):
        return None


def _salario_jobicy(item: dict) -> str | None:
    minimo, maximo = item.get("annualSalaryMin"), item.get("annualSalaryMax")
    if not minimo and not maximo:
        return None
    moeda = str(item.get("salaryCurrency") or "").strip()
    faixa = "-".join(str(v) for v in (minimo, maximo) if v)
    return f"{moeda} {faixa} por ano".strip()
