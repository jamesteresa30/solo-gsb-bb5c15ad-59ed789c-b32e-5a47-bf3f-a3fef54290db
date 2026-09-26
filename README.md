# 年代约束审计 API

供地质记录整理使用的年代约束审计与年代优选服务（Python + FastAPI）。

提交：

- **地层列表**：每层有唯一编号 `id`，以及允许的**整数**年代区间
  `[youngest_ma, oldest_ma]`，单位**百万年（Ma）**，数值越大越古老
  （故 `youngest_ma <= oldest_ma`）。
- **观测关系**：`older_id` 比 `younger_id` 古老（“甲比乙古老”）。
- **年代偏好**（仅 `/fit`）：每层的期望年代 `target_ma` 与正整数权重
  `weight`，每层恰好一项。

服务把每条关系转换为整数约束：

```
age(older_id) >= age(younger_id) + 1
```

即在关系图上加入一条 `older -> younger` 的有向边，然后：

1. 同一地层比自身古老（自环）、编号重复、区间非法、引用未提交的编号等
   情况返回 **HTTP 422** 并附带中文错误说明；
2. 检测关系**环**，命中时返回一条首尾编号相同的成环路径 `cycle_path`；
3. 无环时沿关系图双向传播，收紧每层最早/最晚可行年代；
   - 最晚（上）界沿拓扑正向收紧：`U(v) = min(U(v), U(u) - 1)`；
   - 最早（下）界沿拓扑反向收紧：`L(u) = max(L(u), L(v) + 1)`；
4. 若某层收紧后 `L > U`（区间被压空），返回冲突地层编号 `conflicting_strata`
   与全部收紧区间 `tight_intervals`；
5. `/audit` 在可行时返回各层收紧后的区间 `tight_intervals`，以及
   **古老 → 年轻**方向上编号**字典序最小**的合法顺序
   `oldest_to_youngest`（Kahn 算法 + 最小堆）；
6. `/fit` 在可行时进一步为每层选定整数年代，**最小化最大加权偏差**
   `max(weight × |age − target_ma|)`，返回最优值 `optimal_value` 与年代
   映射 `ages`；多解时取**按编号升序排列的年代向量中字典序最小者**。

无数据库、无外部服务，单容器即可运行。

## 一条命令启动

```bash
docker compose up --build
```

依赖安装、初始化检查与服务启动全部包含在该命令内（构建期会执行
`import app.main` 冒烟检查，健康检查由 Compose 配置）。

启动后 API 位于本机 **http://localhost:8000** ：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000/audit | 审计接口（POST） |
| http://localhost:8000/fit | 年代优选接口（POST） |
| http://localhost:8000/docs | Swagger 交互文档 |
| http://localhost:8000/health | 健康检查 |

## 配置项

| 配置 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `HOST_PORT` | `.env` 或环境变量 / Compose 变量 | `8000` | 映射到宿主机的端口；容器内固定监听 `8000` |

如需改用宿主机 18080 端口：

```bash
HOST_PORT=18080 docker compose up --build
```

也可以复制 `.env.example` 为 `.env` 后修改 `HOST_PORT`。

## 请求示例：POST /audit

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "strata": [
      {"id": "A", "youngest_ma": 100, "oldest_ma": 110},
      {"id": "B", "youngest_ma": 100, "oldest_ma": 110},
      {"id": "C", "youngest_ma": 100, "oldest_ma": 110}
    ],
    "relations": [
      {"older_id": "A", "younger_id": "B"},
      {"older_id": "B", "younger_id": "C"}
    ]
  }'
```

可行时响应（沿 `A -> B -> C` 双向传播后，每层区间都被收紧）：

```json
{
  "status": "feasible",
  "tight_intervals": {
    "A": {"youngest_ma": 102, "oldest_ma": 110},
    "B": {"youngest_ma": 101, "oldest_ma": 109},
    "C": {"youngest_ma": 100, "oldest_ma": 108}
  },
  "oldest_to_youngest": ["A", "B", "C"]
}
```

其他两种结果：

- 成环：`{"status": "cycle", "cycle_path": ["A", "B", "C", "A"]}`，
  路径首尾编号相同，代表环从 `A` 出发回到 `A`；
- 冲突：`{"status": "conflict", "conflicting_strata": ["B", ...],
  "tight_intervals": {...}}`，冲突层的区间形如
  `{"youngest_ma": 51, "oldest_ma": 49}`（下界超过上界，即被压空）。

## 请求示例：POST /fit

在 `/audit` 的 `strata`、`relations` 之外再提交 `preferences`
（`id`、整数 `target_ma`、正整数 `weight`，**每层恰好一项**；缺漏、
重复或未知编号返回 422）：

```bash
curl -s -X POST http://localhost:8000/fit \
  -H 'Content-Type: application/json' \
  -d '{
    "strata": [
      {"id": "A", "youngest_ma": 0, "oldest_ma": 10},
      {"id": "B", "youngest_ma": 0, "oldest_ma": 10}
    ],
    "relations": [
      {"older_id": "A", "younger_id": "B"}
    ],
    "preferences": [
      {"id": "A", "target_ma": 5, "weight": 1},
      {"id": "B", "target_ma": 5, "weight": 1}
    ]
  }'
```

响应（`A >= B + 1` 使两层不可能同时取 5，最优偏差为 1；多解中取按
编号升序 `(A, B)` 字典序最小的 `(5, 4)`）：

```json
{"status": "optimal", "optimal_value": 1, "ages": {"A": 5, "B": 4}}
```

- 原约束无解时返回与 `/audit` 相同的 `cycle` / `conflict` 结果；
- 求解不逐年代枚举：对目标值二分（每次把 `weight×|age−target| ≤ T`
  化为年代区间后做传播可行性检查），再按编号升序逐层固定最小可行
  年代得到字典序最小的最优解。

### /fit 的规模与数值上限（越界返回 422）

| 限制 | 上限 |
| --- | --- |
| 地层数量 | 200 |
| 关系数量 | 1000 |
| 年代（`youngest_ma` / `oldest_ma`）与 `target_ma` 绝对值 | 10^12 |
| `weight` | 1 ~ 10^6 的正整数 |

## 本地开发（不使用 Docker）

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
python -m unittest discover -s tests    # 运行测试
```

## 目录结构

```
app/
  audit.py     # 校验、成环检测、上下界传播、字典序最小拓扑序（/audit 与 /fit 共用）
  fit.py       # 目标值二分 + 字典序最小最优年代选取（/fit）
  schemas.py   # Pydantic 请求/响应模型（含 /fit 规模与数值上限）
  main.py      # FastAPI 路由
tests/
  test_audit.py
  test_fit.py
Dockerfile
docker-compose.yml
requirements.txt
```
