from pydantic import BaseModel, ConfigDict


class SeverityBreakdown(BaseModel):
    """Breakdown of vulnerabilities by severity level."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class ModelStats(BaseModel):
    """Performance statistics for a single model."""

    model_config = ConfigDict(from_attributes=True)

    model: str
    total_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    success_rate: float
    avg_duration_seconds: float | None
    total_vulnerabilities: int
    avg_vulnerabilities_per_audit: float
    severity_breakdown: SeverityBreakdown


class LeaderboardResponse(BaseModel):
    """Leaderboard response with model rankings."""

    models: list[ModelStats]
    total_audits: int
    last_updated: str
