"""Validated API request models."""

import re
from typing import Optional

from pydantic import BaseModel, Field, validator


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PIPELINE_STAGES = ("Applied", "Screening", "Interview", "Offer", "Hired", "Rejected")


class LoginRequest(BaseModel):
    class Config:
        extra = "forbid"

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @validator("email")
    def validate_email(cls, value):
        value = value.strip()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value


class CandidateCreateRequest(BaseModel):
    class Config:
        extra = "forbid"

    full_name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=320)
    phone: Optional[str] = Field(default=None, max_length=50)

    @validator("full_name")
    def validate_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Candidate name must not be empty.")
        return value

    @validator("email")
    def validate_email(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value

    @validator("phone")
    def clean_phone(cls, value):
        if value is None or not value.strip():
            return None
        return value.strip()


class SearchRequest(BaseModel):
    class Config:
        extra = "forbid"

    q: str = Field(min_length=1, max_length=500)


class StageRequest(BaseModel):
    class Config:
        extra = "forbid"

    expected_stage: str

    @validator("expected_stage")
    def validate_stage(cls, value):
        if value not in PIPELINE_STAGES:
            raise ValueError("Use one of the defined candidate stages.")
        return value


class RejectRequest(StageRequest):
    reason: Optional[str] = Field(default=None, max_length=2000)

    @validator("reason")
    def clean_reason(cls, value):
        return value.strip() if value and value.strip() else None
