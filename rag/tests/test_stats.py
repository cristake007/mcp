"""Read-only corpus-statistics tests, independent of semantic embeddings."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragcore.stats import normalize, phrase_pattern, validate


class LiteralStatsTests(unittest.TestCase):
    def test_romanian_accents_case_boundaries_and_literal_phrases(self):
        raw = normalize("AUTORIZAȚIE autorizatie AUTORIZATIEI ISO 9001 ISO 90010")
        matches = list(phrase_pattern("autorizație").finditer(raw))
        self.assertEqual(len(matches), 2)
        self.assertEqual(len(list(phrase_pattern("iso 9001").finditer(raw))), 1)
        self.assertEqual(len(list(phrase_pattern("autorizatiei").finditer(raw))), 1)

    def test_invalid_parameters(self):
        for invalid in ("", " ", "x" * 121, 12, None):
            with self.assertRaises(ValueError):
                validate(invalid, None, "all", 10)
        with self.assertRaises(ValueError):
            validate("ISO", "", "all", 10)
        with self.assertRaises(ValueError):
            validate("ISO", "audit", "some", 10)
        with self.assertRaises(ValueError):
            validate("ISO", None, "all", 21)
        with self.assertRaises(ValueError):
            validate("ISO", None, "all", True)


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "Requires isolated pgvector PostgreSQL")
class CorpusStatsDatabaseTests(unittest.TestCase):
    def test_entire_corpus_and_same_article_vs_same_document(self):
        from ragcore import store
        from ragcore.stats import corpus_stats

        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            (folder / "a.txt").write_text(
                "Art. 1 (1) Autorizare și suspendare.\n"
                "Art. 2 (1) Verificare periodică.\n", encoding="utf-8",
            )
            (folder / "b.md").write_text(
                "Art. 1 (1) Autorizare.\n"
                "Art. 2 (1) Suspendare.\n", encoding="utf-8",
            )
            (folder / "c.txt").write_text(
                "Art. 3 (1) Suspendare și control.\n", encoding="utf-8",
            )
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                self.assertEqual(store.scan(conn, folder, use_vectors=False)["updated"], 3)

                result = corpus_stats(
                    conn, "AUTORIZARE", "suspendare", mode="all",
                    examples_limit=1,
                )
                self.assertEqual(result["documents_scanned"], 3)
                self.assertEqual(result["articles_detected"], 5)
                self.assertEqual(result["matching_documents"], 2)
                self.assertEqual(result["matching_articles"], 1)
                self.assertEqual(result["documents_with_matching_articles"], 1)
                self.assertEqual(result["occurrences_per_term"], [2, 3])
                self.assertEqual(len(result["document_examples"]), 1)
                self.assertEqual(len(result["article_examples"]), 1)
                self.assertTrue(result["document_examples_truncated"])
                self.assertFalse(result["article_examples_truncated"])
                self.assertTrue(result["exhaustive_counts"])

                example = result["article_examples"][0]
                self.assertEqual(example["path"], "a.txt")
                self.assertEqual(example["article"], "1")
                _, passage = store.get_text(
                    conn, example["document_id"], example["char_start"],
                    example["char_end"],
                )
                self.assertIn("Autorizare și suspendare", passage)
                self.assertNotIn("Verificare periodică", passage)

                none = corpus_stats(conn, "domeniu inexistent", examples_limit=0)
                self.assertEqual(none["matching_documents"], 0)
                self.assertEqual(none["matching_articles"], 0)
                self.assertEqual(none["occurrences_per_term"], [0])

                either = corpus_stats(
                    conn, "autorizare", "suspendare", mode="any",
                    examples_limit=0,
                )
                self.assertEqual(either["matching_documents"], 3)
                self.assertEqual(either["matching_articles"], 4)
                self.assertEqual(either["occurrences_per_term"], [2, 3])
                self.assertEqual(either["article_examples"], [])
                self.assertTrue(either["article_examples_truncated"])
                self.assertTrue(either["document_examples_truncated"])

                accents = corpus_stats(conn, "și", examples_limit=0)
                self.assertEqual(accents["matching_documents"], 2)


if __name__ == "__main__":
    unittest.main()
