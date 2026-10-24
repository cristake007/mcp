"""Lightweight polling service for the *standalone* RAG Docker stack.

No watch-dog daemon or MCP dependency. Never parses a file until it has
appeared unchanged in consecutive scans and its mtime is sufficiently old.
"""
import logging
import os
import signal
import threading
import time
from pathlib import Path

from . import store
from .embeddings import enabled

log = logging.getLogger("rag.indexer")


def integer_setting(name: str, default: int, low: int, high: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not low <= parsed <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return parsed


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """A failed file stat aborts the entire cycle, preventing accidental prune."""
    state = {}
    for relative, path in store.walk_documents(folder):
        stat = path.stat()
        state[relative] = (stat.st_size, stat.st_mtime_ns)
    return state


def stable_paths(
    current: dict[str, tuple[int, int]],
    previous: dict[str, tuple[int, int]] | None,
    *,
    settle_seconds: int,
    now_ns: int,
) -> set[str]:
    """Only the same size+mtime in two scans AND older than settle delay."""
    if previous is None:
        return set()
    return {
        name for name, metadata in current.items()
        if previous.get(name) == metadata
        and now_ns - metadata[1] >= settle_seconds * 1_000_000_000
    }


class IndexWatcher:
    def __init__(self, folder: Path, *, interval: int = 60,
                 settle: int = 20, auto_prune: bool = True,
                 use_vectors: bool = True):
        self.folder = folder
        self.interval = interval
        self.settle = settle
        self.auto_prune = auto_prune
        self.use_vectors = use_vectors
        self.previous: dict[str, tuple[int, int]] | None = None
        self.stop_event = threading.Event()

    def tick(self) -> dict | None:
        current = snapshot(self.folder)
        eligible = stable_paths(
            current, self.previous,
            settle_seconds=self.settle,
            now_ns=time.time_ns(),
        )
        complete_cycle = self.previous is not None and current == self.previous
        pending = len(current) - len(eligible)
        # Deletion cleanup only on TWO identical, NONEMPTY folder snapshots.
        # Never prune while files are still being copied/updated or when the
        # document folder may be missing/unmounted.
        prune = self.auto_prune and complete_cycle and bool(current)

        # If no eligible files and no stable deletion candidate, no DB access.
        if eligible or prune:
            try:
                with store.connect() as conn:
                    result = store.scan(
                        conn, self.folder,
                        use_vectors=self.use_vectors,
                        prune=prune,
                        only_paths=eligible,
                    )
            except Exception:
                # Do not mark failed snapshot as confirmed stable for pruning.
                self.previous = None
                raise
            if result["errors"]:
                log.warning("Indexing errors: %d; failed files will retry", result["errors"])
            if any(result[key] for key in ("updated", "errors", "deleted", "empty")):
                log.info(
                    "Scan: updated=%d skipped=%d errors=%d deleted=%d waiting=%d",
                    result["updated"], result["unchanged"], result["errors"],
                    result["deleted"], pending,
                )
        else:
            result = None
        self.previous = current
        return result

    def run(self) -> None:
        log.info(
            "RAG indexer started: interval=%ds settle=%ds auto_prune=%s "
            "semantic_embeddings=%s directory=%s",
            self.interval, self.settle, self.auto_prune,
            self.use_vectors, self.folder,
        )
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Index scan failed; will retry at next interval")
                self.previous = None
            self.stop_event.wait(self.interval)
        log.info("RAG indexer stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    watcher = IndexWatcher(
        Path(os.getenv("RAG_DOCUMENTS", "/documents")),
        interval=integer_setting("RAG_SCAN_INTERVAL_SECONDS", 60, 10, 86400),
        settle=integer_setting("RAG_SETTLE_SECONDS", 20, 0, 3600),
        auto_prune=os.getenv("RAG_AUTO_PRUNE", "1").strip().lower()
        not in ("0", "false", "no"),
        use_vectors=enabled(),
    )
    signal.signal(signal.SIGTERM, lambda *_: watcher.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: watcher.stop_event.set())
    watcher.run()


if __name__ == "__main__":
    main()
