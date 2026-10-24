"""Verified figure-only JPEG crops from indexed OCR figure IDs."""
import base64
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ragcore import store
from ragcore.page_image import get_page_image
from test_ocr import diagram_pdf


@unittest.skipUnless(os.getenv("RAG_TEST_DB") == "1", "PostgreSQL CI only")
class OriginalFigureTests(unittest.IsolatedAsyncioTestCase):
    async def test_crop_exact_indexed_diagram_not_entire_pdf(self):
        import httpx
        from ragcore.api import app

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"RAG_OCR_ENABLED": "1",
                                        "RAG_OCR_LANG": "eng",
                                        "RAG_AUTO_ROTATE_ENABLED": "1"}):
            root = Path(directory)
            manual = root / "device.pdf"
            diagram_pdf(manual)
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                result = store.scan(conn, root, use_vectors=False)
                self.assertEqual(result["errors"], 0)
                doc_id = store.list_documents(conn)["documents"][0]["document_id"]
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, caption FROM rag_figures
                           WHERE document_id=%s ORDER BY ordinal""", (doc_id,)
                    )
                    figures = cur.fetchall()
                self.assertEqual(len(figures), 2)
                self.assertIn("Figure 4", figures[1]["caption"])
                whole = get_page_image(conn, root, doc_id, 1)
                cropped = get_page_image(conn, root, doc_id, 1, figures[1]["id"])
                self.assertEqual(cropped["figure_id"], figures[1]["id"])
                self.assertEqual(cropped["image_kind"], "cropped_original_pdf_figure")
                self.assertIn("Figure 4", cropped["figure_caption"])
                self.assertTrue(cropped["source_verified"])
                self.assertGreater(cropped["image_width"], cropped["image_height"])
                self.assertLess(cropped["image_height"], whole["image_height"])
                self.assertNotEqual(whole["image_base64"], cropped["image_base64"])
                decoded = Image.open(BytesIO(base64.b64decode(cropped["image_base64"])))
                self.assertEqual(decoded.size, (cropped["image_width"], cropped["image_height"]))
                # The figure ID belongs to page 1, never page 2.
                with self.assertRaisesRegex(ValueError, "Figure"):
                    get_page_image(conn, root, doc_id, 2, figures[1]["id"])
                with self.assertRaises(ValueError):
                    get_page_image(conn, root, doc_id, 1, 987654321)
                with self.assertRaises(ValueError):
                    get_page_image(conn, root, doc_id, 1, True)
                with self.assertRaises(ValueError):
                    get_page_image(conn, root, doc_id, 1, -1)

            secret = "rag-figure-test-token-0123456789abcdef"
            with patch.dict(os.environ, {"RAG_DOCUMENTS": str(root),
                                        "RAG_API_TOKEN": secret}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://private-rag",
                ) as client:
                    headers = {"Authorization": "Bearer " + secret}
                    request = {
                        "document_id": doc_id, "page": 1,
                        "figure_id": figures[1]["id"],
                    }
                    inventory = await client.post(
                        "/figures", json={"document_id": doc_id, "page": 1},
                        headers=headers,
                    )
                    self.assertEqual(inventory.status_code, 200, inventory.text)
                    refs = inventory.json()["figures"]
                    self.assertEqual(len(refs), 2)
                    self.assertEqual(refs[1]["figure_id"], figures[1]["id"])
                    self.assertIn("Figure 4", refs[1]["caption"])
                    for bad_id in (True, 0, -1, "wrong"):
                        bad = await client.post(
                            "/figures", json={"document_id": bad_id, "page": 1},
                            headers=headers,
                        )
                        self.assertEqual(bad.status_code, 400, bad.text)
                    ok = await client.post("/page-image", json=request, headers=headers)
                    self.assertEqual(ok.status_code, 200, ok.text[:300])
                    self.assertEqual(ok.json()["image_kind"], "cropped_original_pdf_figure")
                    self.assertEqual(ok.json()["figure_id"], figures[1]["id"])
                    self.assertTrue(ok.json()["source_verified"])
                    for bad in (0, -1, True, "bad", 1234567890):
                        result = await client.post(
                            "/page-image", json={**request, "figure_id": bad}, headers=headers
                        )
                        self.assertEqual(result.status_code, 400, result.text)
                    unauthorized = await client.post("/page-image", json=request)
                    self.assertEqual(unauthorized.status_code, 401)

            # Tampering with original invalidates cached figure coordinates.
            manual.write_bytes(manual.read_bytes() + b"\n% edited")
            with store.connect() as conn:
                with self.assertRaisesRegex(ValueError, "differs"):
                    get_page_image(conn, root, doc_id, 1, figures[1]["id"])
