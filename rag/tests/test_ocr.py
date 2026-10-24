"""CPU OCR integration: source figure + labels -> indexed searchable evidence.

Tests use a synthetic diagram raster embedded into a PDF with a native
figure caption; no online assets, hosted model, or product-specific rules.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragcore import store
from ragcore.ocr import OCR_VERSION, extract_pdf_figures, enabled


def diagram_pdf(path: Path) -> None:
    from io import BytesIO
    from PIL import Image as PILImage, ImageDraw, ImageFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader

    raster = PILImage.new("RGB", (1000, 430), "white")
    pen = ImageDraw.Draw(raster)
    font = ImageFont.truetype("DejaVuSans.ttf", 32)
    pen.text((45, 42), "2 UT connector LEMO 00", fill="black", font=font)
    pen.text((45, 108), "Phased-array IPEX connector", fill="black", font=font)
    pen.text((45, 174), "Encoder inputs", fill="black", font=font)
    pen.text((45, 240), "USB 3.0 connector", fill="black", font=font)
    pen.text((45, 306), "Micro DisplayPort", fill="black", font=font)
    pen.text((45, 366), "Ethernet connector", fill="black", font=font)
    img = BytesIO()
    raster.save(img, format="PNG")
    img.seek(0)

    pdf = Canvas(str(path), pagesize=(612, 792))
    pdf.setFont("Helvetica", 14)
    pdf.drawString(80, 718, "MANTIS overview")
    control = PILImage.new("RGB", (1000, 245), "white")
    label = ImageDraw.Draw(control)
    label.text((45, 40), "Screen copy", fill="black", font=font)
    label.text((45, 118), "Adjust Step", fill="black", font=font)
    pdf.drawImage(ImageReader(control), 88, 520, width=450, height=125)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(170, 505, "Figure 3: MANTIS front-panel buttons")
    pdf.drawImage(ImageReader(img), 88, 245, width=450, height=225)
    pdf.drawString(170, 231, "Figure 4: MANTIS connectors overview")
    pdf.drawString(80, 150, "Technical equipment documentation")
    pdf.save()


class RecognitionTests(unittest.TestCase):
    def test_detect_labels_and_match_nearby_native_figure_caption(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"RAG_OCR_ENABLED": "1",
                                        "RAG_OCR_LANG": "eng"}):
            source = Path(temp) / "manual.pdf"
            diagram_pdf(source)
            figures, warnings = extract_pdf_figures(source)
            self.assertEqual(warnings, [])
            self.assertEqual(len(figures), 2)
            self.assertIn("Figure 3", figures[0]["caption"])
            self.assertNotIn("Ethernet connector", figures[0]["ocr_text"])
            figure = figures[1]
            self.assertEqual(figure["page"], 1)
            self.assertIn("Figure 4", figure["caption"])
            self.assertIn("MANTIS connectors", figure["caption"])
            self.assertIn("Ethernet connector", figure["ocr_text"])
            self.assertIn("USB 3.0 connector", figure["ocr_text"])
            self.assertGreater(figure["ocr_confidence"], 20)
            self.assertEqual(figure["kind"], "embedded_image")
        with patch.dict(os.environ, {"RAG_OCR_ENABLED": "0"}):
            self.assertFalse(enabled())


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "PostgreSQL CI only")
class IndexTests(unittest.TestCase):
    def test_ocr_search_returns_figure_and_preserves_literal_raw_text(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"RAG_OCR_ENABLED": "1",
                                        "RAG_OCR_LANG": "eng"}):
            root = Path(temp)
            diagram_pdf(root / "manual.pdf")
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                first = store.scan(conn, root, use_vectors=False)
                self.assertEqual(first["updated"], 1)
                self.assertEqual(first["errors"], 0)
                docs = store.list_documents(conn)["documents"]
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["figures"], 2)
                self.assertEqual(docs[0]["ocr_chunks"], 2)
                document_id = docs[0]["document_id"]
                raw = store.get_text(conn, document_id)[1]
                self.assertIn("MANTIS overview", raw)
                self.assertIn("Figure 4", raw)
                self.assertNotIn("Ethernet connector", raw)
                self.assertEqual(store.verify_index(conn)["coverage_errors"], [])

                results = store.search(conn, "Ethernet connector", use_vectors=False)
                ocr_hits = [r for r in results if r["source_type"] == "ocr_figure"]
                self.assertTrue(ocr_hits, results)
                hit = ocr_hits[0]
                self.assertIn("Ethernet connector", hit["ocr_text"])
                self.assertEqual(hit["document_id"], document_id)
                self.assertEqual(hit["locations"][0]["page"], 1)
                self.assertEqual(hit["figure"]["page"], 1)
                self.assertIn("Figure 4", hit["figure"]["caption"])
                self.assertIsNone(hit["char_start"])
                self.assertIsNone(hit["article"])
                self.assertGreater(hit["ocr_confidence"], 20)

                again = store.scan(conn, root, use_vectors=False)
                self.assertEqual(again["unchanged"], 1)
                # OCR version changes trigger a revision, even with same file hash.
                with conn.cursor() as cur:
                    cur.execute("UPDATE rag_documents SET ocr_version=0 WHERE id=%s",
                                (document_id,))
                upgraded = store.scan(conn, root, use_vectors=False)
                self.assertEqual(upgraded["updated"], 1)
                with conn.cursor() as cur:
                    cur.execute("SELECT ocr_version FROM rag_documents WHERE id=%s",
                                (document_id,))
                    self.assertEqual(cur.fetchone()["ocr_version"], OCR_VERSION)

                # Disabling OCR clears stale figure/OCR rows on reindex.
                with patch.dict(os.environ, {"RAG_OCR_ENABLED": "0"}):
                    no_ocr = store.scan(conn, root, use_vectors=False)
                self.assertEqual(no_ocr["updated"], 1)
                self.assertEqual(store.list_documents(conn)["documents"][0]["ocr_chunks"], 0)
                self.assertEqual(store.search(
                    conn, "Ethernet connector", use_vectors=False), [])
