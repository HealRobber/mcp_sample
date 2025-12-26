from pydantic import BaseModel, Field
from typing import Optional

class IncreasingErrorServicesRequest(BaseModel):
    cluster: str = Field(..., min_length=1)
    namespace: Optional[str] = Field(default=None)
    status: str = Field(default="error", min_length=1)

    # ✅ 180 → 20160 (2주) 로 확장
    window_minutes: int = Field(default=15, ge=5, le=20160)

    limit: int = Field(default=10, ge=1, le=20)
    min_delta: int = Field(default=10, ge=1)
    min_ratio: float = Field(default=2.0, ge=1.0, le=100.0)
