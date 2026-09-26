"""年代约束审计 FastAPI 服务。

容器内启动::

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .audit import AuditValidationError, audit
from .fit import fit
from .schemas import AuditRequest, AuditResponse, FitRequest, FitResponse

app = FastAPI(
    title="年代约束审计 API",
    description=(
        "提交地层及其允许的整数年代区间（单位百万年，越大越古老）与 "
        "“甲比乙古老”的观测关系；服务检测关系环、传播可行年代区间，"
        "并在可行时返回古老到年轻的字典序最小合法顺序。"
        "POST /fit 进一步在各层期望年代与权重下，选取使最大加权偏差 "
        "max(weight × |age − target_ma|) 最小的整数年代。"
    ),
    version="1.1.0",
)


@app.exception_handler(AuditValidationError)
async def audit_validation_exception_handler(
    _request: object, exc: AuditValidationError
) -> JSONResponse:
    # 业务层基本校验失败（自环、重复编号、非法区间、未知引用等）。
    return JSONResponse(status_code=422, content={"detail": exc.errors})


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"service": "年代约束审计 API", "docs": "/docs", "health": "/health"}


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/audit",
    response_model=AuditResponse,
    summary="审计地层年代约束",
    tags=["audit"],
)
def audit_endpoint(payload: AuditRequest) -> dict:
    """返回三种结果之一（由 ``status`` 字段区分）：

    * ``cycle``：关系成环，附首尾相同的成环编号路径；
    * ``conflict``：区间传播后被压空，附冲突地层与收紧区间；
    * ``feasible``：约束可行，附收紧区间与字典序最小合法顺序。
    """
    return audit(payload.model_dump())


@app.post(
    "/fit",
    response_model=FitResponse,
    summary="在审计约束下优选整数年代",
    tags=["fit"],
)
def fit_endpoint(payload: FitRequest) -> dict:
    """在各层期望年代与权重下选择整数年代，最小化最大加权偏差。

    返回三种结果之一（由 ``status`` 字段区分）：

    * ``cycle`` / ``conflict``：原约束无解，与 /audit 相同；
    * ``optimal``：附最优值 ``optimal_value`` 与年代映射 ``ages``；
      多解时取按编号升序排列的年代向量中字典序最小者。
    """
    return fit(payload.model_dump())
