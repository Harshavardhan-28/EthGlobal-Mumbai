import os
import tempfile
from pathlib import Path

from app.adapters import ArcAdapter, HederaAdapter
from app.service import Orchestrator
from app.store import Store

START = 1_790_000_000


class Clock:
    def __init__(self, now: int = START) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def make_orchestrator(tmp: str, clock: Clock | None = None, hedera: HederaAdapter | None = None) -> Orchestrator:
    os.environ["HEDERA_PAYMENT_MODE"] = "simulated"
    os.environ["ARC_EXECUTION_MODE"] = "simulated"
    os.environ.pop("HEDERA_AUDIT_TOPIC_ID", None)
    hedera = hedera or HederaAdapter()
    hedera._ledger_path = Path(tmp) / "sim-hedera.json"
    return Orchestrator(Store(f"sqlite:///{tmp}/test.db"), hedera, ArcAdapter(), clock or Clock())


class TempDirCase:
    def setUp(self) -> None:  # noqa: D401 - unittest hook
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.orc = make_orchestrator(self.tempdir.name, self.clock)

    def tearDown(self) -> None:
        self.orc.store.close()
        self.tempdir.cleanup()
