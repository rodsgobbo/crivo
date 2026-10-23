"""A leitura estrategica do modelo vira HTML sem que o texto dele vire codigo."""

from __future__ import annotations

import pytest
from markupsafe import Markup

from crivo.report.markdown_seguro import markdown_seguro


# ------------------------------------------------------------- seguranca
@pytest.mark.parametrize("ataque", [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">clique</a>',
    "**<script>alert(1)</script>**",
    "| <b onmouseover=alert(1)>x</b> | y |\n|---|---|\n| a | b |",
])
def test_no_html_from_the_model_survives(ataque):
    """O texto e de terceiro; uma injecao de prompt pode escrever qualquer coisa.

    Toda marcacao do resultado tem de ser das que o conversor produz, e nenhuma
    pode carregar atributo -- e por atributo que um evento entraria.
    """
    import re

    html = str(markdown_seguro(ataque))
    permitidas = {"p", "br", "strong", "em", "h3", "h4", "h5", "ul", "ol", "li",
                  "table", "thead", "tbody", "tr", "th", "td", "hr", "blockquote"}
    marcas = re.findall(r"<\s*/?\s*([a-zA-Z0-9]+)([^>]*)>", html)
    assert {nome.lower() for nome, _ in marcas} <= permitidas, html
    assert all(not atributos.strip() for _, atributos in marcas), html


def test_the_result_is_markup_so_the_template_needs_no_safe():
    assert isinstance(markdown_seguro("**x**"), Markup)


def test_empty_text_is_empty():
    assert markdown_seguro(None) == "" and markdown_seguro("") == ""


# ------------------------------------------------------------ formatacao
def test_headings_bold_and_lists():
    html = str(markdown_seguro("# Titulo\n### 1. Resumo\n- **um**\n- dois\n\n1. a\n2. b"))
    assert "<h3>Titulo</h3>" in html
    assert "<h4>1. Resumo</h4>" in html, "### nao pode ficar menor que o texto"
    assert "<ul><li><strong>um</strong></li><li>dois</li></ul>" in html
    assert "<ol><li>a</li><li>b</li></ol>" in html


def test_a_table_and_the_line_breaks_inside_its_cells():
    html = str(markdown_seguro("| Item | Detalhe |\n|---|---|\n| Entregas | - um<br>- dois |"))
    assert "<th>Item</th><th>Detalhe</th>" in html
    assert "<td>- um<br>- dois</td>" in html


def test_a_pipe_inside_a_cell_stays_in_the_last_column():
    """Visto no relatorio de 23/10/2026: "SRE | DevOps | Platform" abria colunas."""
    html = str(markdown_seguro(
        "| Item | Detalhe |\n|---|---|\n| Experiencia | Tech Manager - SRE | DevOps | Platform |"
    ))
    assert html.count("<td>") == 2
    assert "<td>Tech Manager - SRE | DevOps | Platform</td>" in html


def test_a_quote_and_a_rule():
    html = str(markdown_seguro("---\n> **Observacao:** dados coletados"))
    assert "<hr>" in html
    assert "<blockquote><strong>Observacao:</strong> dados coletados</blockquote>" in html


def test_plain_lines_become_paragraphs():
    html = str(markdown_seguro("primeira\nsegunda\n\nterceira"))
    assert html == "<p>primeira<br>segunda</p>\n<p>terceira</p>"


# ----------------------------------------------------------- no relatorio
def test_the_report_renders_the_synthesis_formatted_and_escaped(tmp_path):
    from test_report import add_vaga, env  # noqa: F401 -- reaproveita a montagem

    from crivo.config import load_config
    from crivo.report.renderer import ReportRenderer
    from crivo.store.migrations import open_database
    from crivo.store.repository import Repository

    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert("users", {"user_id": "ana", "subject_google": "s", "criado_em": "2026-01-01"})
    repo.insert("runs", {
        "run_id": "run-1", "user_id": "ana", "estado": "concluido",
        "janela": "ampla", "solicitado_em": "2026-10-23T09:00:00+00:00",
        "sintese": "### Resumo\n**Forte** em SRE\n<script>alert(1)</script>",
    })
    add_vaga(repo)
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    connection.close()
    assert "<h4>Resumo</h4>" in html
    assert "<strong>Forte</strong>" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
