"""核心审计逻辑单元测试。"""

from __future__ import annotations

import unittest

from app.audit import AuditValidationError, audit


def _s(sid: str, youngest: int, oldest: int) -> dict:
    return {"id": sid, "youngest_ma": youngest, "oldest_ma": oldest}


def _r(older: str, younger: str) -> dict:
    return {"older_id": older, "younger_id": younger}


class FeasibleTests(unittest.TestCase):
    def test_no_relations_keeps_intervals_and_sorts_lexicographically(self):
        result = audit(
            {
                "strata": [
                    _s("b", 100, 120),
                    _s("a", 100, 120),
                    _s("c", 100, 120),
                ],
                "relations": [],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(
            result["tight_intervals"]["b"],
            {"youngest_ma": 100, "oldest_ma": 120},
        )
        # 无关系时古老->年轻的字典序最小顺序即编号升序。
        self.assertEqual(result["oldest_to_youngest"], ["a", "b", "c"])

    def test_chain_propagates_both_bounds(self):
        # a(100..110) -> b(100..110) -> c(100..110)
        result = audit(
            {
                "strata": [_s(s, 100, 110) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c")],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(
            result["tight_intervals"],
            {
                "a": {"youngest_ma": 102, "oldest_ma": 110},
                "b": {"youngest_ma": 101, "oldest_ma": 109},
                "c": {"youngest_ma": 100, "oldest_ma": 108},
            },
        )
        self.assertEqual(result["oldest_to_youngest"], ["a", "b", "c"])

    def test_lexicographically_smallest_topological_order(self):
        # b -> a, b -> c：唯一起点 b，之后可选 a/c，取较小者 a。
        result = audit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("b", "a"), _r("b", "c")],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(result["oldest_to_youngest"], ["b", "a", "c"])

    def test_integer_ages_only_gap_of_one(self):
        # 单层区间只有一个可行整数年；相邻层必须至少相差 1。
        result = audit(
            {
                "strata": [_s("a", 5, 5), _s("b", 5, 5)],
                "relations": [_r("a", "b")],
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b"])

    def test_diamond_propagation(self):
        # a -> {b, c} -> d，统一 [0, 100]
        result = audit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c", "d")],
                "relations": [
                    _r("a", "b"),
                    _r("a", "c"),
                    _r("b", "d"),
                    _r("c", "d"),
                ],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(
            result["tight_intervals"],
            {
                "a": {"youngest_ma": 2, "oldest_ma": 100},
                "b": {"youngest_ma": 1, "oldest_ma": 99},
                "c": {"youngest_ma": 1, "oldest_ma": 99},
                "d": {"youngest_ma": 0, "oldest_ma": 98},
            },
        )
        self.assertEqual(result["oldest_to_youngest"], ["a", "b", "c", "d"])

    def test_duplicate_relations_are_idempotent(self):
        result = audit(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b"), _r("a", "b")],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(
            result["tight_intervals"],
            {
                "a": {"youngest_ma": 1, "oldest_ma": 10},
                "b": {"youngest_ma": 0, "oldest_ma": 9},
            },
        )

    def test_empty_tightening_leaves_valid_point_intervals(self):
        result = audit(
            {
                "strata": [_s("a", 6, 6), _s("b", 5, 5)],
                "relations": [_r("a", "b")],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(result["oldest_to_youngest"], ["a", "b"])

    def test_input_order_does_not_affect_result(self):
        payload = {
            "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
            "relations": [_r("b", "c"), _r("a", "b")],
        }
        shuffled = {
            "strata": list(reversed(payload["strata"])),
            "relations": list(reversed(payload["relations"])),
        }
        self.assertEqual(audit(payload), audit(shuffled))

    def test_disconnected_components_still_fully_sorted(self):
        # 两条无关链 c->d 与 a->b；字典序最小的全局拓扑序为 a,b,c,d。
        result = audit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c", "d")],
                "relations": [_r("c", "d"), _r("a", "b")],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(result["oldest_to_youngest"], ["a", "b", "c", "d"])

    def test_long_chain_is_linear_time_and_correct(self):
        n = 2000
        ids = [f"s{i:04d}" for i in range(n)]  # s0000 最古老
        result = audit(
            {
                "strata": [_s(sid, 0, 10_000) for sid in ids],
                "relations": [
                    _r(ids[i], ids[i + 1]) for i in range(n - 1)
                ],
            }
        )
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(result["oldest_to_youngest"], ids)
        self.assertEqual(
            result["tight_intervals"]["s0000"]["youngest_ma"], n - 1
        )
        self.assertEqual(
            result["tight_intervals"][f"s{n - 1:04d}"]["oldest_ma"],
            10_000 - (n - 1),
        )


class CycleTests(unittest.TestCase):
    def test_simple_cycle_path_is_closed(self):
        result = audit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c"), _r("c", "a")],
            }
        )
        self.assertEqual(result["status"], "cycle")
        path = result["cycle_path"]
        self.assertEqual(path[0], path[-1])
        self.assertEqual(path, ["a", "b", "c", "a"])

    def test_cycle_with_unrelated_branch(self):
        result = audit(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "z")],
                "relations": [
                    _r("a", "b"),
                    _r("b", "a"),
                    _r("z", "a"),
                ],
            }
        )
        self.assertEqual(result["status"], "cycle")
        path = result["cycle_path"]
        self.assertEqual(path[0], path[-1])
        self.assertGreaterEqual(len(path), 3)


class ConflictTests(unittest.TestCase):
    def test_middle_layer_conflict(self):
        # a -> b -> c：c 固定为 50，迫使 b >= 51；a 固定为 50，迫使 b <= 49。
        # 不可行会沿链传播，三层收紧后的区间均被压空。
        result = audit(
            {
                "strata": [
                    _s("a", 50, 50),
                    _s("b", 0, 100),
                    _s("c", 50, 50),
                ],
                "relations": [_r("a", "b"), _r("b", "c")],
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b", "c"])
        self.assertEqual(
            result["tight_intervals"]["b"],
            {"youngest_ma": 51, "oldest_ma": 49},
        )
        self.assertEqual(
            result["tight_intervals"]["a"],
            {"youngest_ma": 52, "oldest_ma": 50},
        )
        self.assertEqual(
            result["tight_intervals"]["c"],
            {"youngest_ma": 50, "oldest_ma": 48},
        )

    def test_conflict_propagated_from_narrow_band(self):
        # 4 层但总宽度只够 3 层（需要至少 3 的跨度），收紧后全部压空。
        result = audit(
            {
                "strata": [_s(s, 10, 12) for s in ("a", "b", "c", "d")],
                "relations": [_r("a", "b"), _r("b", "c"), _r("c", "d")],
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(
            result["conflicting_strata"], ["a", "b", "c", "d"]
        )


class ValidationTests(unittest.TestCase):
    def _errors(self, data: dict) -> list[str]:
        try:
            audit(data)
        except AuditValidationError as exc:
            return exc.errors
        self.fail("应当抛出 AuditValidationError")

    def test_self_older_than_self_is_rejected(self):
        errors = self._errors(
            {"strata": [_s("a", 0, 10)], "relations": [_r("a", "a")]}
        )
        self.assertTrue(any("自身古老" in e for e in errors))

    def test_unknown_reference_is_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [_r("a", "x")],
            }
        )
        self.assertTrue(any("未提交" in e for e in errors))

    def test_duplicate_ids_and_bad_intervals_collected_together(self):
        errors = self._errors(
            {
                "strata": [
                    _s("a", 10, 5),
                    _s("a", 0, 1),
                    _s("b", 0, 0),
                ],
                "relations": [],
            }
        )
        joined = "\n".join(errors)
        self.assertIn("重复", joined)
        self.assertIn("非法", joined)


if __name__ == "__main__":
    unittest.main()
