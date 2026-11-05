import os
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.helpers import make_orchestrator


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite:///{self.tempdir.name}/api.db"
        from app import main

        self.main = main
        main.orchestrator = make_orchestrator(self.tempdir.name)
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.main.orchestrator.store.close()
        self.tempdir.cleanup()

    def post(self, path: str, body: dict | None = None, key: str | None = None):
        return self.client.post(path, json=body, headers={"Idempotency-Key": key or str(uuid4())})

    def test_plan_api_surface_end_to_end(self) -> None:
        self.assertEqual(self.client.get("/api/demo/retailco-net30").json()["lifecycle"], "DRAFT")
        self.assertEqual(self.post("/api/receivables/retailco-net30/verify").status_code, 200)
        self.assertEqual(self.post("/api/receivables/retailco-net30/tokenize").json()["state"]["lifecycle"], "TOKENIZED")
        funded = self.post("/api/facilities/retailco-net30/fund").json()["state"]
        self.assertEqual(funded["facility"]["funded"], "6000.00")
        created = self.post("/api/spend-intents", {"agent": "research", "merchant": "Briefly Data", "amount": "0.25"}).json()["intent"]
        executed = self.post(f"/api/spend-intents/{created['id']}/execute").json()
        self.assertEqual(executed["intent"]["status"], "SETTLED")
        rejected = self.post("/api/spend-intents", {"agent": "compute", "merchant": "Northstar Cloud", "amount": "5500"}).json()["intent"]
        self.assertEqual(rejected["reason"], "AGENT_BUDGET_EXCEEDED")
        self.assertEqual(self.post(f"/api/spend-intents/{rejected['id']}/execute").status_code, 409)
        repaid = self.post("/api/facilities/retailco-net30/repay", {"amount": "10000"}).json()["state"]
        self.assertEqual(repaid["lifecycle"], "REPAID")
        log = self.client.get("/api/receivables/retailco-net30/log").json()["items"]
        self.assertIn("facility.repaid", [e["type"] for e in log])

    def test_mutations_are_idempotent(self) -> None:
        first = self.post("/api/demo/retailco-net30/actions", {"action": "verify_invoice"}, key="same-key")
        second = self.post("/api/demo/retailco-net30/actions", {"action": "verify_invoice"}, key="same-key")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        third = self.post("/api/demo/retailco-net30/actions", {"action": "verify_invoice"})
        self.assertEqual(third.status_code, 409)

    def test_unauthorized_transition_returns_409(self) -> None:
        response = self.post("/api/facilities/retailco-net30/fund")
        self.assertEqual(response.status_code, 409)
        self.assertIn("DRAFT", response.json()["detail"])

    def test_reset(self) -> None:
        self.post("/api/demo/retailco-net30/actions", {"action": "verify_invoice"})
        view = self.client.post("/api/demo/retailco-net30/reset").json()
        self.assertEqual(view["lifecycle"], "DRAFT")


if __name__ == "__main__":
    unittest.main()
