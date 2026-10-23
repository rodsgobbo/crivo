"""Rascunho de pedido de indicacao, sob demanda.

O relatorio recomenda "pedir indicacao antes" quando a fila e longa ou quando o
anuncio marca como imprescindivel algo que falta -- e parava ai. Escrever a
mensagem era o passo seguinte, e o que mais trava quem procura vaga.

Tres decisoes, todas a favor de quem envia:

- Sob demanda, por clique: nada e gerado num run, e nenhum token e gasto em
  vaga que o usuario nao escolheu.
- O crivo nunca envia. Ele mostra o texto para ser copiado e editado.
- So fatos do perfil confirmado. O backlog previa perguntar uma vez pelos
  numeros reais das entregas; em vez disso, o gabarito proibe inventar, e a
  resposta e CONFERIDA: todo numero do rascunho que nao esta no perfil nem na
  vaga e apontado na pagina. Modelo que inventa "reduzi custos em 40%" nao
  passa calado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..providers.client import DeterministicFallback, ModelError

TAREFA = "pedido_de_indicacao"

#: Teto de palavras do rascunho, igual ao do gabarito. Passou disso, a pagina
#: avisa em vez de cortar no meio de uma frase.
LIMITE_DE_PALAVRAS = 150

#: Quanto da descricao vai junto. O suficiente para o modelo saber do que a vaga
#: trata; a mensagem nao precisa da descricao inteira.
TRECHO_DA_DESCRICAO = 2500

_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass
class Rascunho:
    texto: str = ""
    provedor: str | None = None
    modelo: str | None = None
    #: Numeros do rascunho que nao aparecem no perfil nem na vaga.
    numeros_sem_origem: list[str] = field(default_factory=list)
    palavras: int = 0
    falha: str | None = None

    @property
    def longo_demais(self) -> bool:
        return self.palavras > LIMITE_DE_PALAVRAS


def montar_pedido(perfil: dict, vaga: dict) -> str:
    """O corpo enviado ao modelo: o perfil confirmado e a vaga."""
    experiencias = []
    for exp in (perfil.get("experiencias") or [])[:6]:
        if not isinstance(exp, dict):
            continue
        periodo = f"{exp.get('inicio') or '?'} a {exp.get('fim') or 'atual'}"
        linha = f"- {exp.get('titulo') or ''} na {exp.get('empresa') or ''} ({periodo})"
        descricao = (exp.get("descricao") or "").strip()
        if descricao:
            linha += f": {descricao[:600]}"
        experiencias.append(linha)
    return "\n".join([
        "## PERFIL",
        f"Nome: {perfil.get('nome') or 'nao informado'}",
        f"Cargo atual: {perfil.get('headline') or 'nao informado'}",
        "Experiencias:",
        *(experiencias or ["- nao informadas"]),
        "Competencias: " + ", ".join(str(c) for c in (perfil.get("competencias") or [])[:30]),
        "Idiomas: " + (", ".join(perfil.get("idiomas") or []) or "nao informados"),
        "",
        "## VAGA",
        f"Titulo: {vaga.get('titulo') or ''}",
        f"Empresa: {vaga.get('empresa') or 'nao informada'}",
        "Descricao: " + ((vaga.get("descricao") or "nao coletada")[:TRECHO_DA_DESCRICAO]),
    ])


def numeros_sem_origem(texto: str, *fontes: str) -> list[str]:
    """Numeros do rascunho que nenhuma das fontes contem.

    Comparados sem pontuacao: "257 mil" do perfil e "257" do rascunho casam, e
    "1.196" e "1196" tambem. Centavos nao contam: "R$ 1.196,00" e "1196" sao o
    mesmo valor, e apontar a diferenca seria alarme falso.
    """
    def formas(n: str) -> set[str]:
        sem_centavos = re.sub(r"[.,]\d{2}$", "", n)
        return {re.sub(r"[.,]", "", n), re.sub(r"[.,]", "", sem_centavos)}

    conhecidos = set()
    for fonte in fontes:
        for n in _NUMERO.findall(fonte or ""):
            conhecidos |= formas(n)
    vistos = []
    for numero in _NUMERO.findall(texto or ""):
        if not (formas(numero) & conhecidos) and numero not in vistos:
            vistos.append(numero)
    return vistos


def rascunhar(client, user_id: str, perfil: dict, vaga: dict) -> Rascunho:
    """Pede o rascunho ao modelo do usuario e confere os numeros dele."""
    if client is None:
        return Rascunho(falha="sem credencial de modelo cadastrada")
    client.assert_owner(user_id)
    pedido = montar_pedido(perfil, vaga)
    try:
        resposta = client.complete(TAREFA, pedido)
    except (DeterministicFallback, ModelError) as exc:
        return Rascunho(falha=str(exc))
    texto = (resposta.texto or "").strip()
    return Rascunho(
        texto=texto,
        provedor=resposta.provedor,
        modelo=resposta.modelo,
        numeros_sem_origem=numeros_sem_origem(texto, pedido),
        palavras=len(texto.split()),
    )
