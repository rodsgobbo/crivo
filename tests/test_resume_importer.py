import io

import pytest

from crivo.config import load_config
from crivo.resume.importer import (
    ResumeImportError,
    ResumeImporter,
    content_hash,
    extract_docx_text,
    extract_pdf_text,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

TEXTO_LONGO = (
    "Gerente de Infraestrutura e Cloud com vinte anos de experiencia. "
    "Liderou times de SRE e plataforma em fintech, com foco em Kubernetes, "
    "Terraform, observabilidade e reducao de custo de nuvem. "
) * 4


class FakeDrive:
    scope = "https://www.googleapis.com/auth/drive.file"

    def __init__(self, texto=TEXTO_LONGO):
        self.texto = texto
        self.chamadas = []

    def export_text(self, file_id, access_token):
        self.chamadas.append((file_id, access_token))
        return self.texto


def build_docx(texto):
    import docx

    documento = docx.Document()
    for linha in texto.split(". "):
        documento.add_paragraph(linha)
    buffer = io.BytesIO()
    documento.save(buffer)
    return buffer.getvalue()


def build_pdf(texto):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {
            "user_id": "ana",
            "subject_google": "sub-ana",
            "criado_em": "2026-01-01T00:00:00+00:00",
        },
    )
    yield connection, load_config()
    connection.close()


def importer(env, drive=None):
    connection, config = env
    return ResumeImporter(connection, config, drive or FakeDrive())


# ------------------------------------------------------------------ origens
def test_a_drive_document_becomes_a_resume(env):
    drive = FakeDrive()
    guardado = importer(env, drive).import_from_drive("ana", "arquivo-1", "token")
    assert guardado.origem == "gdoc"
    assert guardado.texto == TEXTO_LONGO
    assert drive.chamadas == [("arquivo-1", "token")]


def test_a_word_file_becomes_a_resume(env):
    guardado = importer(env).import_upload("ana", "cv.docx", build_docx(TEXTO_LONGO))
    assert guardado.origem == "docx"
    assert "Kubernetes" in guardado.texto


def test_the_three_origins_produce_the_same_shape(env):
    imp = importer(env)
    do_drive = imp.import_from_drive("ana", "f", "t")
    do_word = imp.import_upload("ana", "cv.docx", build_docx(TEXTO_LONGO))
    assert set(do_drive.__dict__) == set(do_word.__dict__)


def test_only_the_minimum_drive_scope_is_requested(env):
    assert importer(env).required_drive_scope.endswith("drive.file")


# ------------------------------------------------------------------ recusas
def test_an_unaccepted_format_names_the_accepted_ones(env):
    with pytest.raises(ResumeImportError) as err:
        importer(env).import_upload("ana", "cv.odt", b"x" * 1000)
    assert "odt" in str(err.value)
    assert "docx" in str(err.value)


def test_a_file_without_extension_is_refused(env):
    with pytest.raises(ResumeImportError):
        importer(env).import_upload("ana", "curriculo", b"x" * 1000)


def test_a_file_above_the_size_limit_is_refused(env):
    connection, config = env
    grande = b"x" * (config.resume.tamanho_maximo_bytes + 1)
    with pytest.raises(ResumeImportError) as err:
        importer(env).import_upload("ana", "cv.pdf", grande)
    assert "excede o limite" in str(err.value)


def test_a_document_without_a_text_layer_is_refused(env):
    drive = FakeDrive(texto="Curriculo")
    with pytest.raises(ResumeImportError) as err:
        importer(env, drive).import_from_drive("ana", "f", "t")
    assert "camada de texto" in str(err.value)


def test_a_scanned_pdf_is_refused(env):
    with pytest.raises(ResumeImportError) as err:
        importer(env).import_upload("ana", "cv.pdf", build_pdf(TEXTO_LONGO))
    assert "camada de texto" in str(err.value)


def test_a_corrupt_word_file_is_refused(env):
    with pytest.raises(ResumeImportError) as err:
        importer(env).import_upload("ana", "cv.docx", b"nao e um docx" * 100)
    assert "ilegivel" in str(err.value)


def test_the_daily_import_limit_is_enforced(env):
    connection, config = env
    imp = importer(env)
    for _ in range(config.resume.importacoes_por_dia):
        imp.import_from_drive("ana", "f", "t")
    with pytest.raises(ResumeImportError) as err:
        imp.import_from_drive("ana", "f", "t")
    assert "por dia" in str(err.value)


def test_the_limit_is_checked_before_any_text_extraction(env):
    connection, config = env
    drive = FakeDrive()
    imp = importer(env, drive)
    for _ in range(config.resume.importacoes_por_dia):
        imp.import_from_drive("ana", "f", "t")
    chamadas_antes = len(drive.chamadas)
    with pytest.raises(ResumeImportError):
        imp.import_from_drive("ana", "f", "t")
    assert len(drive.chamadas) == chamadas_antes


# ------------------------------------------------------------------ gravacao
def test_the_resume_is_stored_under_the_user_who_imported_it(env):
    connection, _config = env
    importer(env).import_from_drive("ana", "f", "t")
    row = connection.execute("SELECT user_id FROM resumes").fetchone()
    assert row["user_id"] == "ana"


def test_the_same_content_produces_the_same_hash():
    assert content_hash("um   texto  qualquer") == content_hash("um texto qualquer")


def test_different_content_produces_different_hashes():
    assert content_hash("texto a") != content_hash("texto b")


def build_pdf_with_text(texto):
    """Monta um PDF minimo com camada de texto, sem depender de biblioteca extra."""
    conteudo = "BT /F1 12 Tf 40 750 Td (" + texto.replace("(", "").replace(")", "") + ") Tj ET"
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n"
        + conteudo.encode() + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for indice, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += str(indice).encode() + b" 0 obj\n" + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n"
    saida += b"0000000000 65535 f \n"
    for posicao in posicoes:
        saida += f"{posicao:010d} 00000 n \n".encode()
    saida += (
        b"trailer\n<< /Size " + str(len(objetos) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(inicio_xref).encode() + b"\n%%EOF\n"
    )
    return bytes(saida)


def test_a_pdf_with_a_text_layer_becomes_a_resume(env):
    dados = build_pdf_with_text(TEXTO_LONGO)
    assert "Kubernetes" in extract_pdf_text(dados)

    guardado = importer(env).import_upload("ana", "cv.pdf", dados)
    assert guardado.origem == "pdf"
    assert "Kubernetes" in guardado.texto
