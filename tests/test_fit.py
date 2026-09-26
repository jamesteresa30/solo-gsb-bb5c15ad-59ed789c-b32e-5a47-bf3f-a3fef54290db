"""年代优选（POST /fit）单元测试。"""

from __future__ import annotations

import time
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.audit import AuditValidationError
from app.fit import fit
from app.main import app
from app.schemas import FitRequest


def _s(sid: str, youngest: int, oldest: int) -> dict:
    return {"id": sid, "youngest_ma": youngest, "oldest_ma": oldest}


def _r(older: str, younger: str) -> dict:
    return {"older_id": older, "younger_id": younger}


def _p(sid: str, target: int, weight: int = 1) -> dict:
    return {"id": sid, "target_ma": target, "weight": weight}


class OptimalTests(unittest.TestCase):
    def test_single_stratum_hits_target(self):
        result = fit(
            {
                "strata": [_s("a", 100, 110)],
                "relations": [],
                "preferences": [_p("a", 105, 3)],
            }
        )
        self.assertEqual(
            result, {"status": "optimal", "optimal_value": 0, "ages": {"a": 105}}
        )

    def test_target_outside_interval_clamps_to_bound(self):
        result = fit(
            {
                "strata": [_s("a", 100, 110)],
                "relations": [],
                "preferences": [_p("a", 50, 2)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["ages"], {"a": 100})
        self.assertEqual(result["optimal_value"], 2 * (100 - 50))

    def test_chain_forces_deviation(self):
        # a -> b，目标同为 5：T=0 不可行，T=1 可行。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 5), _p("b", 5)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 1)
        self.assertEqual(result["ages"], {"a": 5, "b": 4})

    def test_weighted_tradeoff(self):
        # a 权重 2 期望 0，b 权重 1 期望 10，a -> b。
        # 最优 T=8：a=3（偏差 6）、b=2（偏差 8）。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 0, 2), _p("b", 10, 1)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 8)
        self.assertEqual(result["ages"], {"a": 3, "b": 2})

    def test_lexicographically_smallest_among_ties(self):
        # a -> b，目标 3/7：T=3 时 a∈[5,6]、b∈[4,5] 均最优，
        # 按编号升序 (a, b) 取字典序最小 -> (5, 4)。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 3), _p("b", 7)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 3)
        self.assertEqual(result["ages"], {"a": 5, "b": 4})

    def test_tie_break_follows_id_order_not_input_order(self):
        # 同上但提交顺序颠倒；编号升序仍是 (a, b)，结果不变。
        result = fit(
            {
                "strata": [_s("b", 0, 10), _s("a", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("b", 7), _p("a", 3)],
            }
        )
        self.assertEqual(result["ages"], {"a": 5, "b": 4})

    def test_negative_ages_supported(self):
        result = fit(
            {
                "strata": [_s("a", -10, 0), _s("b", -10, 0)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", -3), _p("b", -8)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 0)
        self.assertEqual(result["ages"], {"a": -3, "b": -8})

    def test_heavier_stratum_stays_closer_to_target(self):
        # 目标同为 60：重权的 a 保持不动，轻权的 b 被迫下移 1。
        result = fit(
            {
                "strata": [_s("a", 0, 100), _s("b", 0, 100)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 60, 100), _p("b", 60, 1)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["ages"], {"a": 60, "b": 59})
        self.assertEqual(result["optimal_value"], 1)

    def test_large_scale_chain_is_fast_and_exact(self):
        # 200 层链、1000 条关系（含重复）、区间 ±10^12：不得逐年代枚举。
        n = 200
        ids = [f"s{i:03d}" for i in range(n)]
        relations = [_r(ids[i], ids[i + 1]) for i in range(n - 1)]
        relations += [_r(ids[0], ids[1])] * (1000 - len(relations))
        started = time.monotonic()
        result = fit(
            {
                "strata": [_s(sid, -(10**12), 10**12) for sid in ids],
                "relations": relations,
                "preferences": [_p(sid, 0) for sid in ids],
            }
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "optimal")
        # 200 个连续整数居中于 0：字典序最小向量为 99, 98, ..., -100。
        self.assertEqual(result["optimal_value"], 100)
        self.assertEqual(
            result["ages"], {sid: 99 - i for i, sid in enumerate(ids)}
        )
        self.assertLess(elapsed, 5.0)

    def test_extreme_values_within_limits(self):
        big = 10**12
        result = fit(
            {
                "strata": [_s("a", 5, big), _s("b", -big, 0)],
                "relations": [],
                "preferences": [_p("a", 0, 10**6), _p("b", big, 10**6)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["ages"], {"a": 5, "b": 0})
        # b 最多只能取 0，偏差 10^12 × 10^6 = 10^18，主导目标值。
        self.assertEqual(result["optimal_value"], 10**18)


class PassthroughTests(unittest.TestCase):
    def test_cycle_result_matches_audit(self):
        result = fit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c"), _r("c", "a")],
                "preferences": [_p(s, 50) for s in ("a", "b", "c")],
            }
        )
        self.assertEqual(result["status"], "cycle")
        self.assertEqual(result["cycle_path"], ["a", "b", "c", "a"])

    def test_conflict_result_matches_audit(self):
        result = fit(
            {
                "strata": [_s("a", 5, 5), _s("b", 5, 5)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 5), _p("b", 5)],
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b"])
        self.assertEqual(
            result["tight_intervals"]["a"],
            {"youngest_ma": 6, "oldest_ma": 5},
        )


class PreferenceValidationTests(unittest.TestCase):
    def _errors(self, data: dict) -> list[str]:
        try:
            fit(data)
        except AuditValidationError as exc:
            return exc.errors
        self.fail("应当抛出 AuditValidationError")

    def test_missing_preference_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5)],
            }
        )
        self.assertTrue(any("缺少" in e and "b" in e for e in errors))

    def test_duplicate_preference_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("a", 6)],
            }
        )
        self.assertTrue(any("重复" in e for e in errors))

    def test_unknown_preference_id_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("x", 5)],
            }
        )
        self.assertTrue(any("未提交" in e for e in errors))

    def test_all_preference_problems_collected_together(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("a", 6), _p("x", 1)],
            }
        )
        joined = "\n".join(errors)
        self.assertIn("重复", joined)
        self.assertIn("未提交", joined)
        self.assertIn("缺少", joined)


