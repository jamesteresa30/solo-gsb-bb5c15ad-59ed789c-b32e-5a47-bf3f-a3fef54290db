"""年代优选（POST /fit）核心逻辑。

在审计约束（整数年代区间 + ``age(older) >= age(younger) + 1``）下为每层
选择整数年代，最小化最大加权偏差::

    minimize  max_i  weight_i * |age_i - target_ma_i|

算法（不逐年代枚举，区间宽度可达 2×10^12）
------------------------------------------
1. 复用 :mod:`app.audit` 的校验、成环检测与区间传播；原约束无解时直接
   返回与 /audit 相同的 ``cycle`` / ``conflict`` 结果。
2. 目标值 T 具有单调性：T 可行则更大的 T 也可行。对整数 T 二分；对每个
   候选 T，约束 ``w_i*|x_i - t_i| <= T`` 等价于区间
   ``[t_i - T//w_i, t_i + T//w_i]``，与收紧区间求交后再做一次 DAG 传播
   可行性检查（O(n + m)）。上界取
   ``max_i w_i * max(|t_i - L_i|, |t_i - U_i|)``，至多约 61 次迭代。
3. 最小 T 确定后，最优解集合 = 落于各层 T 窗口内的可行赋值。按编号升序
   逐层把当前层固定为传播后的最小可行下界（该值必可被某个可行解取到，
   因为全取下界的赋值本身可行），即得按编号升序排列时字典序最小的
   最优年代向量，共 n 次传播。
"""

from __future__ import annotations

from typing import Any

from .audit import (
    AuditValidationError,
    _find_cycle,
    _propagate,
    _topological_order,
    _validate_and_build,
)

# 与 app.schemas 中保持一致：/fit 的规模与数值上限。
MAX_STRATA = 200
MAX_RELATIONS = 1000
MAX_ABS_AGE = 10**12
MAX_WEIGHT = 10**6


def fit(data: dict[str, Any]) -> dict[str, Any]:
    """执行年代优选，返回可直接序列化的结果字典。"""
    raw_strata: list[dict[str, Any]] = list(data.get("strata") or [])
    raw_relations: list[dict[str, Any]] = list(data.get("relations") or [])
    raw_preferences: list[dict[str, Any]] = list(data.get("preferences") or [])

    # ---- 1. 校验（地层/关系与 /audit 共用；偏好为 /fit 特有） ----------
    ids, bounds, adjacency = _validate_and_build(raw_strata, raw_relations)
    targets, weights = _validate_preferences(raw_preferences, ids)

    # ---- 2. 原约束无解时沿用 /audit 的 cycle / conflict 结果 -----------
    cycle = _find_cycle(ids, adjacency)
    if cycle is not None:
        return {"status": "cycle", "cycle_path": cycle}

    topo = _topological_order(ids, adjacency)
    lower, upper = _propagate(
        ids,
        adjacency,
        topo,
        {sid: bounds[sid][0] for sid in ids},
        {sid: bounds[sid][1] for sid in ids},
    )
    conflicts = sorted(sid for sid in ids if lower[sid] > upper[sid])
    if conflicts:
        return {
            "status": "conflict",
            "conflicting_strata": conflicts,
            "tight_intervals": {
                sid: {"youngest_ma": lower[sid], "oldest_ma": upper[sid]}
                for sid in ids
            },
        }

    # ---- 3. 二分最小的最大加权偏差 T ------------------------------------
    def feasible(limit: int) -> bool:
        lo: dict[str, int] = {}
        hi: dict[str, int] = {}
        for sid in ids:
            span = limit // weights[sid]
            lo[sid] = max(lower[sid], targets[sid] - span)
            hi[sid] = min(upper[sid], targets[sid] + span)
            if lo[sid] > hi[sid]:
                return False
        tight_lo, tight_hi = _propagate(ids, adjacency, topo, lo, hi)
        return all(tight_lo[sid] <= tight_hi[sid] for sid in ids)

    # 任意可行解都落在收紧区间内，故该上界必可行。
    lo_t, hi_t = 0, max(
        weights[sid]
        * max(abs(targets[sid] - lower[sid]), abs(targets[sid] - upper[sid]))
        for sid in ids
    )
    while lo_t < hi_t:
        mid = (lo_t + hi_t) // 2
        if feasible(mid):
            hi_t = mid
        else:
            lo_t = mid + 1
    optimal = lo_t

    # ---- 4. 字典序最小最优解：按编号升序逐层固定最小可行年代 ------------
    base_lo: dict[str, int] = {}
    base_hi: dict[str, int] = {}
    for sid in ids:
        span = optimal // weights[sid]
        base_lo[sid] = max(lower[sid], targets[sid] - span)
        base_hi[sid] = min(upper[sid], targets[sid] + span)

    ages: dict[str, int] = {}
    for sid in sorted(ids):
        tight_lo, _ = _propagate(ids, adjacency, topo, base_lo, base_hi)
        ages[sid] = tight_lo[sid]
        base_lo[sid] = base_hi[sid] = tight_lo[sid]

    return {"status": "optimal", "optimal_value": optimal, "ages": ages}


def _validate_preferences(
    raw_preferences: list[dict[str, Any]], ids: list[str]
) -> tuple[dict[str, int], dict[str, int]]:
    """校验偏好列表：每层恰好一项，缺漏、重复或未知编号抛 422 错误。

    返回 ``(targets, weights)``：编号 -> target_ma / weight。
    """
    errors: list[str] = []
    known = set(ids)
    targets: dict[str, int] = {}
    weights: dict[str, int] = {}
    duplicate_ids: set[str] = set()

    for index, pref in enumerate(raw_preferences, start=1):
        sid = pref["id"]
        if sid not in known:
            errors.append(
                f"第 {index} 项偏好引用了未提交的地层编号：{sid!r}"
            )
            continue
        if sid in targets:
            duplicate_ids.add(sid)
            continue
        targets[sid] = pref["target_ma"]
        weights[sid] = pref["weight"]

    for sid in sorted(duplicate_ids):
        errors.append(f"地层 {sid!r} 的偏好重复提交")
    for sid in ids:
        if sid not in targets:
            errors.append(f"地层 {sid!r} 缺少对应的偏好设置")

    if errors:
        raise AuditValidationError(errors)
    return targets, weights
