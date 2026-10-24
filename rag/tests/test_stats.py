"""Read-only corpus-statistics tests, independent of semantic embeddings."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragcore.stats import normalize, phrase_pattern, validate
from ragcore.stemming import count_phrases, stem_sequence


class LiteralStatsTests(unittest.TestCase):
    def test_romanian_accents_case_boundaries_and_literal_phrases(self):
        raw = normalize("AUTORIZAȚIE autorizatie AUTORIZATIEI ISO 9001 ISO 90010")
        matches = list(phrase_pattern("autorizație").finditer(raw))
        self.assertEqual(len(matches), 2)
        self.assertEqual(len(list(phrase_pattern("iso 9001").finditer(raw))), 1)
        self.assertEqual(len(list(phrase_pattern("autorizatiei").finditer(raw))), 1)

    def test_linguistic_romanian_forms_do_not_match_unrelated_words(self):
        phrase = stem_sequence("suspendare")
        self.assertEqual(phrase, stem_sequence("suspendarea"))
        sample = "SUSPENDAREA autorizației; suspendare; nesuspendare"
        self.assertEqual(count_phrases(sample, [phrase]), [2])

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
        with self.assertRaises(ValueError):
            validate("ISO", None, "all", 10, "unknown")


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "Requires isolated pgvector PostgreSQL")
class CorpusStatsDatabaseTests(unittest.TestCase):
    def test_linguistic_vs_exact_statistic(self):
        from ragcore import store
        from ragcore.stats import corpus_stats

        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            (folder / "a.txt").write_text(
                "Art. 1 (1) Autorizarea este urmată de suspendarea dreptului.\\n",
                encoding="utf-8",
            )
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                self.assertEqual(store.scan(conn, folder, use_vectors=False)["updated"], 1)
                linguistic = corpus_stats(conn, "autorizare", "suspendare")
                literal = corpus_stats(
                    conn, "autorizare", "suspendare", match_type="exact"
                )
                self.assertEqual(linguistic["matching_documents"], 1)
                self.assertEqual(linguistic["matching_articles"], 1)
                self.assertEqual(linguistic["occurrences_per_term"], [1, 1])
                self.assertEqual(literal["matching_documents"], 0)
                self.assertEqual(literal["matching_articles"], 0)
                self.assertEqual(literal["occurrences_per_term"], [0, 0])

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


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "Requires isolated pgvector PostgreSQL")
class CorpusStatsAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_stats_endpoint_authorization_and_complete_counts(self):
        import httpx
        from ragcore import store
        from ragcore.api import app

        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            (folder / "one.txt").write_text(
                "Art. 1 (1) Autorizare și suspendare.", encoding="utf-8"
            )
            (folder / "two.md").write_text(
                "Art. 2 (1) Suspendare.", encoding="utf-8"
            )
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                self.assertEqual(store.scan(conn, folder, use_vectors=False)["updated"], 2)

            previous = os.environ.get("RAG_API_TOKEN")
            token = "unit-test-local-secret-token-123456789abcdef"
            os.environ["RAG_API_TOKEN"] = token
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://rag-api",
                ) as client:
                    denied = await client.post("/stats", json={"term": "autorizare"})
                    self.assertEqual(denied.status_code, 401)
                    headers = {"Authorization": "Bearer " + token}
                    response = await client.post(
                        "/stats", headers=headers,
                        json={"term": "autorizare", "second_term": "suspendare",
                              "examples_limit": 0},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()
                    self.assertEqual(data["documents_scanned"], 2)
                    self.assertEqual(data["matching_documents"], 1)
                    self.assertEqual(data["matching_articles"], 1)
                    self.assertEqual(data["occurrences_per_term"], [1, 2])
                    self.assertEqual(data["document_examples"], [])
                    self.assertTrue(data["exhaustive_counts"])
                    for invalid in (
                        {"term": ""},
                        {"term": 15},
                        {"term": "ISO", "mode": "neither"},
                        {"term": "ISO", "examples_limit": 30},
                        {"term": "ISO", "examples_limit": True},
                    ):
                        rejected = await client.post("/stats", json=invalid, headers=headers)
                        self.assertEqual(rejected.status_code, 400, rejected.text)
                    self.assertEqual((await client.get("/stats", headers=headers)).status_code, 405)
            finally:
                if previous is None:
                    os.environ.pop("RAG_API_TOKEN", None)
                else:
                    os.environ["RAG_API_TOKEN"] = previous


if __name__ == "__main__":
    unittest.main()
