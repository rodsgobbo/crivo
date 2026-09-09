"""Cifragem envelopada do material sensivel guardado em repouso.

Duas coisas do sistema precisam viver cifradas no banco: o testemunho de acesso
das origens conectadas e a credencial de modelo que cada usuario informa. As
duas usam o mesmo envelope, definido aqui.

O envelope tem dois niveis. Cada segredo e cifrado com uma chave de dado propria,
gerada na hora, e essa chave de dado e cifrada pela chave mestra do operador. A
consequencia pratica e que trocar a chave mestra nao exige reescrever os
segredos, apenas as chaves de dado; e que o vazamento de uma chave de dado
compromete um segredo, nao o acervo.

O valor em claro so existe durante a operacao que o usa. Nenhuma leitura deste
modulo devolve segredo para a interface: quem exibe recebe o sufixo, nunca a
chave.
"""

from __future__ import annotations

import base64
import os
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Tamanho da chave de dado, em bytes.
DATA_KEY_SIZE = 32

#: Tamanho do vetor de inicializacao exigido pelo modo autenticado usado aqui.
NONCE_SIZE = 12

#: Quantos caracteres finais da credencial sao exibidos ao usuario.
SUFFIX_LENGTH = 4


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CryptoError(Exception):
    """Chave mestra invalida, ou material cifrado que nao abre."""


@dataclass(frozen=True)
class SealedSecret:
    """Segredo cifrado, com a chave de dado que o abre, tambem cifrada."""

    ciphertext: bytes
    wrapped_key: bytes
    suffix: str


def generate_master_key() -> str:
    """Gera uma chave mestra nova, em texto transportavel por ambiente."""
    return base64.urlsafe_b64encode(os.urandom(DATA_KEY_SIZE)).decode("ascii")


class EnvelopeCipher:
    """Sela e abre segredos com chave de dado por segredo."""

    def __init__(self, master_key: str) -> None:
        self._master = _decode_master(master_key)

    def seal(self, plaintext: str, associated: str = "") -> SealedSecret:
        """Cifra o segredo e devolve o envelope pronto para gravacao."""
        if not plaintext:
            raise CryptoError("segredo vazio nao pode ser selado")
        data_key = os.urandom(DATA_KEY_SIZE)
        ciphertext = _encrypt(data_key, plaintext.encode("utf-8"), associated)
        wrapped = _encrypt(self._master, data_key, associated)
        return SealedSecret(
            ciphertext=ciphertext,
            wrapped_key=wrapped,
            suffix=plaintext[-SUFFIX_LENGTH:] if len(plaintext) > SUFFIX_LENGTH else "",
        )

    def open(self, sealed: SealedSecret, associated: str = "") -> str:
        """Devolve o segredo em claro. O valor so deve viver durante a operacao."""
        data_key = _decrypt(self._master, sealed.wrapped_key, associated)
        return _decrypt(data_key, sealed.ciphertext, associated).decode("utf-8")


def _decode_master(master_key: str) -> bytes:
    if not master_key:
        raise CryptoError(
            "chave mestra ausente; defina CRIVO_MASTER_KEY no ambiente"
        )
    try:
        raw = base64.urlsafe_b64decode(master_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CryptoError(
            "chave mestra ilegivel; esperado texto em base64 seguro para URL"
        ) from exc
    if len(raw) != DATA_KEY_SIZE:
        raise CryptoError(
            f"chave mestra com {len(raw)} bytes; esperado {DATA_KEY_SIZE}"
        )
    return raw


def _encrypt(key: bytes, plaintext: bytes, associated: str) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, plaintext, associated.encode("utf-8"))
    return nonce + sealed


def _decrypt(key: bytes, payload: bytes, associated: str) -> bytes:
    if len(payload) <= NONCE_SIZE:
        raise CryptoError("material cifrado truncado")
    nonce, sealed = payload[:NONCE_SIZE], payload[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, sealed, associated.encode("utf-8"))
    except InvalidTag as exc:
        raise CryptoError(
            "material cifrado nao abre com esta chave, ou foi adulterado"
        ) from exc


# ---------------------------------------------------------------- armazenamento


@dataclass(frozen=True)
class StoredCredential:
    """O que a interface pode ver sobre uma credencial guardada.

    Nao existe campo com o valor em claro. Isso e proposital: a ausencia do
    campo e o que torna a nao reexibicao uma propriedade do tipo, e nao uma
    disciplina de quem escreve a tela.
    """

    provedor: str
    sufixo: str
    ordem: int
    criado_em: str


class CredentialValidationError(Exception):
    """O provedor recusou a credencial informada, ou ela nao cabe no registro."""


