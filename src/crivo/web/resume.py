"""Rotas de importacao de curriculo e a tela de conferencia da extracao.

A conferencia e o portao que protege todo o resto do pipeline de uma extracao
ruim, e por isso ela tem endpoint proprio para ler, corrigir e confirmar. Ler e
corrigir nao mexem no portao; so a confirmacao abre.

O caminho manual nao e tratamento de erro: e um caminho de primeira classe, para
quem nao tem credencial de modelo ou nao quer usar uma. Ele existe no mesmo
lugar que o automatico.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..resume.importer import ResumeImportError
from ..resume.parser import CAMPOS, ExtractionError, ManualEntryRequired
from .auth import de_navegador as _de_navegador

#: Campos cujo valor e uma lista, e como o formulario a representa.
LISTA_POR_LINHA = ("formacao",)
LISTA_POR_VIRGULA = ("competencias", "idiomas")
LISTA_EM_JSON = ("experiencias",)


@lru_cache(maxsize=1)
def _pagina() -> Environment:
    ambiente = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    ambiente.globals["para_formulario"] = _para_formulario
    return ambiente


def _valor_do_formulario(campo: str, bruto: str):
    """Converte o texto digitado no tipo que o campo guarda.

    Vazio vira `None` e nao lista vazia: a extracao distingue "o texto nao
    afirmou" de "afirmou que nao ha", e o formulario nao pode apagar essa
    diferenca ao devolver o valor.
    """
    texto = (bruto or "").strip()
    if not texto:
        return None
    if campo in LISTA_EM_JSON:
        try:
            valor = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{campo}: JSON invalido na linha {exc.lineno}, coluna "
                f"{exc.colno} -- esperada uma lista de objetos"
            ) from exc
        if not isinstance(valor, list):
            raise ValueError(f"{campo}: esperada uma lista, veio {type(valor).__name__}")
        return valor
    if campo in LISTA_POR_LINHA:
        return [linha.strip() for linha in texto.splitlines() if linha.strip()]
    if campo in LISTA_POR_VIRGULA:
        return [item.strip() for item in texto.split(",") if item.strip()]
    return texto


def _para_formulario(campo: str, valor) -> str:
    """O inverso: mostra o valor guardado na forma que o formulario aceita."""
    if valor is None:
        return ""
    if campo in LISTA_EM_JSON:
        return json.dumps(valor, ensure_ascii=False, indent=2)
    if campo in LISTA_POR_LINHA and isinstance(valor, list):
        return "\n".join(str(item) for item in valor)
    if campo in LISTA_POR_VIRGULA and isinstance(valor, list):
        return ", ".join(str(item) for item in valor)
    return str(valor)


def _resume_mais_recente(context, user_id: str) -> str | None:
    linhas = context.repository().for_user(user_id).select(
        "resumes", columns="resume_id", order_by="importado_em DESC"
    )
    return linhas[0]["resume_id"] if linhas else None


def _extracao_para_json(extraida) -> dict:
    return {
        "extraction_id": extraida.extraction_id,
        "resume_id": extraida.resume_id,
        "campos": extraida.campos,
        "correcoes": extraida.correcoes,
        "efetivo": extraida.efetivo(),
        "trechos_origem": extraida.trechos_origem,
        "lacunas": extraida.lacunas,
        "provedor": extraida.provedor,
        "modelo": extraida.modelo,
        "confirmada": extraida.confirmada,
    }


def build_router(context) -> APIRouter:
    router = APIRouter(prefix="/resume", tags=["curriculo"])

    # ------------------------------------------------------------ importacao
    @router.get("/disclosure")
    def aviso(request: Request):
        context.guard().require(request)
        return {"aviso": context.privacy_service().disclosure()}

    @router.post("/upload")
    async def enviar(request: Request, arquivo: UploadFile = File(...)):
        user_id = context.guard().require(request)
        dados = await arquivo.read()
        try:
            guardado = context.resume_importer().import_upload(
                user_id, arquivo.filename or "", dados
            )
        except ResumeImportError as exc:
            if _de_navegador(request):
                return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
            return JSONResponse({"erro": str(exc)}, status_code=400)
        if _de_navegador(request):
            return RedirectResponse(
                "/?aviso=" + quote(
                    "currículo importado; falta extrair e conferir os campos "
                    "antes que ele alimente uma busca"
                ),
                status_code=303,
            )
        return {"resume_id": guardado.resume_id, "origem": guardado.origem}

    @router.post("/drive")
    def do_drive(request: Request, corpo: dict = Body(...)):
        user_id = context.guard().require(request)
        try:
            guardado = context.resume_importer().import_from_drive(
                user_id, str(corpo.get("file_id") or ""),
                str(corpo.get("access_token") or ""),
            )
        except ResumeImportError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        return {"resume_id": guardado.resume_id, "origem": guardado.origem}

    @router.get("/drive/scope")
    def escopo(request: Request):
        context.guard().require(request)
        return {"escopo": context.resume_importer().required_drive_scope}

    # ------------------------------------------------------------ extracao
    @router.post("/{resume_id}/extract")
    def extrair(request: Request, resume_id: str):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        try:
            extraida = parser.extract(user_id, resume_id)
        except ManualEntryRequired as exc:
            # Nao e erro: e o caminho manual sendo oferecido.
            if _de_navegador(request):
                return RedirectResponse(
                    f"/resume/conferencia?manual=1&motivo={quote(str(exc))}",
                    status_code=303,
                )
            return JSONResponse(
                {"preenchimento_manual": True, "motivo": str(exc)}, status_code=200
            )
        except ExtractionError as exc:
            if _de_navegador(request):
                return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
            return JSONResponse({"erro": str(exc)}, status_code=400)
        if _de_navegador(request):
            return RedirectResponse("/resume/conferencia", status_code=303)
        return _extracao_para_json(extraida)

    @router.post("/{resume_id}/manual")
    def manual(request: Request, resume_id: str, corpo: dict = Body(...)):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        try:
            return _extracao_para_json(parser.manual(user_id, resume_id, corpo))
        except ExtractionError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)

    # ---------------------------------------------------------- conferencia
    @router.get("/pending")
    def pendentes(request: Request):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        return {
            "pendentes": [_extracao_para_json(e) for e in parser.pending(user_id)]
        }

    @router.get("/confirmed")
    def confirmada(request: Request):
        user_id = context.guard().require(request)
        atual = context.resume_parser(user_id).confirmed(user_id)
        if atual is None:
            return JSONResponse(
                {"erro": "nenhuma extracao confirmada; confira uma antes de buscar"},
                status_code=404,
            )
        return _extracao_para_json(atual)

    @router.patch("/extractions/{extraction_id}")
    def corrigir(request: Request, extraction_id: str, corpo: dict = Body(...)):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        try:
            atual = parser.correct(
                user_id, extraction_id,
                str(corpo.get("campo") or ""), corpo.get("valor"),
            )
        except ExtractionError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        return _extracao_para_json(atual)

    @router.post("/extractions/{extraction_id}/confirm")
    def confirmar(request: Request, extraction_id: str):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        try:
            return _extracao_para_json(parser.confirm(user_id, extraction_id))
        except ExtractionError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)

    # ------------------------------------------------- conferencia em pagina
    # As rotas acima falam JSON e usam PATCH, que formulario de navegador nao
    # emite. Estas existem para que a conferencia -- o portao que protege todo
    # o pipeline -- seja alcancavel sem JavaScript e sem cliente de API.
    @router.get("/conferencia", response_class=HTMLResponse)
    def conferencia(request: Request, manual: str = "", motivo: str = ""):
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        pendentes = parser.pending(user_id)
        confirmada = parser.confirmed(user_id)
        atual = pendentes[0] if pendentes else confirmada
        return HTMLResponse(
            _pagina().get_template("conferencia.html.j2").render(
                ctx={
                    "campos": CAMPOS,
                    "extracao": _extracao_para_json(atual) if atual else None,
                    "confirmada": atual is not None and atual.confirmada,
                    "resume_id": (
                        atual.resume_id if atual
                        else _resume_mais_recente(context, user_id)
                    ),
                    "manual": bool(manual) or (atual is None),
                    "motivo": motivo,
                    "erro": request.query_params.get("erro"),
                    "aviso": request.query_params.get("aviso"),
                }
            )
        )

    @router.post("/conferencia/{extraction_id}/campo")
    def corrigir_em_pagina(
        request: Request,
        extraction_id: str,
        campo: str = Form(...),
        valor: str = Form(""),
    ):
        user_id = context.guard().require(request)
        try:
            context.resume_parser(user_id).correct(
                user_id, extraction_id, campo, _valor_do_formulario(campo, valor)
            )
        except (ExtractionError, ValueError) as exc:
            return RedirectResponse(
                f"/resume/conferencia?erro={quote(str(exc))}", status_code=303
            )
        return RedirectResponse("/resume/conferencia", status_code=303)

    @router.post("/conferencia/{extraction_id}/campos")
    async def corrigir_tudo(request: Request, extraction_id: str):
        """Grava os campos alterados de uma vez, e confirma se pedirem.

        Antes cada campo era um formulario proprio, e corrigir os sete custava
        sete recarregamentos de pagina. O custo acumulado empurrava para
        confirmar sem revisar -- exatamente o que este portao existe para
        impedir, e o que de fato aconteceu num uso real, com uma extracao
        confirmada em branco.

        So o que mudou e gravado: uma correcao registrada ao lado do valor
        extraido significa "uma pessoa discordou daqui", e reescrever os sete
        campos a cada visita apagaria essa distincao.
        """
        user_id = context.guard().require(request)
        parser = context.resume_parser(user_id)
        formulario = await request.form()
        try:
            atual = parser.extraction(user_id, extraction_id)
            vigente = atual.efetivo()
            for campo in CAMPOS:
                if campo not in formulario:
                    continue
                novo = _valor_do_formulario(campo, str(formulario.get(campo) or ""))
                if novo != vigente.get(campo):
                    parser.correct(user_id, extraction_id, campo, novo)
            if formulario.get("confirmar"):
                parser.confirm(user_id, extraction_id)
        except (ExtractionError, ValueError) as exc:
            return RedirectResponse(
                f"/resume/conferencia?erro={quote(str(exc))}", status_code=303
            )
        if formulario.get("confirmar"):
            return RedirectResponse(
                "/?aviso=" + quote(
                    "currículo conferido; seu perfil já pode ser consolidado"
                ),
                status_code=303,
            )
        return RedirectResponse(
            "/resume/conferencia?aviso=" + quote("correções gravadas"),
            status_code=303,
        )

    @router.post("/conferencia/{extraction_id}/confirmar")
    def confirmar_em_pagina(request: Request, extraction_id: str):
        user_id = context.guard().require(request)
        try:
            context.resume_parser(user_id).confirm(user_id, extraction_id)
        except ExtractionError as exc:
            return RedirectResponse(
                f"/resume/conferencia?erro={quote(str(exc))}", status_code=303
            )
        return RedirectResponse(
            "/?aviso=" + quote("currículo conferido; seu perfil já pode ser montado"),
            status_code=303,
        )

    @router.post("/conferencia/{resume_id}/manual")
    async def manual_em_pagina(request: Request, resume_id: str):
        user_id = context.guard().require(request)
        formulario = await request.form()
        try:
            campos = {
                campo: _valor_do_formulario(campo, str(formulario.get(campo) or ""))
                for campo in CAMPOS
            }
            context.resume_parser(user_id).manual(user_id, resume_id, campos)
        except (ExtractionError, ValueError) as exc:
            return RedirectResponse(
                f"/resume/conferencia?erro={quote(str(exc))}", status_code=303
            )
        return RedirectResponse(
            "/?aviso=" + quote("perfil preenchido a mao e confirmado"),
            status_code=303,
        )

    return router
