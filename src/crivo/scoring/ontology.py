"""Canonicalizacao de competencias por sinonimos.

Sem esta camada, "Aurora PostgreSQL" e "postgres" seriam competencias distintas
e o score perderia casamentos obvios -- que e o modo mais comum de um sistema
destes parecer burro para quem o usa. O ganho nao esta na sofisticacao do
algoritmo, esta em reconhecer que a mesma coisa tem muitos nomes.

O mapa vive em configuracao e cresce com o uso: cada casamento perdido que
aparece num relatorio vira uma linha nova, sem versao de codigo.

A extracao de competencias a partir de texto livre e deliberadamente
conservadora. Ela reconhece termos declarados na ontologia e nao tenta inferir
competencia de prosa, porque um falso positivo aqui infla o score e faz o
usuario perder tempo com vaga que nao serve.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ONTOLOGY_PATH = Path("config") / "ontology.toml"


class OntologyError(Exception):
    """Arquivo de ontologia ausente ou ilegivel."""


def normalize(texto: str | None) -> str:
    """Reduz acentuacao, caixa e espacos, para comparar formas equivalentes."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


@dataclass(frozen=True)
class Ontology:
    """Mapa de formas para termos canonicos."""

    #: forma normalizada -> termo canonico
    formas: dict[str, str]
    #: termos canonicos cuja ausencia inviabiliza a candidatura
    eliminatorios: frozenset[str]
    #: termo eliminatorio -> campo do perfil que carrega a evidencia dele.
    #: Termo cujo campo esta vazio nao gera teto: ausencia de evidencia nao e
    #: evidencia de ausencia. Vazio mantem o comportamento anterior.
    campo_do_eliminatorio: dict[str, str] = field(default_factory=dict)

    @property
    def canonicos(self) -> frozenset[str]:
        return frozenset(self.formas.values())

    def canonical(self, termo: str) -> str:
        """Termo canonico de uma forma, ou a propria forma normalizada."""
        normalizado = normalize(termo)
        return self.formas.get(normalizado, normalizado)

    def canonical_set(self, termos) -> frozenset[str]:
        """Reduz uma colecao de competencias ao conjunto canonico."""
        return frozenset(
            self.canonical(t) for t in (termos or []) if normalize(t)
        )

    def extract_obrigatorias(self, texto: str | None) -> frozenset[str]:
        """Competencias que o anuncio marca como imprescindiveis.

        A eliminatoriedade vem do texto, e nao de uma lista fixa. Uma lista
        fechada so acerta o que alguem previu -- a ontologia trazia apenas
        `ingles`, e por isso um "Azure e imprescindivel" passava batido numa
        vaga que o candidato nao pode aceitar.

        O marcador governa a frase em que aparece, e alcanca a seguinte apenas
        quando a propria nao cita competencia nenhuma -- o caso do cabecalho,
        "Requisitos obrigatorios:" seguido da lista. Sem esse limite, um
        "imprescindivel Azure" no comeco tornava obrigatorio tudo o que viesse
        na frase seguinte, inclusive o que o anuncio citava de passagem.
        """
        alvo = normalize(texto)
        if not alvo:
            return frozenset()
        frases = [f.strip() for f in SEPARADOR.split(alvo) if f.strip()]
        obrigatorias: set[str] = set()
        for indice, frase in enumerate(frases):
            if not any(marca in frase for marca in MARCADORES_OBRIGATORIOS):
                continue
            propria = self.extract(frase)
            if propria:
                obrigatorias |= propria
            elif indice + 1 < len(frases):
                obrigatorias |= self.extract(frases[indice + 1])
        return frozenset(obrigatorias)

    def extract(self, texto: str | None) -> frozenset[str]:
        """Reconhece competencias declaradas na ontologia dentro de um texto."""
        alvo = normalize(texto)
        if not alvo:
            return frozenset()
        encontrados = set()
        # Formas mais longas primeiro: "sql server" precisa vencer "sql" quando
        # as duas casariam no mesmo trecho.
        for forma in sorted(self.formas, key=len, reverse=True):
            if _contem_termo(alvo, forma):
                encontrados.add(self.formas[forma])
        return frozenset(encontrados)


#: Onde uma frase termina, para efeito de vizinhanca de um marcador.
SEPARADOR = re.compile(r"[.;\n]")

#: Formas com que um anuncio declara que um requisito nao e negociavel.
MARCADORES_OBRIGATORIOS = (
    "imprescindivel", "indispensavel", "obrigatorio", "obrigatoria",
    "must have", "must-have", "required", "proven experience",
    "e requisito", "sao requisitos", "requisito obrigatorio",
    "nao negociavel", "essencial",
)


def _contem_termo(texto: str, forma: str) -> bool:
    """Casa a forma como palavra inteira, para nao achar 'go' dentro de 'algo'."""
    return re.search(rf"(?<![\w/]){re.escape(forma)}(?![\w/])", texto) is not None


def skills_from_profile(ontology: "Ontology", campos: dict) -> frozenset[str]:
    """Competencias do candidato: as declaradas mais as que o historico evidencia.

    Sem isto, o sistema recomenda ao candidato acrescentar ao perfil algo que o
    curriculo dele ja afirma. Numa simulacao real ele sugeriu adicionar "SRE" a
    quem liderava um time de SRE ha seis anos, porque a palavra estava na
    descricao da experiencia e nao na lista de competencias.

    A extracao continua conservadora: reconhece apenas termos declarados na
    ontologia, e nao infere competencia de prosa.
    """
    declaradas = ontology.canonical_set(campos.get("competencias") or [])
    trechos = [str(campos.get("headline") or "")]
    for experiencia in campos.get("experiencias") or []:
        if isinstance(experiencia, dict):
            trechos.append(str(experiencia.get("titulo") or ""))
            trechos.append(str(experiencia.get("descricao") or ""))
    return declaradas | ontology.extract(" ".join(trechos))


def load_ontology(path: Path | str = DEFAULT_ONTOLOGY_PATH) -> Ontology:
    """Le a ontologia e devolve o mapa pronto para consulta."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise OntologyError(f"ontologia nao encontrada: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise OntologyError(f"ontologia ilegivel em {path}: {exc}") from exc

    sinonimos = raw.get("sinonimos")
    if not isinstance(sinonimos, dict) or not sinonimos:
        raise OntologyError(f"{path}: secao [sinonimos] ausente ou vazia")

    formas: dict[str, str] = {}
    for canonico, variantes in sinonimos.items():
        alvo = normalize(canonico)
        formas[alvo] = alvo
        for variante in variantes or []:
            formas[normalize(variante)] = alvo

    eliminatorios = {
        normalize(t) for t in raw.get("eliminatorios", {}).get("termos", [])
    }
    desconhecidos = eliminatorios - set(formas.values())
    if desconhecidos:
        raise OntologyError(
            f"{path}: eliminatorios citam termos fora da ontologia: "
            f"{sorted(desconhecidos)}"
        )
    campo = {
        normalize(termo): str(nome)
        for termo, nome in (
            raw.get("eliminatorios", {}).get("campo", {}) or {}
        ).items()
    }
    orfaos = set(campo) - eliminatorios
    if orfaos:
        raise OntologyError(
            f"{path}: eliminatorios.campo cita termos que nao sao "
            f"eliminatorios: {sorted(orfaos)}"
        )
    return Ontology(
        formas=formas,
        eliminatorios=frozenset(eliminatorios),
        campo_do_eliminatorio=campo,
    )
