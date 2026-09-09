"""Rotas de credenciais de modelo do usuario.

A regra que governa este modulo e negativa: nenhuma resposta daqui devolve o
valor de uma credencial. A oferta mostra provedor, limite declarado e destino
dos dados; a lista mostra provedor, sufixo e posicao na cadeia. O valor em claro
existe apenas durante a requisicao que o usa, dentro do cliente de modelo.

O aviso previo e endpoint proprio e nao texto embutido no formulario, porque ele
precisa ser consultavel antes de o usuario decidir: e nele que esta para onde o
curriculo vai.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Body, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..providers.vault import CredentialValidationError


def build_router(context) -> APIRouter:
    router = APIRouter(prefix="/credentials", tags=["credenciais de modelo"])

    @router.get("")
    def listar(request: Request):
        user_id = context.guard().require(request)
        cofre = context.credential_vault()
        return {
            "credenciais": [
                {
                    "provedor": c.provedor,
                    "sufixo": c.sufixo,
                    "ordem": c.ordem,
                    "criado_em": c.criado_em,
                }
                for c in cofre.list(user_id)
            ],
            "ordem_de_tentativa": cofre.chain(user_id),
        }

    @router.get("/providers")
    def oferta(request: Request):
        context.guard().require(request)
        registro = context.provider_registry()
        return {
            "provedores": [
                {
                    "id": o.id,
                    "rotulo": o.rotulo,
                    "exige_credencial": o.exige_credencial,
                    "limite_declarado": o.limite_declarado,
                    "destino_dos_dados": o.destino_dos_dados,
                    "modelos": list(o.modelos),
                }
                for o in registro.offers()
            ],
            "indisponiveis": registro.unavailable,
        }

    @router.get("/providers/{provider_id}/disclosure")
    def aviso(request: Request, provider_id: str):
        context.guard().require(request)
        try:
            return {"aviso": context.credential_vault().disclosure_for(provider_id)}
        except CredentialValidationError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=404)

    @router.post("")
    def cadastrar(request: Request, corpo: dict = Body(...)):
        user_id = context.guard().require(request)
        provider_id = str(corpo.get("provedor") or "")
        segredo = str(corpo.get("chave") or "")
        cofre = context.credential_vault()
        try:
            guardada = cofre.store(
                user_id,
                provider_id,
                segredo,
                validate=context.validate_credential,
                default_order=context.config.providers.ordem_padrao,
            )
        except CredentialValidationError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        # A resposta confirma o cadastro pelo sufixo, nunca pelo valor.
        return {
            "provedor": guardada.provedor,
            "sufixo": guardada.sufixo,
            "ordem": guardada.ordem,
        }

    @router.post("/cadastrar")
    def cadastrar_em_pagina(
        request: Request,
        provedor: str = Form(...),
        chave: str = Form(""),
    ):
        """Mesmo cadastro, vindo de formulario em vez de JSON.

        A rota acima recebe corpo JSON, que formulario de navegador nao envia.
        Sem esta, cadastrar credencial exigiria um cliente de API -- e sem
        credencial nao ha extracao automatica nem sintese, ou seja, o produto
        inteiro ficaria atras de uma barreira de linha de comando.
        """
        user_id = context.guard().require(request)
        cofre = context.credential_vault()
        try:
            guardada = cofre.store(
                user_id,
                provedor,
                chave,
                validate=context.validate_credential,
                default_order=context.config.providers.ordem_padrao,
            )
        except CredentialValidationError as exc:
            return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
        return RedirectResponse(
            "/?aviso=" + quote(
                f"credencial de {guardada.provedor} cadastrada (final "
                f"{guardada.sufixo}); a extracao automatica ja pode rodar"
            ),
            status_code=303,
        )

    @router.put("/order")
    def reordenar(request: Request, corpo: dict = Body(...)):
        user_id = context.guard().require(request)
        ordem = list(corpo.get("ordem") or [])
        cofre = context.credential_vault()
        try:
            cofre.set_order(user_id, ordem)
        except CredentialValidationError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        return {"ordem_de_tentativa": cofre.chain(user_id)}

    @router.delete("/{provider_id}")
    def remover(request: Request, provider_id: str):
        user_id = context.guard().require(request)
        removida = context.credential_vault().remove(user_id, provider_id)
        if not removida:
            return JSONResponse(
                {"erro": f"nenhuma credencial de {provider_id} para este usuario"},
                status_code=404,
            )
        return {"removida": provider_id}

    return router
