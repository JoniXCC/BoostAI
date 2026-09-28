"""Strict schema for AI output. Anything that does not fit is discarded."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    issue_key: str = Field(min_length=1, max_length=300)
    recommended_action_id: str | None = Field(default=None, max_length=64)
    proposal_index: int | None = Field(default=None, ge=0, le=50)
    explanation: str = Field(min_length=1, max_length=1000)
    user_warning: str | None = Field(default=None, max_length=400)


class AIAdvice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=1500)
    priority: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommendations: list[AIRecommendation] = Field(default_factory=list, max_length=15)


class AIExplanation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    explanation: str = Field(min_length=1, max_length=1500)
