"""Registro dos provedores de modelo habilitados pelo operador.

A lista vem de configuracao e nao de codigo. Isso nao e preferencia de estilo:
oferta gratuita muda de limite e desaparece em prazo curto, e uma lista fixa no
codigo nasceria desatualizada e exigiria uma versao nova a cada mudanca de
mercado.

A regra que sustenta o modelo de credencial por usuario tambem mora aqui. O
registro aceita apenas provedor cujo formato de credencial declarado seja chave
estatica ou ausencia de credencial. Provedor que exigisse registrar uma
aplicacao junto a ele e recusado na carga, porque o registro de aplicacao e
trabalho do operador e anularia a premissa de que cada usuario traz a sua chave.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROVIDERS_PATH = Path("config") / "providers.toml"

#: Formatos de credencial compativeis com credencial trazida pelo usuario.
FORMATOS_ACEITOS = ("chave_estatica", "nenhuma")

#: Onde o provedor executa.
EXECUCOES_ACEITAS = ("rede_externa", "maquina_do_operador")

_CAMPOS_OBRIGATORIOS = (
    "id", "rotulo", "endereco", "formato_credencial", "execucao",
    "modelos", "limite_declarado", "destino_dos_dados",
)


class ProviderConfigError(Exception):
    """Entrada de provedor invalida ou incompativel com o modelo de credencial."""


@dataclass(frozen=True)
class Provider:
    """Um provedor habilitado, com o que o usuario precisa saber para escolher."""

    id: str
    rotulo: str
    endereco: str
    formato_credencial: str
    execucao: str
    modelos: tuple[str, ...]
    limite_declarado: str
    destino_dos_dados: str

    @property
    def exige_credencial(self) -> bool:
        return self.formato_credencial == "chave_estatica"

    @property
    def roda_localmente(self) -> bool:
        return self.execucao == "maquina_do_operador"


@dataclass(frozen=True)
class ProviderOffer:
    """O que a interface mostra ao usuario sobre um provedor."""

    id: str
    rotulo: str
    exige_credencial: bool
    limite_declarado: str
    destino_dos_dados: str
    modelos: tuple[str, ...]


def load_providers(path: Path | str = DEFAULT_PROVIDERS_PATH) -> list[Provider]:
    """Le e valida o registro. Levanta ao primeiro provedor invalido."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ProviderConfigError(
            f"registro de provedores nao encontrado: {path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProviderConfigError(
            f"registro de provedores ilegivel em {path}: {exc}"
        ) from exc

    entradas = raw.get("provedor")
    if not isinstance(entradas, list) or not entradas:
        raise ProviderConfigError(
            f"{path}: esperada ao menos uma entrada [[provedor]]"
        )
    provedores = [_build(entrada, index) for index, entrada in enumerate(entradas)]
    ids = [p.id for p in provedores]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ProviderConfigError(f"provedores com id repetido: {repetidos}")
    return provedores


def _build(entrada: Any, index: int) -> Provider:
    onde = f"provedor[{index}]"
    if not isinstance(entrada, dict):
        raise ProviderConfigError(f"{onde}: esperada uma tabela")
    faltando = [c for c in _CAMPOS_OBRIGATORIOS if c not in entrada]
    if faltando:
        raise ProviderConfigError(f"{onde}: campos ausentes {faltando}")

    formato = entrada["formato_credencial"]
    if formato not in FORMATOS_ACEITOS:
        raise ProviderConfigError(
            f"{onde} ({entrada['id']}): formato_credencial {formato!r} recusado; "
            f"esperado um de {list(FORMATOS_ACEITOS)}. Provedor que exige "
            "registro de aplicacao nao cabe no modelo de credencial por usuario"
        )
    execucao = entrada["execucao"]
    if execucao not in EXECUCOES_ACEITAS:
        raise ProviderConfigError(
            f"{onde} ({entrada['id']}): execucao {execucao!r}; "
            f"esperado um de {list(EXECUCOES_ACEITAS)}"
        )
    modelos = entrada["modelos"]
    if not isinstance(modelos, list) or not modelos:
        raise ProviderConfigError(
            f"{onde} ({entrada['id']}): esperada lista nao vazia de modelos"
        )
    return Provider(
        id=str(entrada["id"]),
        rotulo=str(entrada["rotulo"]),
        endereco=str(entrada["endereco"]),
        formato_credencial=formato,
        execucao=execucao,
        modelos=tuple(str(m) for m in modelos),
        limite_declarado=str(entrada["limite_declarado"]),
        destino_dos_dados=str(entrada["destino_dos_dados"]),
    )


class ProviderRegistry:
    """Guarda os provedores habilitados e o resultado da ultima verificacao."""

    def __init__(self, providers: Iterable[Provider]) -> None:
        self._providers = {p.id: p for p in providers}
        self._indisponiveis: dict[str, str] = {}

    @property
    def all(self) -> tuple[Provider, ...]:
        return tuple(self._providers.values())

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def check_availability(
        self, probe: Callable[[Provider], bool]
    ) -> dict[str, str]:
        """Testa cada provedor e retira da oferta os que nao respondem.

        Um provedor que nao responde e melhor ausente da lista do que presente e
        falhando depois: o usuario cadastraria uma credencial contra um destino
        que nao esta de pe.
        """
        self._indisponiveis = {}
        for provider in self._providers.values():
            try:
                if not probe(provider):
                    self._indisponiveis[provider.id] = "nao respondeu a verificacao"
            except Exception as exc:
                self._indisponiveis[provider.id] = (
                    f"{type(exc).__name__}: {exc}"
                )
        return dict(self._indisponiveis)

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self._indisponiveis)

    def offers(self) -> list[ProviderOffer]:
        """O que a interface mostra: apenas provedores que responderam."""
        return [
            ProviderOffer(
                id=p.id,
                rotulo=p.rotulo,
                exige_credencial=p.exige_credencial,
                limite_declarado=p.limite_declarado,
                destino_dos_dados=p.destino_dos_dados,
                modelos=p.modelos,
            )
            for p in self._providers.values()
            if p.id not in self._indisponiveis
        ]
