"""Pydantic models for structured LLM output in agent nodes."""

from typing import List, Optional
from pydantic import BaseModel, Field


class IntentFilter(BaseModel):
    """A single filter condition parsed from the user query."""

    field: str = Field(description="Filter field name, e.g. region, category")
    operator: str = Field(description="Comparison operator: =, >, <, >=, <=, IN")
    value: str = Field(description="Filter value")


class IntentResult(BaseModel):
    """Structured intent parsed from the user's natural-language analysis question.

    Candidate values for analysis_type / metrics / dimensions are injected at prompt time
    from knowledge.json → get_intent_options(). Add new metrics there without touching code.
    """

    analysis_type: str = Field(
        description="分析类型，从 prompt 中列出的候选值中选择一项"
    )
    metrics: List[str] = Field(
        default_factory=list,
        description="分析指标，从 prompt 中列出的候选值中选择",
    )
    dimensions: List[str] = Field(
        default_factory=list,
        description="分析维度，从 prompt 中列出的候选值中选择",
    )
    time_range: str = Field(default="", description="Time range, e.g. 2025年Q1-Q3 或 2025年7月")
    filters: List[IntentFilter] = Field(
        default_factory=list, description="Optional filter conditions"
    )
