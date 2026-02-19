from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import get_db
from api.models.job import Job, JobStatus
from api.schemas.leaderboard import LeaderboardResponse, ModelStats, SeverityBreakdown


router = APIRouter(prefix='/leaderboard', tags=['leaderboard'])

DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


def _count_vulnerabilities_by_severity(result: dict | None) -> dict[str, int]:
    """Extract vulnerability counts by severity from a job result."""
    counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
    if not result or not isinstance(result, dict):
        return counts

    vulns = result.get('vulnerabilities', [])
    if not isinstance(vulns, list):
        return counts

    for vuln in vulns:
        if not isinstance(vuln, dict):
            continue
        severity = vuln.get('severity', '').lower()
        if severity in counts:
            counts[severity] += 1

    return counts


@router.get('')
async def get_leaderboard(session: DbSessionDep) -> LeaderboardResponse:
    """Get model performance leaderboard based on completed audits."""
    # Get aggregate stats per model
    stmt = (
        select(
            Job.model,
            func.count().label('total_jobs'),
            func.sum(case((Job.status == JobStatus.succeeded, 1), else_=0)).label('succeeded_jobs'),
            func.sum(case((Job.status == JobStatus.failed, 1), else_=0)).label('failed_jobs'),
            func.avg(
                case(
                    (
                        Job.status == JobStatus.succeeded,
                        func.extract('epoch', Job.finished_at - Job.started_at),
                    ),
                    else_=None,
                )
            ).label('avg_duration'),
        )
        .where(Job.status.in_([JobStatus.succeeded, JobStatus.failed]))
        .group_by(Job.model)
    )

    result = await session.execute(stmt)
    model_rows = result.fetchall()

    # Get all successful jobs with results to calculate vulnerability stats
    vuln_stmt = (
        select(Job.model, Job.result)
        .where(Job.status == JobStatus.succeeded, Job.result.isnot(None))
    )
    vuln_result = await session.execute(vuln_stmt)
    vuln_rows = vuln_result.fetchall()

    # Aggregate vulnerability stats by model
    model_vuln_stats: dict[str, dict] = {}
    for row in vuln_rows:
        model = row.model
        if model not in model_vuln_stats:
            model_vuln_stats[model] = {
                'total_vulns': 0,
                'audit_count': 0,
                'severity': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0},
            }

        counts = _count_vulnerabilities_by_severity(row.result)
        total = sum(counts.values())
        model_vuln_stats[model]['total_vulns'] += total
        model_vuln_stats[model]['audit_count'] += 1
        for sev, count in counts.items():
            model_vuln_stats[model]['severity'][sev] += count

    # Build response
    models: list[ModelStats] = []
    total_audits = 0

    for row in model_rows:
        total_jobs = row.total_jobs or 0
        succeeded = row.succeeded_jobs or 0
        failed = row.failed_jobs or 0
        total_audits += total_jobs

        vuln_stats = model_vuln_stats.get(row.model, {
            'total_vulns': 0,
            'audit_count': 0,
            'severity': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0},
        })

        avg_vulns = (
            vuln_stats['total_vulns'] / vuln_stats['audit_count']
            if vuln_stats['audit_count'] > 0
            else 0.0
        )

        models.append(ModelStats(
            model=row.model,
            total_jobs=total_jobs,
            succeeded_jobs=succeeded,
            failed_jobs=failed,
            success_rate=succeeded / total_jobs if total_jobs > 0 else 0.0,
            avg_duration_seconds=round(row.avg_duration, 2) if row.avg_duration else None,
            total_vulnerabilities=vuln_stats['total_vulns'],
            avg_vulnerabilities_per_audit=round(avg_vulns, 2),
            severity_breakdown=SeverityBreakdown(**vuln_stats['severity']),
        ))

    # Sort by success rate, then by total jobs
    models.sort(key=lambda m: (-m.success_rate, -m.total_jobs))

    return LeaderboardResponse(
        models=models,
        total_audits=total_audits,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )
