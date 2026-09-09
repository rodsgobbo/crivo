"""Cofre em memoria dos segredos do operador.

Segredos vivem exclusivamente em variaveis de ambiente. Este modulo e o unico
ponto do sistema autorizado a le-los, e nunca consulta o arquivo de
configuracao: um segredo que aparecesse em `config/*.toml` seria versionado
junto com o codigo, que e exatamente o que o requisito proibe.

Todo valor entregue por este cofre e registrado para redacao, de modo que o
filtro de log tenha a lista completa do que precisa apagar sem que cada
chamador precise lembrar de informa-la.

O carregamento do arquivo `.env` vive aqui pelo mesmo motivo. Ler variavel de
ambiente e o unico contrato do cofre, mas alguem precisa colocar os valores no
ambiente, e enquanto ninguem fazia isso o produto nao subia: o README mandava
copiar `.env.example` para `.env`, o arquivo era criado e nenhuma linha de
codigo o abria. Todo criterio sobre segredo estava satisfeito -- eles vem do
ambiente -- e nenhum dizia como chegam la.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: Variaveis reconhecidas. `True` marca o valor como sensivel a redacao.
KNOWN_SECRETS: dict[str, bool] = {
    "CRIVO_MASTER_KEY": True,
    "CRIVO_DATABASE_URL": True,
    "GOOGLE_CLIENT_ID": False,
    "GOOGLE_CLIENT_SECRET": True,
    "LINKEDIN_CLIENT_ID": False,
    "LINKEDIN_CLIENT_SECRET": True,
}

#: Comprimento minimo para um valor entrar na lista de redacao. Valores muito
#: curtos produziriam substituicoes espurias em texto de log nao relacionado.
MIN_REDACTABLE_LENGTH = 6


def load_env_file(caminho: str | Path = ".env", environ=None) -> list[str]:
    """Carrega `.env` no ambiente e devolve os nomes que passou a definir.

    Variavel ja presente no ambiente sempre vence a do arquivo. Em producao os
    valores vem do orquestrador, e um `.env` esquecido no disco nao pode
    sobrescrever o que foi injetado deliberadamente.

    Ausencia do arquivo nao e erro: quem exporta as variaveis na mao nao
    precisa dele. O erro por segredo faltando continua sendo do cofre, que e
    quem sabe o que cada modo exige.
    """
    destino = os.environ if environ is None else environ
    arquivo = Path(caminho)
    if not arquivo.is_file():
        return []

    definidos = []
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        nome, valor = _par(linha)
        if nome is None or nome in destino:
            continue
        destino[nome] = valor
        definidos.append(nome)
    return definidos


def _par(linha: str) -> tuple[str | None, str]:
    """Interpreta uma linha do arquivo, ou devolve `None` quando nao ha par."""
    limpa = linha.strip()
    if not limpa or limpa.startswith("#"):
        return None, ""
    if limpa.startswith("export "):
        limpa = limpa[len("export "):].lstrip()
    if "=" not in limpa:
        return None, ""
    nome, _, valor = limpa.partition("=")
    nome = nome.strip()
    if not nome:
        return None, ""
    valor = valor.strip()
    # Aspas delimitam o valor e nao fazem parte dele; sem isto, uma chave
    # entre aspas entraria no ambiente com as aspas e falharia na decodificacao
    # bem longe daqui.
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
        valor = valor[1:-1]
    return nome, valor


class SecretError(Exception):
    """Segredo obrigatorio ausente ou vazio no ambiente."""


class SecretsVault:
    """Le segredos do ambiente e mantem os valores sensiveis para redacao."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._sensitive: set[str] = set()

    def require(self, name: str) -> str:
        """Devolve o segredo, levantando `SecretError` quando ausente ou vazio."""
        value = self._environ.get(name)
        if value is None or not value.strip():
            raise SecretError(
                f"{name}: variavel de ambiente ausente ou vazia, "
                "esperado valor nao vazio"
            )
        self._remember(name, value)
        return value

    def optional(self, name: str, default: str | None = None) -> str | None:
        """Devolve o segredo quando presente, ou `default` quando ausente."""
        value = self._environ.get(name)
        if value is None or not value.strip():
            return default
        self._remember(name, value)
        return value

    def _remember(self, name: str, value: str) -> None:
        sensitive = KNOWN_SECRETS.get(name, True)
        if sensitive and len(value) >= MIN_REDACTABLE_LENGTH:
            self._sensitive.add(value)

    @property
    def sensitive_values(self) -> frozenset[str]:
        """Valores ja lidos que o filtro de log deve apagar."""
        return frozenset(self._sensitive)

    def missing(self, names: list[str]) -> list[str]:
        """Nomes que estao ausentes ou vazios no ambiente, na ordem informada."""
        return [
            name
            for name in names
            if not (self._environ.get(name) or "").strip()
        ]
