"""HTTP 接口层测试。

依赖 fastapi/httpx（镜像内已安装）；本地无依赖时自动跳过，
核心逻辑测试见 tests/test_audit.py 与 tests/test_fit.py。
"""

from __future__ import annotations

import unittest

try:  # pragma: no cover - 本地环境可能未安装 Web 依赖
    from fastapi.testclient import TestClient

    from app.main import app
except ImportError:  # pragma: no cover
    TestClient = None


def _s(sid: str, youngest: int, oldest: int) -> dict:
    return {"id": sid, "youngest_ma": youngest, "oldest_ma": oldest}


def _p(sid: str, target: int, weight: int = 1) -> dict:
    return {"id": sid, "target_ma": target, "weight": weight}


@unittest.skipIf(TestClient is None, "未安装 fastapi/httpx，跳过接口测试")
class FitEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _post_fit(self, payload: dict):
        return self.client.post("/fit", json=payload)

    def test_fit_optimal(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [{"older_id": "a", "younger_id": "b"}],
                "preferences": [_p("a", 0), _p("b", 10)],
            }
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "optimal")
        self.assertEqual(body["optimal_value"], 6)
        self.assertEqual(body["ages"], {"a": 5, "b": 4})

    def test_fit_cycle_passthrough(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [
                    {"older_id": "a", "younger_id": "b"},
                    {"older_id": "b", "younger_id": "a"},
                ],
                "preferences": [_p("a", 0), _p("b", 0)],
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cycle")

    def test_fit_conflict_passthrough(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 5, 5), _s("b", 5, 5)],
                "relations": [{"older_id": "a", "younger_id": "b"}],
                "preferences": [_p("a", 5), _p("b", 5)],
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "conflict")

    def test_missing_preference_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5)],
            }
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("缺少偏好", str(resp.json()["detail"]))

    def test_duplicate_preference_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("a", 6)],
            }
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("重复", str(resp.json()["detail"]))

    def test_unknown_preference_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("ghost", 5)],
            }
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("未提交", str(resp.json()["detail"]))

    def test_weight_out_of_range_returns_422(self):
        for bad_weight in (0, -1, 10**6 + 1):
            resp = self._post_fit(
                {
                    "strata": [_s("a", 0, 10)],
                    "relations": [],
                    "preferences": [_p("a", 5, bad_weight)],
                }
            )
            self.assertEqual(resp.status_code, 422, bad_weight)

    def test_target_out_of_range_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 10**12 + 1)],
            }
        )
        self.assertEqual(resp.status_code, 422)

    def test_age_out_of_range_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", -(10**12) - 1, 10)],
                "relations": [],
                "preferences": [_p("a", 5)],
            }
        )
        self.assertEqual(resp.status_code, 422)

    def test_too_many_strata_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s(f"s{i}", 0, 10) for i in range(201)],
                "relations": [],
                "preferences": [_p(f"s{i}", 5) for i in range(201)],
            }
        )
        self.assertEqual(resp.status_code, 422)

    def test_too_many_relations_returns_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [
                    {"older_id": "a", "younger_id": "b"}
                ]
                * 1001,
                "preferences": [_p("a", 5), _p("b", 5)],
            }
        )
        self.assertEqual(resp.status_code, 422)

    def test_non_integer_values_return_422(self):
        resp = self._post_fit(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [{"id": "a", "target_ma": 5.5, "weight": 1}],
            }
        )
        self.assertEqual(resp.status_code, 422)


@unittest.skipIf(TestClient is None, "未安装 fastapi/httpx，跳过接口测试")
class AuditCompatibilityTests(unittest.TestCase):
    """确认 /audit 行为保持不变。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_audit_still_feasible(self):
        resp = self.client.post(
            "/audit",
            json={
                "strata": [_s("a", 100, 110), _s("b", 100, 110)],
                "relations": [{"older_id": "a", "younger_id": "b"}],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "feasible")
        self.assertEqual(body["oldest_to_youngest"], ["a", "b"])

    def test_audit_validation_still_422(self):
        resp = self.client.post(
            "/audit",
            json={
                "strata": [_s("a", 0, 10)],
                "relations": [{"older_id": "a", "younger_id": "a"}],
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
