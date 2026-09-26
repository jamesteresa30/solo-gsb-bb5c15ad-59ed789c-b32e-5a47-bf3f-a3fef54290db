"""POST /fit 核心拟合逻辑单元测试（纯 Python，不依赖 FastAPI）。"""

from __future__ import annotations

import itertools
import random
import unittest

from app.audit import AuditValidationError
from app.fit import fit


def _s(sid: str, youngest: int, oldest: int) -> dict:
    return {"id": sid, "youngest_ma": youngest, "oldest_ma": oldest}


def _r(older: str, younger: str) -> dict:
    return {"older_id": older, "younger_id": younger}


def _p(sid: str, target: int, weight: int = 1) -> dict:
    return {"id": sid, "target_ma": target, "weight": weight}


class OptimalTests(unittest.TestCase):
    def test_zero_deviation_when_targets_feasible(self):
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("b", 7)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 0)
        self.assertEqual(result["ages"], {"a": 5, "b": 7})

    def test_relation_forces_deviation_and_tie_break(self):
        # a 比 b 古老：a >= b+1；目标 a=0、b=10 不可兼得。
        # 最优值 6 有两个最优解 (a,b) = (5,4)/(6,5)，取向量字典序最小者。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 0), _p("b", 10)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 6)
        self.assertEqual(result["ages"], {"a": 5, "b": 4})

    def test_weights_shift_deviation_to_low_weight_stratum(self):
        # b 权重为 3：偏差尽量落在 a 上。最优 max(a, 3×(10−b))。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", 0, 1), _p("b", 10, 3)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 9)
        self.assertEqual(result["ages"], {"a": 8, "b": 7})

    def test_slack_stratum_takes_smallest_age_on_ties(self):
        # 最优值由 a 决定（10）；b 在最优阈值下仍有 [0,10] 的空闲，
        # 字典序最小取向量 (10, 0)。
        result = fit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 20), _p("b", 5)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 10)
        self.assertEqual(result["ages"], {"a": 10, "b": 0})

    def test_negative_ages_and_targets(self):
        result = fit(
            {
                "strata": [_s("a", -10, -5), _s("b", -3, 0)],
                "relations": [],
                "preferences": [_p("a", -8), _p("b", -1)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 0)
        self.assertEqual(result["ages"], {"a": -8, "b": -1})

    def test_large_magnitudes(self):
        # 10^12 量级：目标分列区间两端，关系迫使偏差 ~10^12。
        big = 10**12
        result = fit(
            {
                "strata": [_s("a", -big, big), _s("b", -big, big)],
                "relations": [_r("a", "b")],
                "preferences": [_p("a", -big), _p("b", big)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], big + 1)
        self.assertEqual(result["ages"], {"a": 0, "b": -1})

    def test_max_weight_boundary(self):
        result = fit(
            {
                "strata": [_s("a", 10**12 - 5, 10**12)],
                "relations": [],
                "preferences": [_p("a", 10**12, 10**6)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], 0)
        self.assertEqual(result["ages"], {"a": 10**12})

    def test_chain_of_two_hundred_strata(self):
        # 200 层长链：目标全为 0，最优解把年代压向 0，最优值 199。
        n = 200
        ids = [f"s{i:03d}" for i in range(n)]  # s000 最古老
        result = fit(
            {
                "strata": [_s(sid, 0, 10**6) for sid in ids],
                "relations": [_r(ids[i], ids[i + 1]) for i in range(n - 1)],
                "preferences": [_p(sid, 0) for sid in ids],
            }
        )
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["optimal_value"], n - 1)
        self.assertEqual(
            result["ages"], {ids[i]: n - 1 - i for i in range(n)}
        )

    def test_dense_dag_with_thousand_relations(self):
        # 200 层、1000 条关系的稠密 DAG（边均指向更小编号，保证无环）。
        n = 200
        ids = [f"s{i:03d}" for i in range(n)]
        relations = []
        for i in range(n):
            for j in range(i - 1, -1, -1):
                if len(relations) >= 1000:
                    break
                relations.append(_r(ids[j], ids[i]))
            if len(relations) >= 1000:
                break
        self.assertEqual(len(relations), 1000)
        result = fit(
            {
                "strata": [_s(sid, 0, 10**6) for sid in ids],
                "relations": relations,
                "preferences": [_p(sid, 500_000, (i % 7) + 1) for i, sid in enumerate(ids)],
            }
        )
        self.assertEqual(result["status"], "optimal")
        ages = result["ages"]
        # 结果必须满足全部关系约束与区间约束。
        for rel in relations:
            self.assertGreaterEqual(ages[rel["older_id"]], ages[rel["younger_id"]] + 1)
        for sid in ids:
            self.assertGreaterEqual(ages[sid], 0)
            self.assertLessEqual(ages[sid], 10**6)
        # 最优值与结果自洽。
        worst = max(
            ((i % 7) + 1) * abs(ages[sid] - 500_000)
            for i, sid in enumerate(ids)
        )
        self.assertEqual(worst, result["optimal_value"])


class PassthroughTests(unittest.TestCase):
    def test_cycle_result_matches_audit_shape(self):
        result = fit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c"), _r("c", "a")],
                "preferences": [_p(s, 50) for s in ("a", "b", "c")],
            }
        )
        self.assertEqual(result["status"], "cycle")
        path = result["cycle_path"]
        self.assertEqual(path[0], path[-1])
        self.assertEqual(set(result.keys()), {"status", "cycle_path"})

    def test_conflict_result_matches_audit_shape(self):
        result = fit(
            {
                "strata": [
                    _s("a", 50, 50),
                    _s("b", 0, 100),
                    _s("c", 50, 50),
                ],
                "relations": [_r("a", "b"), _r("b", "c")],
                "preferences": [_p(s, 50) for s in ("a", "b", "c")],
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b", "c"])
        self.assertEqual(
            result["tight_intervals"]["b"],
            {"youngest_ma": 51, "oldest_ma": 49},
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
        self.assertTrue(any("缺少偏好" in e and "b" in e for e in errors))

    def test_duplicate_preference_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("a", 6)],
            }
        )
        self.assertTrue(any("重复" in e for e in errors))

    def test_unknown_preference_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "preferences": [_p("a", 5), _p("x", 5)],
            }
        )
        self.assertTrue(any("未提交" in e for e in errors))

    def test_all_three_violations_collected_together(self):
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
        self.assertIn("缺少偏好", joined)  # b 缺偏好


