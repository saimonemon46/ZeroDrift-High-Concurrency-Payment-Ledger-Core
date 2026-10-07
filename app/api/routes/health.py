"""Health check and readiness routes."""

import time

from fastapi import APIRouter, Depends

from app.api.dependencies import get_circuit_breaker
from app.api.schemas import HealthResponse
from app.infrastructure.resilience.circuit_breaker import CircuitBreaker

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and health check")
def health_check(
    circuit_breaker: CircuitBreaker = Depends(get_circuit_breaker),
) -> HealthResponse:
    """Returns engine health and circuit breaker state."""
    state = circuit_breaker.get_state().value
    return HealthResponse(
        status="healthy" if state != "OPEN" else "degraded",
        circuit_breaker_state=state,
        timestamp=time.time(),
    )
