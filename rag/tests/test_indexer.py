"""Automatic RAG folder indexing: stability, deletion safety, and idempotence."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragcore.indexer import IndexWatcher, snapshot, stable_paths


class WatcherUnitTests(unittest.TestCase):
    def test_file_must_be_stable_across_two_cycles_and_old_enough(self):
        current = {"docs/a.pdf": (234, 1_000_000_000)}
        self.assertEqual(stable_paths(current, None, settle_seconds=0, now_ns=10_000_000_000), set())
        self.assertEqual(stable_paths(current, current, settle_seconds=20, now_ns=10_000_000_000), set())
        self.assertEqual(stable_paths(current, current, settle_seconds=20, now_ns=25_000_000_000), {"docs/a.pdf"})
        self.assertEqual(stable_paths({"docs/a.pdf": (235, 1_000_000_000)}, current,
                                      settle_seconds=0, now_ns=25_000_000_000), set())

    def test_recursive_snapshot_skips_unknown_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "nested").mkdir()
            (root / "nested" / "file.txt").write_text("Document", encoding="utf-8")
            (root / "nested" / "image.png").write_bytes(b"fake")
            (root / "outside.pdf").symlink_to(root / "nested" / "file.txt")
            state = snapshot(root)
            self.assertEqual(set(state), {"nested/file.txt"})

    def test_watcher_does_not_prune_or_scan_empty_mount(self):
        with tempfile.TemporaryDirectory() as scratch:
            watcher = IndexWatcher(Path(scratch), settle=0)
            with patch("ragcore.indexer.store.connect") as connect:
                self.assertIsNone(watcher.tick())
                self.assertIsNone(watcher.tick())
                connect.assert_not_called()


@unittest.skipUnless(os.environ.get("RAG_TEST_DB") == "1", "Needs isolated PostgreSQL test DB")
class WatcherDatabaseTests(unittest.TestCase):
    def test_add_update_and_prune_with_two_stable_snapshots(self):
        from ragcore import store

        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            first = folder / "existing.txt"
            second = folder / "nested" / "new.md"
            second.parent.mkdir()
            first.write_text("ISO 9001 - test", encoding="utf-8")

            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")

            watcher = IndexWatcher(folder, settle=0, use_vectors=False)
            self.assertIsNone(watcher.tick(), "First snapshot never indexes partial copies")
            self.assertEqual(watcher.tick()["updated"], 1)
            self.assertEqual(watcher.tick()["unchanged"], 1)

            second.write_text("ISO 14001 - updated", encoding="utf-8")
            self.assertEqual(watcher.tick()["updated"], 0, "New file waits for next stable snapshot")
            self.assertEqual(watcher.tick()["updated"], 1)
            first.write_text("ISO 9001 - changed", encoding="utf-8")
            watcher.tick()
            self.assertEqual(watcher.tick()["updated"], 1)

            first.unlink()
            self.assertEqual(watcher.tick()["deleted"], 0)
            self.assertEqual(watcher.tick()["deleted"], 1)

            with store.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT path,raw_text FROM rag_documents")
                    self.assertEqual(
                        [(row["path"], row["raw_text"]) for row in cur.fetchall()],
                        [("nested/new.md", "ISO 14001 - updated")],
                    )
                self.assertEqual(store.verify_index(conn)["coverage_errors"], [])

            # Empty mount is treated as possible operator/mount error, not
            # an instruction to delete the entire indexed corpus.
            second.unlink()
            watcher.tick()
            watcher.tick()
            with store.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) AS n FROM rag_documents")
                    self.assertEqual(cur.fetchone()["n"], 1)

    def test_changed_during_parse_is_not_committed(self):
        from ragcore import store
        from ragcore.parsing import parse_file

        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            path = folder / "partial.txt"
            path.write_text("Initial legal text", encoding="utf-8")
            with store.connect() as conn:
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM rag_documents")
                self.assertEqual(store.scan(conn, folder, use_vectors=False)["updated"], 1)

                def mutate_during_parse(p):
                    parsed = parse_file(p)
                    p.write_text("Changed during parse", encoding="utf-8")
                    return parsed

                path.write_text("New document under copy", encoding="utf-8")
                with patch("ragcore.store.parse_file", side_effect=mutate_during_parse):
                    report = store.scan(conn, folder, use_vectors=False)
                self.assertEqual(report["errors"], 1)
                self.assertEqual(report["updated"], 0)
                with conn.cursor() as cur:
                    cur.execute("SELECT raw_text FROM rag_documents WHERE path=%s", ("partial.txt",))
                    self.assertEqual(cur.fetchone()["raw_text"], "Initial legal text")

    def test_advisory_lock_prevents_simultaneous_scans(self):
        from ragcore import store

        with tempfile.TemporaryDirectory() as scratch, store.connect() as lock_owner:
            with lock_owner.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(8460153001)")
            try:
                with store.connect() as other:
                    with self.assertRaisesRegex(RuntimeError, "Another RAG indexing"):
                        store.scan(other, Path(scratch), use_vectors=False)
            finally:
                with lock_owner.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(8460153001)")


if __name__ == "__main__":
    unittest.main()
