from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SuccessEnvelope(BaseModel):
    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorBody


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int = 1
    page_size: int = 25
    pages: int = 1

    @classmethod
    def build(cls, items: List[T], total: int, page: int, page_size: int) -> "Page[T]":
        pages = max(1, (total + page_size - 1) // page_size) if page_size else 1
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


class SlaView(BaseModel):
    state: str
    deadline: Optional[dt.datetime] = None
    remaining_seconds: Optional[int] = None
    remaining_label: str = ""
    elapsed_percent: int = 0
    is_breached: bool = False


class IdName(BaseModel):
    id: int
    name: str


class Choice(BaseModel):
    value: str
    label: str


class Counter(BaseModel):
    label: str
    value: int
    key: Optional[str] = None


class SeriesPoint(BaseModel):
    label: str
    value: float = Field(default=0)
