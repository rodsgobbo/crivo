import logging

from crivo.logging_filters import REDACTED, RedactionFilter, install, redact
from crivo.secrets_vault import SecretsVault


def capture(logger, level=logging.INFO):
    records = []

    class Sink(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Sink()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return records


def test_removes_a_value_the_vault_has_delivered():
    vault = SecretsVault({"GOOGLE_CLIENT_SECRET": "valor-ultrassecreto"})
    vault.require("GOOGLE_CLIENT_SECRET")
    logger = logging.getLogger("test.vault")
    records = capture(logger)
    install(vault, logger)

    logger.info("falha ao trocar codigo com valor-ultrassecreto")

    assert "valor-ultrassecreto" not in records[0]
    assert REDACTED in records[0]


def test_removes_a_value_passed_as_an_argument():
    vault = SecretsVault({"CRIVO_MASTER_KEY": "mestra-abcdef"})
    vault.require("CRIVO_MASTER_KEY")
    logger = logging.getLogger("test.args")
    records = capture(logger)
    install(vault, logger)

    logger.info("chave em uso: %s", "mestra-abcdef")

    assert "mestra-abcdef" not in records[0]


def test_removes_authorization_and_cookie_headers():
    out = redact("Authorization: Bearer abc.def.ghi\nCookie: li_at=xyz123456")
    assert "abc.def.ghi" not in out
    assert "xyz123456" not in out
    assert out.lower().startswith("authorization")


def test_removes_provider_credentials_never_seen_by_the_vault():
    out = redact('{"api_key": "sk-usuario-1234567890", "modelo": "algum"}')
    assert "sk-usuario-1234567890" not in out
    assert "algum" in out


def test_removes_signed_token_shapes():
    token = "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl"
    assert token not in redact(f"recebido {token} do provedor")


def test_keeps_unrelated_text_intact():
    assert redact("coletadas 40 vagas, 12 apos o pre-filtro") == (
        "coletadas 40 vagas, 12 apos o pre-filtro"
    )


def test_longer_secret_is_replaced_before_a_shorter_one_inside_it():
    out = redact("abc-longo-segredo", literals=["abc", "abc-longo-segredo"])
    assert out == REDACTED


def test_exception_text_is_redacted():
    vault = SecretsVault({"CRIVO_DATABASE_URL": "postgres://u:senha-real@h/d"})
    vault.require("CRIVO_DATABASE_URL")
    logger = logging.getLogger("test.exc")
    records = capture(logger)
    install(vault, logger)

    try:
        raise RuntimeError("conexao falhou em postgres://u:senha-real@h/d")
    except RuntimeError:
        logger.exception("erro de conexao")

    joined = "\n".join(records)
    assert "senha-real" not in joined


def test_installing_twice_leaves_a_single_filter():
    logger = logging.getLogger("test.once")
    install(None, logger)
    install(None, logger)
    assert sum(isinstance(f, RedactionFilter) for f in logger.filters) == 1