class CredentialVault:
    """Guarda, ordena e devolve as credenciais de modelo de cada usuario."""

    def __init__(self, connection, cipher: "EnvelopeCipher", registry) -> None:
        from ..store.repository import Repository

        self._repository = Repository(connection)
        self._cipher = cipher
        self._registry = registry

    # ------------------------------------------------------------- aviso
    def disclosure_for(self, provider_id: str) -> str:
        """Texto mostrado antes do primeiro armazenamento de credencial."""
        provider = self._require_provider(provider_id)
        return (
            f"Ao usar {provider.rotulo}, o texto do seu curriculo e as descricoes "
            f"das vagas selecionadas serao enviados para "
            f"{provider.destino_dos_dados}. Limite declarado pelo provedor: "
            f"{provider.limite_declarado}. Sua chave e guardada cifrada e nunca "
            "e exibida de novo."
        )

    def has_any(self, user_id: str) -> bool:
        return bool(self._repository.for_user(user_id).select("provider_credentials"))

    # ------------------------------------------------------------ escrita
    def store(
        self,
        user_id: str,
        provider_id: str,
        secret: str,
        validate,
        default_order: tuple = (),
    ) -> StoredCredential:
        """Valida contra o provedor e so entao guarda, sempre cifrada.

        A ordem importa: gravar antes de validar deixaria credencial morta no
        cofre e faria a cadeia de fallback tentar um destino que ja se sabe
        recusado.
        """
        provider = self._require_provider(provider_id)
        if provider.exige_credencial and not (secret or "").strip():
            raise CredentialValidationError(
                f"{provider.rotulo}: este provedor exige chave e nenhuma foi informada"
            )
        try:
            aceita = validate(provider, secret)
        except Exception as exc:
            raise CredentialValidationError(
                f"{provider.rotulo} recusou a credencial: {type(exc).__name__}: {exc}"
            ) from exc
        if not aceita:
            raise CredentialValidationError(
                f"{provider.rotulo} recusou a credencial informada"
            )

        sealed = self._cipher.seal(secret or provider_id, associated=user_id)
        primeira = not self.has_any(user_id)
        ordem = (
            self._seed_order(provider_id, tuple(default_order))
            if primeira
            else self._next_order(user_id)
        )
        criado_em = _timestamp()
        scope = self._repository.for_user(user_id)
        scope.delete(
            "provider_credentials", where="provedor = ?", params=(provider_id,)
        )
        scope.insert(
            "provider_credentials",
            {
                "credential_id": str(_uuid.uuid4()),
                "provedor": provider_id,
                "chave_cifrada": sealed.ciphertext,
                "chave_de_dado_cifrada": sealed.wrapped_key,
                "sufixo": sealed.suffix,
                "ordem": ordem,
                "criado_em": criado_em,
            },
        )
        return StoredCredential(
            provedor=provider_id,
            sufixo=sealed.suffix,
            ordem=ordem,
            criado_em=criado_em,
        )

    def remove(self, user_id: str, provider_id: str) -> bool:
        removidas = self._repository.for_user(user_id).delete(
            "provider_credentials", where="provedor = ?", params=(provider_id,)
        )
        return removidas > 0

    def set_order(self, user_id: str, ordered_ids: list) -> None:
        """Grava a ordem de tentativa escolhida pelo usuario."""
        scope = self._repository.for_user(user_id)
        conhecidos = {r["provedor"] for r in scope.select("provider_credentials")}
        desconhecidos = [p for p in ordered_ids if p not in conhecidos]
        if desconhecidos:
            raise CredentialValidationError(
                f"a ordem cita provedores sem credencial deste usuario: "
                f"{desconhecidos}"
            )
        for posicao, provider_id in enumerate(ordered_ids):
            scope.update(
                "provider_credentials",
                {"ordem": posicao},
                where="provedor = ?",
                params=(provider_id,),
            )

    # ------------------------------------------------------------ leitura
    def list(self, user_id: str) -> list:
        """Lista o que existe, sem nunca devolver o valor em claro."""
        return [
            StoredCredential(
                provedor=row["provedor"],
                sufixo=row["sufixo"],
                ordem=row["ordem"],
                criado_em=row["criado_em"],
            )
            for row in self._repository.for_user(user_id).select(
                "provider_credentials", order_by="ordem, provedor"
            )
        ]

    def chain(self, user_id: str) -> list:
        """Ordem de tentativa do usuario, do primeiro ao ultimo destino."""
        return [credencial.provedor for credencial in self.list(user_id)]

    def reveal(self, user_id: str, provider_id: str) -> str:
        """Abre a credencial para uso imediato. Nao vai para a interface."""
        rows = self._repository.for_user(user_id).select(
            "provider_credentials", where="provedor = ?", params=(provider_id,)
        )
        if not rows:
            raise CredentialValidationError(
                f"usuario nao tem credencial para {provider_id!r}"
            )
        row = rows[0]
        return self._cipher.open(
            SealedSecret(
                ciphertext=row["chave_cifrada"],
                wrapped_key=row["chave_de_dado_cifrada"],
                suffix=row["sufixo"],
            ),
            associated=user_id,
        )

    # ------------------------------------------------------------ interno
    def _require_provider(self, provider_id: str):
        provider = self._registry.get(provider_id)
        if provider is None:
            raise CredentialValidationError(
                f"provedor {provider_id!r} nao esta no registro habilitado"
            )
        return provider

    def _seed_order(self, provider_id: str, default_order: tuple) -> int:
        """Semeia a ordem do usuario a partir do padrao do operador."""
        if provider_id in default_order:
            return default_order.index(provider_id)
        return len(default_order)

    def _next_order(self, user_id: str) -> int:
        existentes = self.list(user_id)
        return max((c.ordem for c in existentes), default=-1) + 1
