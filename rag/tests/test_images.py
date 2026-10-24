"""Indexed-PDF page image tests with a generated local 2-page fixture.

No internet, user documents, or model downloads. PDF pages are rendered
from an original file with an exact index hash.
"""
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ragcore import store
from ragcore.page_image import get_page_image


def sample_pdf(path: Path):
    from reportlab.pdfgen.canvas import Canvas
    writer = Canvas(str(path))
    writer.setFont("Helvetica", 18)
    writer.drawString(60, 730, "Page 1 - General Overview")
    writer.rect(60, 450, 380, 210, stroke=1, fill=0)
    writer.showPage()
    writer.setFont("Helvetica", 18)
    writer.drawString(60, 730, "Page 2 - Home Panel Figure")
    writer.rect(60, 450, 380, 210, stroke=1, fill=0)
    writer.showPage()
    writer.save()


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "PostgreSQL CI only")
class PageImageTests(unittest.TestCase):
    def test_indexed_source_render_and_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "manual.pdf"
            sample_pdf(source)
            (folder / "notes.txt").write_text("Notes", encoding="utf-8")
            with store.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                self.assertEqual(
                    store.scan(conn, folder, use_vectors=False)["updated"], 2,
                )
                docs = store.list_documents(conn)["documents"]
                document_id = next(x["document_id"] for x in docs if x["path"] == "manual.pdf")
                text_id = next(x["document_id"] for x in docs if x["path"] == "notes.txt")

                result = get_page_image(conn, folder, document_id, 2)
                self.assertEqual(result["path"], "manual.pdf")
                self.assertEqual(result["page"], 2)
                self.assertEqual(result["total_pages"], 2)
                self.assertTrue(result["source_verified"])
                self.assertEqual(result["image_kind"], "rendered_original_pdf_page")
                self.assertEqual(result["mime_type"], "image/jpeg")
                self.assertLessEqual(result["image_width"], 1050)
                self.assertLessEqual(result["image_height"], 1500)
                decoded = base64.b64decode(result["image_base64"], validate=True)
                self.assertGreater(len(decoded), 1000)
                self.assertTrue(decoded.startswith(bytes([0xff, 0xd8])))

                with self.assertRaisesRegex(ValueError, "PDF"):
                    get_page_image(conn, folder, text_id, 1)
                with self.assertRaisesRegex(ValueError, "Invalid page"):
                    get_page_image(conn, folder, document_id, 3)
                with self.assertRaises(ValueError):
                    get_page_image(conn, folder, document_id, 0)
                with self.assertRaises(ValueError):
                    get_page_image(conn, folder, False, 1)
                source.write_bytes(source.read_bytes() + b"\n% modified!")
                with self.assertRaisesRegex(ValueError, "differs"):
                    get_page_image(conn, folder, document_id, 1)
                source.unlink()
                with self.assertRaisesRegex(ValueError, "missing"):
                    get_page_image(conn, folder, document_id, 1)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            safe = root / "safe"
            safe.mkdir()
            sample_pdf(safe / "real.pdf")
            with store.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                store.scan(conn, safe, use_vectors=False)
                doc = store.list_documents(conn)["documents"][0]["document_id"]
                (safe / "real.pdf").rename(safe / "renamed.pdf")
                (safe / "real.pdf").symlink_to("renamed.pdf")
                with self.assertRaisesRegex(ValueError, "Symlinks"):
                    get_page_image(conn, safe, doc, 1)


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "PostgreSQL CI only")
class PageImageApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_returns_encoded_image_and_rejects_invalid_requests(self):
        import httpx
        from ragcore.api import app

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            sample_pdf(folder / "manual.pdf")
            with store.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                store.scan(conn, folder, use_vectors=False)
                doc = store.list_documents(conn)["documents"][0]["document_id"]

            secret = "tests-only-private-rag-key-0123456789abcdef"
            with patch.dict(os.environ, {
                "RAG_DOCUMENTS": str(folder), "RAG_API_TOKEN": secret,
            }):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://private-rag",
                ) as client:
                    headers = {"Authorization": "Bearer " + secret}
                    blocked = await client.post(
                        "/page-image", json={"document_id": doc, "page": 2},
                    )
                    self.assertEqual(blocked.status_code, 401)
                    ok = await client.post(
                        "/page-image", json={"document_id": doc, "page": 2},
                        headers=headers,
                    )
                    self.assertEqual(ok.status_code, 200, ok.text[:300])
                    self.assertEqual(ok.headers["cache-control"], "no-store")
                    image = ok.json()
                    self.assertEqual(image["page"], 2)
                    self.assertEqual(image["mime_type"], "image/jpeg")
                    self.assertTrue(image["source_verified"])
                    self.assertTrue(base64.b64decode(image["image_base64"])
                                    .startswith(bytes([0xff, 0xd8])))

                    for payload in (
                        {"document_id": doc, "page": 999},
                        {"document_id": doc, "page": 0},
                        {"document_id": doc, "page": True},
                        {"document_id": "1", "page": 1},
                    ):
                        response = await client.post(
                            "/page-image", json=payload, headers=headers,
                        )
                        self.assertEqual(response.status_code, 400, response.text)