class FitRequestSchemaTests(unittest.TestCase):
    """规模与数值上限在模型层拒绝（路由层映射为 HTTP 422）。"""

    def _payload(self, **overrides) -> dict:
        payload = {
            "strata": [_s("a", 0, 10)],
            "relations": [],
            "preferences": [_p("a", 5)],
        }
        payload.update(overrides)
        return payload

    def _assert_invalid(self, **overrides):
        with self.assertRaises(ValidationError):
            FitRequest.model_validate(self._payload(**overrides))

    def test_too_many_strata_rejected(self):
        self._assert_invalid(
            strata=[_s(f"s{i}", 0, 10) for i in range(201)],
            preferences=[_p(f"s{i}", 5) for i in range(201)],
        )

    def test_too_many_relations_rejected(self):
        self._assert_invalid(
            strata=[_s("a", 0, 10**12), _s("b", 0, 10**12)],
            relations=[_r("a", "b")] * 1001,
            preferences=[_p("a", 5), _p("b", 5)],
        )

    def test_age_beyond_limit_rejected(self):
        self._assert_invalid(strata=[_s("a", 0, 10**12 + 1)])
        self._assert_invalid(strata=[_s("a", -(10**12) - 1, 0)])

    def test_target_beyond_limit_rejected(self):
        self._assert_invalid(preferences=[_p("a", 10**12 + 1)])
        self._assert_invalid(preferences=[_p("a", -(10**12) - 1)])

    def test_weight_must_be_positive_and_within_limit(self):
        self._assert_invalid(preferences=[_p("a", 5, 0)])
        self._assert_invalid(preferences=[_p("a", 5, 10**6 + 1)])

    def test_non_integer_values_rejected(self):
        self._assert_invalid(preferences=[{"id": "a", "target_ma": 1.5, "weight": 1}])
        self._assert_invalid(strata=[{"id": "a", "youngest_ma": 0, "oldest_ma": 2.5}])

    def test_preferences_field_is_required(self):
        payload = self._payload()
        del payload["preferences"]
        with self.assertRaises(ValidationError):
            FitRequest.model_validate(payload)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_fit_optimal_via_api(self):
        response = self.client.post(
            "/fit",
            json={
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 5), _p("b", 5)],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "optimal", "optimal_value": 1, "ages": {"a": 5, "b": 4}},
        )

    def test_fit_cycle_and_conflict_via_api(self):
        cycle = self.client.post(
            "/fit",
            json={
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b"), _r("b", "a")],
                "preferences": [_p("a", 5), _p("b", 5)],
            },
        )
        self.assertEqual(cycle.status_code, 200)
        self.assertEqual(cycle.json()["status"], "cycle")

        conflict = self.client.post(
            "/fit",
            json={
                "strata": [_s("a", 5, 5), _s("b", 5, 5)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 5), _p("b", 5)],
            },
        )
        self.assertEqual(conflict.status_code, 200)
        self.assertEqual(conflict.json()["status"], "conflict")

    def test_fit_business_validation_returns_422(self):
        response = self.client.post(
            "/fit",
            json={
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("a", 6)],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("重复", str(response.json()["detail"]))

    def test_fit_schema_violations_return_422(self):
        base = {
            "strata": [_s("a", 0, 10)],
            "relations": [],
            "preferences": [_p("a", 5)],
        }
        for bad in (
            {**base, "preferences": [_p("a", 5, 0)]},
            {**base, "preferences": [_p("a", 10**12 + 1)]},
            {**base, "strata": [_s("a", 0, 10**12 + 1)]},
            {k: v for k, v in base.items() if k != "preferences"},
        ):
            self.assertEqual(
                self.client.post("/fit", json=bad).status_code, 422, bad
            )

    def test_audit_still_compatible(self):
        response = self.client.post(
            "/audit",
            json={
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "feasible")
        self.assertEqual(response.json()["oldest_to_youngest"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
