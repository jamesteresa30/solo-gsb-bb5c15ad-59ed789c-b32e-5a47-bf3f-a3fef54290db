"""偏好拟合：在审计约束下挑选整数年代，最小化最大加权偏差。

与 /audit 共用同一套约束（整数年代区间 + ``age(older) >= age(younger) + 1``），
另为每层接收恰好一项偏好 ``(target_ma, weight)``，求解::

    minimize  max_i( weight_i × |age_i − target_i| )

算法（不逐年代枚举）
------------------
1. 对候选阈值 T，约束 ``weight_i × |age_i − target_i| <= T`` 的整数解集为
   区间 ``[target_i − ⌊T/weight_i⌋, target_i + ⌊T/weight_i⌋]``；与原始区间
   取交后在关系 DAG 上双向传播即可判定可行性（差分约束系统，
   单次判定 O(层数 + 关系数)）。
2. 目标值必为非负整数，且“T 可行 ⇒ 更大的 T 可行”，故二分最小的可行 T
   （上界 2×10^18，约 60 次迭代）。
3. 在最优 T 的收紧区间内按编号升序贪心：每层的传播下界即其当前可行最小
   年代，直接固定并重传播——最终得到“按编号升序排列的年代向量”中
   字典序最小的最优解。
"""

from __future__ import annotations

from typing import Any

from .audit import (
    AuditValidationError,
    find_cycle,
    lexicographic_topo,
    tighten_bounds,
    validate_and_build,
)

#: 目标值的稳妥上界：|age − target| ≤ 2×10^12 且 weight ≤ 10^6。
_MAX_OBJECTIVE = 2 * 10**18


def fit(data: dict[str, Any]) -> dict[str, Any]:
    """执行偏好拟合，返回可直接序列化的结果字典。

    结果形态与 /audit 对齐：约束成环/压空时原样返回 cycle/conflict；
    否则返回 ``{"status": "optimal", "optimal_value": T, "ages": {...}}``。
    """
    raw_strata: list[dict[str, Any]] = list(data.get("strata") or [])
    raw_relations: list[dict[str, Any]] = list(data.get("relations") or [])
    raw_preferences: list[dict[str, Any]] = list(data.get("preferences") or [])

    # ---- 1. 基本校验（与 /audit 完全一致的约束语义） --------------------
    ids, bounds, adjacency = validate_and_build(raw_strata, raw_relations)
    prefs = _validate_preferences(ids, raw_preferences)

    # ---- 2. 原约束无解时沿用现有 cycle/conflict 结果 --------------------
    cycle = find_cycle(ids, adjacency)
    if cycle is not None:
        return {"status": "cycle", "cycle_path": cycle}

    topo = lexicographic_topo(ids, adjacency)
    base_lower = {sid: bounds[sid][0] for sid in ids}
    base_upper = {sid: bounds[sid][1] for sid in ids}
    tighten_bounds(topo, adjacency, base_lower, base_upper)

    conflicts = sorted(sid for sid in ids if base_lower[sid] > base_upper[sid])
    if conflicts:
        return {
            "status": "conflict",
            "conflicting_strata": conflicts,
            "tight_intervals": {
                sid: {
                    "youngest_ma": base_lower[sid],
                    "oldest_ma": base_upper[sid],
                }
                for sid in ids
            },
        }

    # ---- 3. 二分最小的可行阈值 T ----------------------------------------
    lo_t, hi_t = -1, _MAX_OBJECTIVE  # 不变式：lo_t 不可行，hi_t 必可行
    while hi_t - lo_t > 1:
        mid = (lo_t + hi_t) // 2
        if _clipped_bounds(
            ids, topo, adjacency, base_lower, base_upper, prefs, mid
        ) is not None:
            hi_t = mid
        else:
            lo_t = mid
    optimal_value = hi_t

    # ---- 4. 多解中取字典序最小者 ----------------------------------------
    clipped = _clipped_bounds(
        ids, topo, adjacency, base_lower, base_upper, prefs, optimal_value
    )
    assert clipped is not None  # 二分已确认最优阈值可行
    lower, upper = clipped

    ages: dict[str, int] = {}
    for sid in sorted(ids):
        # 传播下界即该层当前可行的最小整数年代；固定后系统仍可行。
        value = lower[sid]
        ages[sid] = value
        lower[sid] = upper[sid] = value
        tighten_bounds(topo, adjacency, lower, upper)

    return {
        "status": "optimal",
        "optimal_value": optimal_value,
        "ages": ages,
    }


def _validate_preferences(
    ids: list[str], raw_preferences: list[dict[str, Any]]
) -> dict[str, tuple[int, int]]:
    """校验偏好并返回 ``{sid: (weight, target_ma)}``。

    每层恰好一项：缺漏、重复或引用未提交编号均抛
    :class:`AuditValidationError`（由路由层映射为 HTTP 422）。
    """
    known = set(ids)
    prefs: dict[str, tuple[int, int]] = {}
    errors: list[str] = []
    duplicates: set[str] = set()
    for item in raw_preferences:
        sid = item["id"]
        if sid not in known:
            errors.append(f"偏好引用了未提交的地层编号：{sid!r}")
            continue
        if sid in prefs:
            duplicates.add(sid)
            continue
        prefs[sid] = (item["weight"], item["target_ma"])
    for sid in sorted(duplicates):
        errors.append(f"地层 {sid!r} 的偏好重复：每层恰好需要一项偏好")
    for sid in ids:
        if sid not in prefs:
            errors.append(f"地层 {sid!r} 缺少偏好：每层恰好需要一项偏好")
    if errors:
        raise AuditValidationError(errors)
    return prefs


def _clipped_bounds(
    ids: list[str],
    topo: list[str],
    adjacency: dict[str, set[str]],
    base_lower: dict[str, int],
    base_upper: dict[str, int],
    prefs: dict[str, tuple[int, int]],
    threshold: int,
) -> tuple[dict[str, int], dict[str, int]] | None:
    """阈值 ``threshold`` 下收紧后的 ``(lower, upper)``；不可行返回 None。

    ``weight × |age − target| <= T`` 的整数解集恰为
    ``[target − ⌊T/weight⌋, target + ⌊T/weight⌋]``（左端取整后仍为整数），
    与原始区间取交后在 DAG 上双向传播判定可行性。
    """
    lower: dict[str, int] = {}
    upper: dict[str, int] = {}
    for sid in ids:
        weight, target = prefs[sid]
        slack = threshold // weight  # ⌊T/weight⌋
        lo = base_lower[sid]
        hi = base_upper[sid]
        pref_lo = target - slack
        pref_hi = target + slack
        if pref_lo > lo:
            lo = pref_lo
        if pref_hi < hi:
            hi = pref_hi
        if lo > hi:
            return None
        lower[sid] = lo
        upper[sid] = hi
    tighten_bounds(topo, adjacency, lower, upper)
    for sid in ids:
        if lower[sid] > upper[sid]:
            return None
    return lower, upper
