"""Markdown do modelo de linguagem para HTML, sem confiar no texto.

A leitura estrategica chega do modelo em Markdown -- titulos, negrito, tabelas
--, e o relatorio a mostrava num `<pre>`, cru: `**Cargo atual**`, `### 1.`,
linhas de `|---|`. Visto num navegador em 23/10/2026, a secao mais cara do
relatorio estava quase ilegivel.

O texto e de terceiro, e pode trazer o que uma injecao de prompt quiser. Por
isso a ordem e a regra inteira: TUDO e escapado primeiro, e so depois um
subconjunto pequeno de Markdown vira marcacao -- titulos, negrito, italico,
listas, tabelas, separador e quebra de linha. Nenhum HTML do modelo atravessa:
um `<script>` vira texto, e um `<br>` escrito por ele vira so quebra de linha.
Sem link, sem imagem: nada que faca o navegador buscar alguma coisa.

O resultado e `Markup`, para o Jinja nao escapar de novo; o gabarito continua
sem `|safe`, que a suite proibe.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

#: `<br>` que o modelo escreve dentro de celula de tabela. Depois do escape ele
#: e texto (`&lt;br&gt;`), e so este padrao o devolve a quebra de linha.
_BR = re.compile(r"&lt;br\s*/?&gt;", re.IGNORECASE)
_NEGRITO = re.compile(r"\*\*(.+?)\*\*")
_ITALICO = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
_TITULO = re.compile(r"^(#{1,6})\s+(.*)$")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_ORDENADO = re.compile(r"^\s*\d+[.)]\s+")
_SEPARADOR_DE_TABELA = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_REGRA = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_CITACAO = re.compile(r"^\s*>\s?(.*)$")


def _em_linha(texto: str) -> str:
    """Escapa e aplica o que vale dentro de uma linha."""
    seguro = str(escape(texto))
    seguro = _BR.sub("<br>", seguro)
    seguro = _NEGRITO.sub(r"<strong>\1</strong>", seguro)
    seguro = _ITALICO.sub(r"<em>\1</em>", seguro)
    return seguro


def _celulas(linha: str) -> list[str]:
    miolo = linha.strip()
    if miolo.startswith("|"):
        miolo = miolo[1:]
    if miolo.endswith("|"):
        miolo = miolo[:-1]
    return [c.strip() for c in miolo.split("|")]


def markdown_seguro(texto: str | None) -> Markup:
    """Converte o subconjunto de Markdown em HTML seguro."""
    if not texto:
        return Markup("")
    linhas = str(texto).replace("\r\n", "\n").split("\n")
    saida: list[str] = []
    paragrafo: list[str] = []
    i = 0

    def fecha_paragrafo() -> None:
        if paragrafo:
            saida.append("<p>" + "<br>".join(paragrafo) + "</p>")
            paragrafo.clear()

    while i < len(linhas):
        linha = linhas[i]
        if not linha.strip():
            fecha_paragrafo()
            i += 1
            continue

        if _REGRA.match(linha):
            fecha_paragrafo()
            saida.append("<hr>")
            i += 1
            continue

        titulo = _TITULO.match(linha.strip())
        if titulo:
            fecha_paragrafo()
            # O relatorio ja usa h1 e h2. "#" vira h3, e "##" e "###" -- os que
            # o modelo mais usa para numerar secoes -- viram h4: descer um nivel
            # por cerquilha deixava "### 1. Resumo" menor que o proprio texto.
            nivel = 3 if len(titulo.group(1)) == 1 else 4 if len(titulo.group(1)) <= 3 else 5
            saida.append(f"<h{nivel}>{_em_linha(titulo.group(2))}</h{nivel}>")
            i += 1
            continue

        if _CITACAO.match(linha):
            fecha_paragrafo()
            trechos = []
            while i < len(linhas) and _CITACAO.match(linhas[i]):
                trechos.append(_em_linha(_CITACAO.match(linhas[i]).group(1)))
                i += 1
            saida.append("<blockquote>" + "<br>".join(trechos) + "</blockquote>")
            continue

        # Tabela: linha com barra seguida da linha separadora.
        if (
            "|" in linha and i + 1 < len(linhas)
            and _SEPARADOR_DE_TABELA.match(linhas[i + 1].strip())
        ):
            fecha_paragrafo()
            cabecalho = _celulas(linha)
            corpo = []
            i += 2
            while i < len(linhas) and "|" in linhas[i] and linhas[i].strip():
                corpo.append(_celulas(linhas[i]))
                i += 1
            html = ["<table><thead><tr>"]
            html += [f"<th>{_em_linha(c)}</th>" for c in cabecalho]
            html.append("</tr></thead><tbody>")
            colunas = len(cabecalho)
            for celulas in corpo:
                # O modelo usa "|" dentro do texto ("SRE | DevOps | Platform"), e
                # isso abria colunas que o cabecalho nao tem. O que sobra volta
                # para a ultima coluna, com a barra que estava escrita.
                if len(celulas) > colunas > 0:
                    celulas = celulas[: colunas - 1] + [" | ".join(celulas[colunas - 1:])]
                html.append("<tr>" + "".join(f"<td>{_em_linha(c)}</td>" for c in celulas) + "</tr>")
            html.append("</tbody></table>")
            saida.append("".join(html))
            continue

        if _ITEM.match(linha):
            fecha_paragrafo()
            ordenada = bool(_ORDENADO.match(linha))
            itens = []
            while i < len(linhas) and _ITEM.match(linhas[i]):
                itens.append(f"<li>{_em_linha(_ITEM.match(linhas[i]).group(1))}</li>")
                i += 1
            marca = "ol" if ordenada else "ul"
            saida.append(f"<{marca}>{''.join(itens)}</{marca}>")
            continue

        paragrafo.append(_em_linha(linha.strip()))
        i += 1

    fecha_paragrafo()
    return Markup("\n".join(saida))
