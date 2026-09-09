"""Adaptador do coletor multi-portal para o card normalizado do sistema.

Este e o unico modulo que conhece os tipos da biblioteca de coleta. A saida dela
e tabular; a conversao para o card acontece aqui e nada adiante ve `DataFrame`,
o que mantem a troca por outra fonte como mudanca local.

A chamada de descoberta pede explicitamente para NAO trazer descricao. A
biblioteca oferece traze-la junto, e usar essa opcao seria um erro caro: pela
definicao dos requisitos isso e enriquecer, aconteceria antes do pre-filtro e
pagaria descricao das quarenta vagas coletadas em vez das doze que sobrevivem.
A descricao vem numa segunda chamada, restrita aos sobreviventes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Portal habilitado nesta versao. Os requisitos aprovados definem a camada
#: guest como endpoints publicos do LinkedIn; ligar os demais portais que a
#: biblioteca cobre depende de emenda ao glossario da Fase 1.
PORTAIS = ("linkedin",)

REMOTO = "remote"
PRESENCIAL = "on-site"

#: Endereco publico do anuncio. Nao exige sessao e responde em fracao de segundo.
JOB_POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

CABECALHOS_GUEST = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

#: O corpo do anuncio vive neste bloco; o resto da pagina e navegacao e rodape.
_BLOCO_DESCRICAO = re.compile(
    r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
    re.S,
)
#: A contagem vive na legenda, nao no elemento que a envolve. A primeira versao
#: deste padrao lia o `figure` e capturava o espaco em branco ate o `<span>` do
#: icone, entao devolvia None em toda vaga real. O dublê dos testes trazia o
#: numero direto no `figure` -- marcacao que a origem nunca produziu.
_CANDIDATOS = re.compile(r'num-applicants__caption[^>]*>(.*?)</figcaption>', re.S)

#: "Mais de 200 candidaturas" e um piso de concorrencia. "Seja um dos 25
#: primeiros" nao e contagem alguma: o 25 e o limiar da propria frase, e le-lo
#: como numero de candidatos inverteria o sinal -- diria que a vaga concorrida
#: tem 200 e a vaga vazia tem 25.
_PISO_DE_CANDIDATOS = re.compile(
    r"(?:mais de|over)\s+(\d[\d.,]*)\s*(?:candidatura|candidato|applicant|people)",
    re.I,
)
_PRIMEIROS = re.compile(r"seja um dos \d+ primeiros|be among the first", re.I)
_PUBLICADA = re.compile(r'posted-time-ago__text[^>]*>(.*?)<', re.S)
_CRITERIO = re.compile(
    r'description__job-criteria-subheader[^>]*>(.*?)</h3>.*?'
    r'description__job-criteria-text[^>]*>(.*?)</span>',
    re.S,
)
_NUMERO = re.compile(r"(\d[\d.]*)")

#: Marcas de trabalho remoto no titulo ou na localidade. Existem porque o campo
#: estruturado da origem nao e confiavel: numa coleta real ele devolveu
#: "on-site" para uma vaga chamada "Software Development Manager - Remote".
MARCAS_DE_REMOTO = re.compile(
    r"(?i)\b(remoto?|remote|home[- ]?office|anywhere|100% remoto|teletrabalho)\b"
)


class CollectionError(Exception):
    """A busca falhou por erro de rede ou resposta invalida da origem."""


#: Quantos dias cada unidade da frase de recencia vale. Mes e ano usam a media
#: do calendario: a frase ja e aproximada e fingir precisao maior seria falso.
_DIAS_POR_UNIDADE = (
    (("minuto", "minute", "segundo", "second", "hora", "hour"), 0.0),
    (("dia", "day"), 1.0),
    (("semana", "week"), 7.0),
    (("mes", "month"), 30.44),
    (("ano", "year"), 365.25),
)

_RECENCIA = re.compile(r"(\d+)\s*([a-zA-ZÀ-ſ]+)")


def data_de_publicacao(frase: str | None, hoje: date | None = None) -> str | None:
    """Converte "ha 3 semanas" na data aproximada de publicacao.

    O card da busca chega sem data quando a vaga e recente -- a origem marca o
    anuncio novo com outra classe de marcacao e a biblioteca de coleta procura
    so a antiga. A pagina do anuncio, que o enriquecimento ja busca, diz a
    recencia por extenso, e converte-la fecha o buraco sem requisicao a mais.

    Devolve `None` para o que nao se le. Uma data inventada seria pior que
    nenhuma: e sobre ela que o filtro de janela e o componente de recencia
    decidem, e o relatorio afirmaria ao usuario algo que a origem nao disse.

    "ha 2 horas" vira hoje, e nao `None`: o anuncio afirma que e de hoje, e
    perder isso e perder exatamente a vaga mais fresca.
    """
    texto = normalizar_recencia(frase)
    if not texto:
        return None
    achado = _RECENCIA.search(texto)
    if not achado:
        return None
    quantidade = int(achado.group(1))
    unidade = achado.group(2)
    for formas, dias in _DIAS_POR_UNIDADE:
        if any(unidade.startswith(f) for f in formas):
            referencia = hoje or date.today()
            return str(referencia - timedelta(days=round(quantidade * dias)))
    return None


def normalizar_recencia(frase: str | None) -> str:
    """Minusculas e sem acento, para que "há" e "ha" leiam igual."""
    if not frase:
        return ""
    sem_acento = unicodedata.normalize("NFKD", str(frase))
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


@dataclass(frozen=True)
class Card:
    """Vaga resumida, no formato que o resto do sistema entende."""

    job_id: str
    titulo: str
    empresa: str | None
    url: str
    local: str | None
    modelo_trabalho: str | None
    publicada_em: str | None
    flags: tuple[str, ...] = ()
    salario: str | None = None
    busca: str = ""

    @property
    def remoto(self) -> bool:
        return self.modelo_trabalho == REMOTO


@dataclass
class SearchOutcome:
    """Resultado de uma busca, separando mercado vazio de defeito."""

    busca: str
    cards: list[Card] = field(default_factory=list)
    improdutiva: bool = False
    falha: str | None = None
    #: A origem recusou a requisicao por excesso. Nao e mercado vazio nem
    #: defeito nosso, e chamar de improdutiva -- que era o que acontecia --
    #: fazia o relatorio afirmar que nao havia vagas quando o que houve foi um
    #: portao fechado. As duas coisas exigem resposta oposta: uma calibra o
    #: planejador, a outra manda parar de pedir.
    bloqueada: bool = False


class JobSource(Protocol):
    """Porta de fonte de vagas. Os adaptadores ficam atras dela."""

    def search(self, termo: str, local: str, janela_horas: int, quantidade: int): ...

    def describe(self, job_id: str, url: str) -> dict: ...


class MultiPortalSource:
    """Adaptador da biblioteca de coleta multi-portal."""

    def __init__(
        self, scrape=None, portais: tuple[str, ...] = PORTAIS, http=None
    ) -> None:
        self._scrape = scrape
        self._portais = portais
        self._http = http

    def _scraper(self):
        if self._scrape is None:  # pragma: no cover - exige rede
            from jobspy import scrape_jobs

            self._scrape = scrape_jobs
        return self._scrape

    def search(
        self, termo: str, local: str, janela_horas: int, quantidade: int
    ) -> SearchOutcome:
        """Descoberta: cards sem descricao, deliberadamente."""
        scraper = self._scraper()
        try:
            with _escuta_a_recusa() as recusa:
                tabela = scraper(
                    site_name=list(self._portais),
                    search_term=termo,
                    location=local,
                    hours_old=janela_horas,
                    results_wanted=quantidade,
                    # Nao trazer descricao aqui e a decisao central deste adaptador.
                    linkedin_fetch_description=False,
                )
        except Exception as exc:
            logger.warning("busca %r falhou: %s", termo, exc)
            return SearchOutcome(busca=termo, falha=f"{type(exc).__name__}: {exc}")

        linhas = _to_rows(tabela)
        cards = [c for c in (to_card(linha, termo) for linha in linhas) if c]
        if cards:
            return SearchOutcome(busca=termo, cards=cards)
        # A recusa vence o vazio. A biblioteca engole o 429: ela registra o
        # erro no proprio log e devolve a lista que tinha ate ali, de modo que
        # "fui barrado" e "nao ha vaga nesta janela" chegam aqui com a mesma
        # forma. Sem esta distincao o run seguia pedindo depois de barrado, e o
        # relatorio dizia que o mercado estava vazio.
        if recusa.motivo:
            logger.warning("busca %r barrada pela origem: %s", termo, recusa.motivo)
            return SearchOutcome(busca=termo, bloqueada=True, falha=recusa.motivo)
        return SearchOutcome(busca=termo, improdutiva=True)

    def describe(self, job_id: str, url: str) -> dict:
        """Segunda chamada: descricao de uma vaga que sobreviveu ao pre-filtro.

        Nao usa a biblioteca de coleta. Ela busca por palavra-chave e nao sabe
        buscar uma vaga por identificador -- a primeira versao deste metodo
        passava a URL como termo de busca e por isso nunca devolveu nada. O
        defeito so apareceu contra a origem real, porque o dublê dos testes
        respondia ao contrato e nao ao comportamento da biblioteca.

        O endereco publico de anuncio devolve o fragmento da vaga sem exigir
        sessao, e traz de graca tres coisas que o desenho atribuia a camada
        operacional: a contagem de candidatos, a recencia da publicacao e o
        sinal de baixa concorrencia.
        """
        identificador = job_id.split("-")[-1]
        try:
            resposta = self._http_client().get(
                JOB_POSTING_URL.format(identificador)
            )
        except Exception as exc:
            raise CollectionError(
                f"descricao da vaga {job_id} indisponivel: {type(exc).__name__}: {exc}"
            ) from exc
        if resposta.status_code != 200:
            raise CollectionError(
                f"descricao da vaga {job_id} indisponivel: HTTP "
                f"{resposta.status_code}"
            )
        return parse_job_posting(resposta.text, job_id)

    def _http_client(self):
        if self._http is None:  # pragma: no cover - exige rede
            import httpx

            self._http = httpx.Client(
                timeout=20, headers=CABECALHOS_GUEST, follow_redirects=True
            )
        return self._http


def to_card(linha: dict[str, Any], busca: str = "") -> Card | None:
    """Converte uma linha da biblioteca no card normalizado."""
    url = _texto(linha.get("job_url"))
    titulo = _texto(linha.get("title"))
    if not url or not titulo:
        return None
    return Card(
        job_id=_job_id(linha, url),
        titulo=titulo,
        empresa=_texto(linha.get("company")) or None,
        url=url,
        local=_texto(linha.get("location")) or None,
        modelo_trabalho=_modelo(
            linha.get("is_remote"), f"{titulo} {_texto(linha.get('location'))}"
        ),
        publicada_em=_data(linha.get("date_posted")),
        salario=_salario(linha),
        busca=busca,
    )


# ------------------------------------------------------------------ conversao
def _to_rows(tabela: Any) -> list[dict[str, Any]]:
    """Reduz a saida tabular a dicionarios, sem deixar o tipo dela sair daqui."""
    if tabela is None:
        return []
    if isinstance(tabela, list):
        return [dict(item) for item in tabela]
    to_dict = getattr(tabela, "to_dict", None)
    if to_dict is None:
        return []
    return [_limpar(linha) for linha in to_dict(orient="records")]


def _limpar(linha: dict[str, Any]) -> dict[str, Any]:
    """Troca marcadores de ausencia da biblioteca tabular por `None`."""
    return {k: (None if _ausente(v) else v) for k, v in linha.items()}


def _ausente(valor: Any) -> bool:
    if valor is None:
        return True
    # Valor ausente de tabela numerica nao e igual a si mesmo.
    return valor != valor  # noqa: PLR0124


def _texto(valor: Any) -> str:
    # A checagem de ausencia vive aqui, e nao apenas em `_limpar`, porque
    # `to_card` e chamavel diretamente com uma linha crua. Sem isto, um valor
    # ausente de tabela numerica viraria a string literal "nan" no banco.
    if _ausente(valor):
        return ""
    return str(valor).strip()


def _job_id(linha: dict[str, Any], url: str) -> str:
    identificador = _texto(linha.get("id"))
    if identificador:
        return identificador
    # Sem identificador da origem, a URL canonica serve de identidade estavel.
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _modelo(valor: Any, texto: str = "") -> str | None:
    # O texto vence o campo estruturado quando ele anuncia trabalho remoto: a
    # origem erra para menos nesse campo, nunca para mais.
    if texto and MARCAS_DE_REMOTO.search(texto):
        return REMOTO
    if valor is None:
        return None
    if isinstance(valor, str):
        if valor.strip().lower() in ("true", "1", "sim", "yes"):
            return REMOTO
        if valor.strip().lower() in ("false", "0", "nao", "no"):
            return PRESENCIAL
        return None
    return REMOTO if bool(valor) else PRESENCIAL


def _data(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, date):
        return valor.isoformat()
    texto = _texto(valor)
    return texto[:10] if texto else None


def _emails(valor: Any) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        return sorted({_texto(v) for v in valor if _texto(v)})
    texto = _texto(valor)
    return sorted({p.strip() for p in texto.split(",") if p.strip()}) if texto else []


def _salario(linha: dict[str, Any]) -> str | None:
    minimo, maximo = linha.get("min_amount"), linha.get("max_amount")
    if _ausente(minimo) and _ausente(maximo):
        return None
    moeda = _texto(linha.get("currency")) or ""
    intervalo = _texto(linha.get("interval")) or ""
    partes = [p for p in (_texto(minimo), _texto(maximo)) if p]
    return " ".join(filter(None, [moeda, "-".join(partes), intervalo])).strip() or None



def parse_job_posting(html: str, job_id: str = "") -> dict:
    """Extrai descricao, contatos e sinais do fragmento publico do anuncio."""
    bloco = _BLOCO_DESCRICAO.search(html or "")
    texto = _sem_marcacao(bloco.group(1)) if bloco else ""
    if not texto:
        raise CollectionError(
            f"descricao da vaga {job_id} veio sem corpo; a pagina mudou de forma "
            "ou a origem esta limitando as requisicoes"
        )

    criterios = {
        _sem_marcacao(chave): _sem_marcacao(valor)
        for chave, valor in _CRITERIO.findall(html or "")
    }
    return {
        "texto": texto,
        "emails": sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", texto))),
        "candidatos": _candidatos(html),
        "publicada_ha": _primeiro(_PUBLICADA, html),
        "sinais": _sinais(html),
        "criterios": criterios,
    }


def _sem_marcacao(fragmento: str) -> str:
    import html as _html

    limpo = re.sub(r"<[^>]+>", " ", fragmento or "")
    return re.sub(r"\s+", " ", _html.unescape(limpo)).strip()


def _primeiro(padrao: re.Pattern[str], html: str) -> str | None:
    achado = padrao.search(html or "")
    return _sem_marcacao(achado.group(1)) if achado else None


def _candidatos(html: str) -> int | None:
    """Piso de candidaturas, quando a origem informa um.

    Devolve None tanto quando a legenda falta quanto quando ela diz apenas que
    a vaga tem poucos candidatos: nesse caso nao ha numero a reportar, e o fato
    de haver poucos ja viaja como sinal.
    """
    bruto = _primeiro(_CANDIDATOS, html)
    if not bruto:
        return None
    piso = _PISO_DE_CANDIDATOS.search(bruto)
    if not piso:
        return None
    return int(re.sub(r"[.,]", "", piso.group(1)))


def _sinais(html: str) -> list[str]:
    """Sinais de concorrencia, lidos da mesma legenda que traz a contagem."""
    legenda = _primeiro(_CANDIDATOS, html) or ""
    encontrados = []
    if _PRIMEIROS.search(legenda):
        encontrados.append("early_applicant")
    if _PISO_DE_CANDIDATOS.search(legenda):
        encontrados.append("muitos_candidatos")
    return encontrados


#: Marcas de recusa por excesso no log da biblioteca de coleta. Sao as unicas
#: formas em que ela relata o 429 e o 403: o valor de retorno e uma lista vazia
#: nos dois casos, indistinguivel de mercado sem vaga.
_MARCAS_DE_RECUSA = (
    "429",
    "blocked by",
    "status code 403",
    "too many requests",
)


class _Recusa:
    """O que a biblioteca disse ao ser barrada, se disse algo."""

    def __init__(self) -> None:
        self.motivo: str | None = None


class _Ouvinte(logging.Handler):
    def __init__(self, recusa: _Recusa) -> None:
        super().__init__(level=logging.ERROR)
        self._recusa = recusa

    def emit(self, record: logging.LogRecord) -> None:
        if self._recusa.motivo:
            return
        try:
            texto = record.getMessage()
        except Exception:  # pragma: no cover - formatacao do proprio log
            return
        minuscula = texto.lower()
        if any(marca in minuscula for marca in _MARCAS_DE_RECUSA):
            self._recusa.motivo = texto[:200]


@contextmanager
def _escuta_a_recusa():
    """Escuta o log da biblioteca durante a chamada, e so durante ela.

    A biblioteca cria os proprios registradores com `propagate = False`, entao
    nada do que ela diz chega ao registrador raiz: escutar de fora exige
    pendurar o ouvinte em cada um deles. E o log e mesmo a interface disponivel
    aqui -- o valor de retorno nao carrega a recusa, e o unico outro caminho
    seria remendar a biblioteca.

    Os registradores nascem no import de cada raspador, e por isso o ouvinte e
    pendurado depois de o raspador ja ter sido importado, e nao na partida.
    """
    recusa = _Recusa()
    ouvinte = _Ouvinte(recusa)
    alvos = [
        logging.getLogger(nome)
        for nome in list(logging.root.manager.loggerDict)
        if nome == "JobSpy" or nome.startswith("JobSpy:")
    ]
    for alvo in alvos:
        alvo.addHandler(ouvinte)
    try:
        yield recusa
    finally:
        for alvo in alvos:
            alvo.removeHandler(ouvinte)
