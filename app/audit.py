"""年代约束审计核心逻辑。

数据模型
--------
* 每个地层有唯一编号 ``id`` 与允许的整数年代区间
  ``[youngest_ma, oldest_ma]``，单位百万年（Ma），数值越大越古老，
  因此必有 ``youngest_ma <= oldest_ma``。
* 关系 ``older_id -> younger_id`` 表示“甲比乙古老”，转换为约束::

      age(older_id) >= age(younger_id) + 1

  即在关系图上加入一条 older -> younger 的有向边。

处理流程
--------
1. 基本校验（重复编号、非法区间、自比古老、引用未知编号）——不通过时抛
   :class:`AuditValidationError`，由路由层映射为 HTTP 422。
2. 迭代式 DFS 检测有向环，命中时返回一条首尾编号相同的成环路径。
3. 无环时在 DAG 上传播可行年代：
   * 最晚（上）界沿拓扑正向收紧：``U(v) = min(U(v), U(u) - 1)``；
   * 最早（下）界沿拓扑反向收紧：``L(u) = max(L(u), L(v) + 1)``。
4. 若某层出现 ``L > U``，区间被压空，返回冲突地层；否则返回收紧后的
   区间，以及古老 -> 年轻方向上编号字典序最小的合法顺序
   （Kahn 算法配合最小堆）。
"""

from __future__ import annotations

from heapq import heappop, heappush
from typing import Any, Iterator

WHITE, GRAY, BLACK = 0, 1, 2


class AuditValidationError(ValueError):
    """提交数据不满足基本约束，``errors`` 为面向用户的中文说明列表。"""

    def __init__(self, errors: list[str]):
        super().__init__("；".join(errors))
        self.errors = list(errors)


def audit(data: dict[str, Any]) -> dict[str, Any]:
    """对一次提交执行完整审计，返回可直接序列化的结果字典。"""
    raw_strata: list[dict[str, Any]] = list(data.get("strata") or [])
    raw_relations: list[dict[str, Any]] = list(data.get("relations") or [])

    # ---- 1. 基本校验（尽量一次性收集全部错误） -------------------------
    errors: list[str] = []

    ids: list[str] = []
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for item in raw_strata:
        sid = item["id"]
        if sid in seen:
            duplicate_ids.add(sid)
        else:
            seen.add(sid)
            ids.append(sid)
    for sid in sorted(duplicate_ids):
        errors.append(f"地层编号重复：{sid!r}")

    # 对每个编号第一次出现的记录检查区间；后续出现记为重复。
    bounds: dict[str, tuple[int, int]] = {}
    first_seen: set[str] = set()
    for item in raw_strata:
        sid = item["id"]
        if sid in first_seen:
            continue
        first_seen.add(sid)
        youngest = item["youngest_ma"]
        oldest = item["oldest_ma"]
        if youngest <= oldest:
            bounds[sid] = (youngest, oldest)
        else:
            errors.append(
                f"地层 {sid!r} 的年代区间非法：youngest_ma({youngest}) "
                f"不得大于 oldest_ma({oldest})"
            )

    # 去重后的边；非法关系（自环/未知引用）不参与建图。
    edge_set: set[tuple[str, str]] = set()
    for index, rel in enumerate(raw_relations, start=1):
        older = rel["older_id"]
        younger = rel["younger_id"]
        if older == younger:
            errors.append(
                f"第 {index} 条关系非法：地层 {older!r} 不得同时比自身古老"
            )
            continue
        missing = [x for x in (older, younger) if x not in seen]
        if missing:
            errors.append(
                f"第 {index} 条关系引用了未提交的地层编号："
                f"{', '.join(repr(x) for x in missing)}"
            )
            continue
        edge_set.add((older, younger))

    if errors:
        raise AuditValidationError(errors)

    adjacency: dict[str, set[str]] = {sid: set() for sid in ids}
    indegree: dict[str, int] = {sid: 0 for sid in ids}
    for older, younger in edge_set:
        if younger not in adjacency[older]:
            adjacency[older].add(younger)
            indegree[younger] += 1

    # ---- 2. 成环检测（迭代 DFS，输出首尾相同的环路径） -----------------
    cycle = _find_cycle(ids, adjacency)
    if cycle is not None:
        return {"status": "cycle", "cycle_path": cycle}

    # ---- 3a. 字典序最小拓扑序，同时正向收紧最晚（上）界 ----------------
    upper = {sid: bounds[sid][1] for sid in ids}
    heap = [sid for sid in ids if indegree[sid] == 0]
    heap.sort()  # 本身即堆序
    topo: list[str] = []
    while heap:
        u = heappop(heap)
        topo.append(u)
        for v in sorted(adjacency[u]):
            # age(u) >= age(v) + 1  =>  U(v) <= U(u) - 1
            candidate = upper[u] - 1
            if candidate < upper[v]:
                upper[v] = candidate
            indegree[v] -= 1
            if indegree[v] == 0:
                heappush(heap, v)

    # DFS 已确认无环，所有节点都应得到排列。
    if len(topo) != len(ids):  # pragma: no cover - 防御性分支
        raise RuntimeError("拓扑排序失败：关系图中仍存在环")

    # ---- 3b. 反向收紧最早（下）界 --------------------------------------
    lower = {sid: bounds[sid][0] for sid in ids}
    for u in reversed(topo):
        for v in adjacency[u]:
            # age(u) >= age(v) + 1  =>  L(u) >= L(v) + 1
            candidate = lower[v] + 1
            if candidate > lower[u]:
                lower[u] = candidate

    tight_intervals = {
        sid: {"youngest_ma": lower[sid], "oldest_ma": upper[sid]} for sid in ids
    }

    # ---- 4. 区间是否被压空 ---------------------------------------------
    conflicts = sorted(sid for sid in ids if lower[sid] > upper[sid])
    if conflicts:
        return {
            "status": "conflict",
            "conflicting_strata": conflicts,
            "tight_intervals": tight_intervals,
        }

    return {
        "status": "feasible",
        "tight_intervals": tight_intervals,
        "oldest_to_youngest": topo,
    }


def _find_cycle(
    ids: list[str], adjacency: dict[str, set[str]]
) -> list[str] | None:
    """在有向图中寻找一条环。

    使用迭代式三色 DFS：遇到 GRAY（仍在当前 DFS 栈中）的邻点即找到环，
    环路径取该邻点在栈中的位置到栈尾、再补上该邻点（首尾编号相同）。
    起点与邻接点均按编号排序，保证结果确定。
    """
    color: dict[str, int] = {sid: WHITE for sid in ids}

    for start in sorted(ids):
        if color[start] != WHITE:
            continue

        stack: list[tuple[str, Iterator[str]]] = [
            (start, iter(sorted(adjacency[start])))
        ]
        color[start] = GRAY
        # GRAY 节点在当前 DFS 栈中的位置；节点变黑（出栈）时同步删除。
        position = {start: 0}

        while stack:
            u, neighbors = stack[-1]
            next_node: str | None = None
            for w in neighbors:
                if color[w] == GRAY:
                    cycle_nodes = [node for node, _ in stack[position[w] :]]
                    cycle_nodes.append(w)
                    return cycle_nodes
                if color[w] == WHITE:
                    next_node = w
                    break
                # BLACK 节点直接跳过
            if next_node is not None:
                color[next_node] = GRAY
                position[next_node] = len(stack)
                stack.append(
                    (next_node, iter(sorted(adjacency[next_node])))
                )
            else:
                color[u] = BLACK
                del position[u]
                stack.pop()

    return None