class BruteForceCrossCheckTests(unittest.TestCase):
    """小规模随机用例与暴力枚举对照（最优值 + 字典序最小年代向量）。"""

    def test_against_brute_force(self):
        rng = random.Random(20260926)
        for _ in range(80):
            n = rng.randint(1, 4)
            ids = [f"s{i}" for i in range(n)]
            strata = []
            for sid in ids:
                lo = rng.randint(-4, 4)
                hi = lo + rng.randint(0, 4)
                strata.append(_s(sid, lo, hi))
            # 仅沿随机排列的先后连边，保证无环。
            order = ids[:]
            rng.shuffle(order)
            relations = []
            for i in range(n):
                for j in range(i + 1, n):
                    if rng.random() < 0.4:
                        relations.append(_r(order[i], order[j]))
            preferences = [
                _p(sid, rng.randint(-4, 4), rng.randint(1, 3)) for sid in ids
            ]
            payload = {
                "strata": strata,
                "relations": relations,
                "preferences": preferences,
            }
            expected = _brute_force(payload)
            result = fit(payload)
            if expected is None:
                self.assertEqual(result["status"], "conflict", payload)
                continue
            best_value, best_vector = expected
            self.assertEqual(result["status"], "optimal", payload)
            self.assertEqual(result["optimal_value"], best_value, payload)
            got_vector = tuple(result["ages"][sid] for sid in sorted(ids))
            self.assertEqual(got_vector, best_vector, payload)


def _brute_force(payload: dict) -> tuple[int, tuple[int, ...]] | None:
    """暴力枚举全部整数年代，返回 (最优值, 字典序最小年代向量) 或 None。"""
    strata = {item["id"]: item for item in payload["strata"]}
    prefs = {item["id"]: item for item in payload["preferences"]}
    ids = sorted(strata)
    domains = [
        range(strata[sid]["youngest_ma"], strata[sid]["oldest_ma"] + 1)
        for sid in ids
    ]
    best_value: int | None = None
    best_vector: tuple[int, ...] | None = None
    for combo in itertools.product(*domains):
        age = dict(zip(ids, combo))
        if any(
            age[rel["older_id"]] < age[rel["younger_id"]] + 1
            for rel in payload["relations"]
        ):
            continue
        value = max(
            prefs[sid]["weight"] * abs(age[sid] - prefs[sid]["target_ma"])
            for sid in ids
        )
        if best_value is None or value < best_value:
            best_value, best_vector = value, combo
        elif value == best_value and combo < best_vector:
            best_vector = combo
    if best_value is None:
        return None
    return best_value, best_vector


if __name__ == "__main__":
    unittest.main()
