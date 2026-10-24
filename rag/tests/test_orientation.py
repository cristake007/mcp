"""Automatic orientation tests: native PDF, mixed rotation, indexing and images.

Tests never alter the input document. A rotated page from pypdf has source
text displaced exactly as in real-world PDFs with incorrect /Rotate metadata.
"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ragcore.orientation import detect_rotations, normalized_pdf, native_rotation


def sample_pdf(path: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        upright = Path(td) / "upright.pdf"
        builder = canvas.Canvas(str(upright), pagesize=(612, 792))
        texts = [
            "First page is already upright - Capture online activation and guidance.",
            "Second page must be corrected - Capture license activation offline workflow.",
            "Third page must be corrected - transfer a license to another computer.",
            "Fourth page must be corrected - Capture GO Mantis activation instructions.",
        ]
        for content in texts:
            builder.setFont("Helvetica", 15)
            builder.drawString(55, 650, content)
            builder.showPage()
        builder.save()
        reader = PdfReader(str(upright))
        writer = PdfWriter()
        for n, p in enumerate(reader.pages):
            item = writer.add_page(p)
            item.rotate([0, 270, 180, 90][n])
        with path.open("wb") as stream:
            writer.write(stream)


class OrientationDetectionTests(unittest.TestCase):
    def test_mixed_orientation_normalizes_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "rotated.pdf"
            sample_pdf(source)
            old = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(detect_rotations(source), {2: 90, 3: 180, 4: 270})
            with normalized_pdf(source) as (working, rotations):
                self.assertNotEqual(working, source)
                self.assertEqual(rotations, {2: 90, 3: 180, 4: 270})
                with pdfplumber.open(working) as pdf:
                    self.assertEqual(len(pdf.pages), 4)
                    self.assertTrue(all(
                        sum(c["upright"] for c in page.chars) / len(page.chars) > 0.9
                        for page in pdf.pages
                    ))
                    self.assertIn("Second page must be corrected",
                                  pdf.pages[1].extract_text())
                    self.assertIn("Third page must be corrected",
                                  pdf.pages[2].extract_text())
                    self.assertIn("Fourth page must be corrected",
                                  pdf.pages[3].extract_text())
                self.assertTrue(working.exists())
            self.assertFalse(working.exists())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), old)

    def test_disabled_mode_preserves_original_orientation(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "rotated.pdf"
            sample_pdf(source)
            with patch.dict(os.environ, {"RAG_AUTO_ROTATE_ENABLED": "0"}):
                with normalized_pdf(source) as (working, angles):
                    self.assertEqual(working, source)
                    self.assertEqual(angles, {})
                    with pdfplumber.open(working) as doc:
                        self.assertNotIn(
                            "Second page must be corrected",
                            doc.pages[1].extract_text(),
                        )

    def test_sparse_pages_do_not_get_invented_rotation(self):
        from ragcore import orientation
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "blank.pdf"
            builder = canvas.Canvas(str(source))
            builder.drawString(30, 200, "a")
            builder.save()
            with pdfplumber.open(source) as pdf:
                self.assertIsNone(native_rotation(pdf.pages[0]))
            with patch.object(orientation, "scanned_rotation", return_value=None):
                self.assertEqual(detect_rotations(source), {})
            with patch.object(orientation, "scanned_rotation", return_value=270):
                self.assertEqual(detect_rotations(source), {1: 270})


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "Isolated PostgreSQL CI only")
class OrientationDatabaseTests(unittest.TestCase):
    def test_storage_reindex_and_image_follow_normalized_pages(self):
        from ragcore import store
        from ragcore.page_image import get_page_image

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            file = root / "wrong-orientation.pdf"
            sample_pdf(file)
            source_sha = hashlib.sha256(file.read_bytes()).hexdigest()
            with patch.dict(os.environ, {
                "RAG_AUTO_ROTATE_ENABLED": "1", "RAG_OCR_ENABLED": "0",
            }):
                with store.connect() as conn:
                    store.init_schema(conn)
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM rag_documents")
                    indexed = store.scan(conn, root, use_vectors=False)
                    self.assertEqual(indexed["updated"], 1)
                    self.assertEqual(indexed["errors"], 0)
                    item = store.list_documents(conn)["documents"][0]
                    doc_id = item["document_id"]
                    self.assertEqual(item["rotated_pages"], 3)
                    self.assertEqual(item["orientation_version"], 1)
                    details = store.document_status(conn, doc_id)
                    self.assertEqual(details["page_rotations"],
                                     {"2": 90, "3": 180, "4": 270})
                    self.assertEqual(details["status"], "indexed_complete")
                    text = store.get_text(conn, doc_id)[1]
                    self.assertIn("Second page must be corrected", text)
                    self.assertIn("Third page must be corrected", text)
                    self.assertIn("Fourth page must be corrected", text)
                    image = get_page_image(conn, root, doc_id, 2)
                    self.assertEqual(image["orientation_corrected_clockwise"], 90)
                    self.assertLess(image["image_width"], image["image_height"])
                    self.assertTrue(image["source_verified"])
                    self.assertEqual(store.scan(conn, root, use_vectors=False)["unchanged"], 1)

                    # Force a one-time orientation upgrade on existing source.
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE rag_documents SET orientation_version=0 WHERE id=%s",
                            (doc_id,),
                        )
                    self.assertEqual(store.scan(conn, root, use_vectors=False)["updated"], 1)
                    self.assertEqual(store.document_status(conn, doc_id)["rotated_pages"], 3)

            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), source_sha)

            # Operator can disable auto-rotation; reindex then clears metadata.
            with patch.dict(os.environ, {
                "RAG_AUTO_ROTATE_ENABLED": "0", "RAG_OCR_ENABLED": "0",
            }):
                with store.connect() as conn:
                    self.assertEqual(store.scan(conn, root, use_vectors=False)["updated"], 1)
                    details = store.document_status(conn, doc_id)
                    self.assertEqual(details["page_rotations"], {})
                    self.assertEqual(details["orientation_version"], 0)
                    image = get_page_image(conn, root, doc_id, 2)
                    self.assertEqual(image["orientation_corrected_clockwise"], 0)
                    self.assertGreater(image["image_width"], image["image_height"])
