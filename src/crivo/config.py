"""Leitura e validacao por esquema da configuracao do servico.

A configuracao cobre todo parametro que os requisitos descrevem como
configurado. Segredos nao passam por aqui: eles vivem em variaveis de
ambiente e sao lidos por `crivo.secrets`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config") / "default.toml"


class ConfigError(Exception):
    """Configuracao ausente, ilegivel ou fora do contrato declarado."""


@dataclass(frozen=True)
class CollectionConfig:
    janela_incremental_horas: int
    janela_ampla_horas: int
    intervalo_enriquecimento_min_s: int
    intervalo_enriquecimento_max_s: int
    intervalo_recuperacao_s: int
    orcamento_diario_coleta: int
    #: Teto de buscas distintas por run. Anulavel para nao invalidar arquivo de
    #: configuracao anterior a este campo; nulo quer dizer "sem teto", que e o
    #: comportamento antigo.
    max_buscas_por_run: int | None = None


@dataclass(frozen=True)
class RunConfig:
    duracao_maxima_s: int
    runs_imediatos_por_dia: int


@dataclass(frozen=True)
class SessionConfig:
    duracao_maxima_s: int


@dataclass(frozen=True)
class RetentionConfig:
    periodo_sem_sessao_dias: int


@dataclass(frozen=True)
class ResumeConfig:
    tamanho_maximo_bytes: int
    comprimento_minimo_texto: int
    importacoes_por_dia: int
    formatos_aceitos: tuple[str, ...]


@dataclass(frozen=True)
class SynthesisConfig:
    limite_vagas_enviadas: int
    limite_caracteres_texto_externo: int
    modo_deterministico: bool


@dataclass(frozen=True)
class ReportConfig:
    limiar_destaque: int
    vagas_relidas: int
    dias_de_contratacao: int


@dataclass(frozen=True)
class ProfileConfig:
    precedencia_origens: tuple[str, ...]
    raio_deslocamento_km: int


@dataclass(frozen=True)
class ScoringConfig:
    pesos: dict[str, float]
    bonus: dict[str, int]
    penalidades: dict[str, int]


@dataclass(frozen=True)
class ProvidersConfig:
    ordem_padrao: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Config:
    collection: CollectionConfig
    run: RunConfig
    session: SessionConfig
    retention: RetentionConfig
    resume: ResumeConfig
    synthesis: SynthesisConfig
    report: ReportConfig
    profile: ProfileConfig
    scoring: ScoringConfig
    providers: ProvidersConfig


def read_config_file(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Le o arquivo de configuracao e devolve o mapa cru, sem validar."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"arquivo de configuracao nao encontrado: {path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"arquivo de configuracao ilegivel em {path}: {exc}"
        ) from exc


# Esquema declarativo: secao -> chave -> (tipo, minimo, maximo).
# `None` em minimo ou maximo significa ausencia daquele limite.
_SCHEMA: dict[str, dict[str, tuple[type, Any, Any]]] = {
    "collection": {
        "janela_incremental_horas": (int, 1, 8760),
        "janela_ampla_horas": (int, 1, 8760),
        "intervalo_enriquecimento_min_s": (int, 0, 3600),
        "intervalo_enriquecimento_max_s": (int, 0, 3600),
        "intervalo_recuperacao_s": (int, 0, 604800),
        "orcamento_diario_coleta": (int, 0, 100000),
    },
    "run": {
        "duracao_maxima_s": (int, 60, 604800),
        "runs_imediatos_por_dia": (int, 0, 1000),
    },
    "session": {"duracao_maxima_s": (int, 60, 31536000)},
    "retention": {"periodo_sem_sessao_dias": (int, 1, 3650)},
    "resume": {
        "tamanho_maximo_bytes": (int, 1024, 104857600),
        "comprimento_minimo_texto": (int, 1, 100000),
        "importacoes_por_dia": (int, 0, 1000),
        "formatos_aceitos": (list, 1, None),
    },
    "synthesis": {
        "limite_vagas_enviadas": (int, 1, 500),
        "limite_caracteres_texto_externo": (int, 100, 1000000),
        "modo_deterministico": (bool, None, None),
    },
    "report": {
        "limiar_destaque": (int, 0, 100),
        "vagas_relidas": (int, 1, 100),
        "dias_de_contratacao": (int, 1, 365),
    },
    "profile": {
        "precedencia_origens": (list, 1, None),
        "raio_deslocamento_km": (int, 0, 20000),
    },
    "providers": {"ordem_padrao": (list, 0, None)},
}

_SCORING_TABLES: dict[str, type] = {
    "pesos": float,
    "bonus": int,
    "penalidades": int,
}

_ORIGENS_VALIDAS = ("manual", "resume", "linkedin")


def _check(section: str, key: str, value: Any, spec: tuple[type, Any, Any]) -> Any:
    expected, minimum, maximum = spec
    # bool e subclasse de int em Python; sem esta guarda, `true` passaria por int.
    if expected is int and isinstance(value, bool):
        raise ConfigError(
            f"{section}.{key}: esperado inteiro, recebido booleano {value!r}"
        )
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, expected):
        raise ConfigError(
            f"{section}.{key}: esperado {expected.__name__}, "
            f"recebido {type(value).__name__} {value!r}"
        )
    size = len(value) if expected is list else value
    if expected is not bool:
        if minimum is not None and size < minimum:
            unidade = "itens" if expected is list else "valor minimo"
            raise ConfigError(
                f"{section}.{key}: esperado {unidade} >= {minimum}, recebido {size}"
            )
        if maximum is not None and size > maximum:
            unidade = "itens" if expected is list else "valor maximo"
            raise ConfigError(
                f"{section}.{key}: esperado {unidade} <= {maximum}, recebido {size}"
            )
    return value


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        raise ConfigError(f"{name}: secao ausente, esperada uma tabela")
    if not isinstance(value, dict):
        raise ConfigError(f"{name}: esperado tabela, recebido {type(value).__name__}")
    return value


def _validate_flat(raw: dict[str, Any], name: str) -> dict[str, Any]:
    section = _section(raw, name)
    out: dict[str, Any] = {}
    for key, spec in _SCHEMA[name].items():
        if key not in section:
            raise ConfigError(
                f"{name}.{key}: chave ausente, esperado {spec[0].__name__}"
            )
        out[key] = _check(name, key, section[key], spec)
    return out


def _validate_scoring(raw: dict[str, Any]) -> ScoringConfig:
    section = _section(raw, "scoring")
    tables: dict[str, dict[str, Any]] = {}
    for table, kind in _SCORING_TABLES.items():
        values = section.get(table)
        if not isinstance(values, dict) or not values:
            raise ConfigError(
                f"scoring.{table}: esperada tabela nao vazia de {kind.__name__}"
            )
        tables[table] = {
            k: _check(f"scoring.{table}", k, v, (kind, 0, None))
            for k, v in values.items()
        }
    total = sum(tables["pesos"].values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"scoring.pesos: esperada soma 1.0 dos componentes, recebida {total}"
        )
    return ScoringConfig(
        pesos=tables["pesos"], bonus=tables["bonus"], penalidades=tables["penalidades"]
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Le, valida e devolve a configuracao efetiva.

    Levanta `ConfigError` nomeando a chave e o valor esperado ao encontrar
    valor ausente, de tipo errado ou fora da faixa permitida.
    """
    raw = read_config_file(path)

    collection = _validate_flat(raw, "collection")
    # Opcional de proposito: o campo nasceu depois, e um arquivo de
    # configuracao escrito antes dele continua valido. Ausente significa "sem
    # teto", que e como todo run anterior a ele foi planejado.
    if "max_buscas_por_run" in _section(raw, "collection"):
        collection["max_buscas_por_run"] = _check(
            "collection",
            "max_buscas_por_run",
            _section(raw, "collection")["max_buscas_por_run"],
            (int, 1, 1000),
        )
    if (
        collection["intervalo_enriquecimento_min_s"]
        > collection["intervalo_enriquecimento_max_s"]
    ):
        raise ConfigError(
            "collection.intervalo_enriquecimento_min_s: esperado valor menor ou "
            "igual a collection.intervalo_enriquecimento_max_s, recebido "
            f"{collection['intervalo_enriquecimento_min_s']} > "
            f"{collection['intervalo_enriquecimento_max_s']}"
        )

    profile = _validate_flat(raw, "profile")
    desconhecidas = [
        o for o in profile["precedencia_origens"] if o not in _ORIGENS_VALIDAS
    ]
    if desconhecidas:
        raise ConfigError(
            "profile.precedencia_origens: esperado apenas "
            f"{list(_ORIGENS_VALIDAS)}, recebido desconhecido {desconhecidas}"
        )

    resume = _validate_flat(raw, "resume")
    providers = _validate_flat(raw, "providers")

    return Config(
        collection=CollectionConfig(**collection),
        run=RunConfig(**_validate_flat(raw, "run")),
        session=SessionConfig(**_validate_flat(raw, "session")),
        retention=RetentionConfig(**_validate_flat(raw, "retention")),
        resume=ResumeConfig(
            **{**resume, "formatos_aceitos": tuple(resume["formatos_aceitos"])}
        ),
        synthesis=SynthesisConfig(**_validate_flat(raw, "synthesis")),
        report=ReportConfig(**_validate_flat(raw, "report")),
        profile=ProfileConfig(
            **{**profile, "precedencia_origens": tuple(profile["precedencia_origens"])}
        ),
        scoring=_validate_scoring(raw),
        providers=ProvidersConfig(ordem_padrao=tuple(providers["ordem_padrao"])),
    )
