"""请求与响应模型。

年代单位为百万年（Ma），整数，数值越大越古老。
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Stratum(BaseModel):
    """一个地层及其允许的整数年代区间 [youngest_ma, oldest_ma]。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="地层唯一编号")
    youngest_ma: StrictInt = Field(
        ..., description="允许的最年轻年代（Ma，下界）"
    )
    oldest_ma: StrictInt = Field(
        ..., description="允许的最古老年代（Ma，上界）"
    )


class OlderRelation(BaseModel):
    """观测关系：older_id 比 younger_id 古老。

    服务将其转换为约束 age(older_id) >= age(younger_id) + 1。
    """

    model_config = ConfigDict(extra="forbid")

    older_id: str = Field(..., min_length=1, description="较古老的地层编号（甲）")
    younger_id: str = Field(..., min_length=1, description="较年轻的地层编号（乙）")


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strata: list[Stratum] = Field(..., min_length=1, description="提交的地层列表")
    relations: list[OlderRelation] = Field(
        default_factory=list, description="“甲比乙古老”的观测关系列表"
    )


class IntervalOut(BaseModel):
    youngest_ma: int
    oldest_ma: int


class CycleResponse(BaseModel):
    status: Literal["cycle"]
    cycle_path: list[str] = Field(
        ..., description="成环的地层编号路径，首尾编号相同"
    )


class ConflictResponse(BaseModel):
    status: Literal["conflict"]
    conflicting_strata: list[str] = Field(
        ..., description="收紧后可行区间被压空的地层编号"
    )
    tight_intervals: dict[str, IntervalOut] = Field(
        ..., description="沿关系图传播收紧后的各层年代区间"
    )


class FeasibleResponse(BaseModel):
    status: Literal["feasible"]
    tight_intervals: dict[str, IntervalOut] = Field(
        ..., description="沿关系图传播收紧后的各层年代区间"
    )
    oldest_to_youngest: list[str] = Field(
        ..., description="古老 -> 年轻方向上编号字典序最小的合法顺序"
    )


AuditResponse = Annotated[
    Union[CycleResponse, ConflictResponse, FeasibleResponse],
    Field(discriminator="status"),
]
