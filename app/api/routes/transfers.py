"""Transfer execution routes with idempotency and circuit breaker protection."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.dependencies import (
    get_circuit_breaker,
    get_idempotency_store,
    get_transfer_use_case,
)
from app.api.schemas import TransferRequest, TransferResponse
from app.application.decorators.circuit_decorator import CircuitBreakerWrapper
from app.application.decorators.idempotency_decorator import IdempotencyWrapper
from app.application.use_cases.execute_transfer import (
    ExecuteTransferUseCase,
    TransferCommand,
    TransferResult,
)
from app.domain.strategies.fee_strategy import (
    FlatFeeStrategy,
    IFeeStrategy,
    MerchantFeeStrategy,
    P2PPeerFeeStrategy,
)
from app.domain.value_objects import Currency, Money
from app.infrastructure.resilience.circuit_breaker import CircuitBreaker
from app.ports.idempotency_port import IIdempotencyStore

router = APIRouter(prefix="/transfers", tags=["Transfers"])


def _resolve_fee_strategy(request: TransferRequest) -> IFeeStrategy:
    """Instantiates the appropriate commercial fee calculation strategy."""
    strat_type = request.fee_strategy_type.upper()
    if strat_type == "FLAT":
        return FlatFeeStrategy(flat_fee=request.flat_fee)
    if strat_type == "MERCHANT":
        rate = request.fee_rate if request.fee_rate > Decimal("0.00") else Decimal("0.015")
        return MerchantFeeStrategy(rate=rate)
    # Default to P2P Strategy
    return P2PPeerFeeStrategy(flat_fee=request.flat_fee, rate=request.fee_rate)


@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute an atomic double-entry money transfer",
)
def execute_transfer(
    request: TransferRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    use_case: ExecuteTransferUseCase = Depends(get_transfer_use_case),
    idempotency_store: IIdempotencyStore = Depends(get_idempotency_store),
    circuit_breaker: CircuitBreaker = Depends(get_circuit_breaker),
) -> TransferResponse:
    """Executes a financial transfer with full idempotency and resilience protection."""
    strategy = _resolve_fee_strategy(request)
    currency = Currency(request.currency.upper())
    amount = Money(request.amount, currency)

    command = TransferCommand(
        from_account_id=request.from_account_id,
        to_account_id=request.to_account_id,
        amount=amount,
        fee_strategy=strategy,
        fee_account_id=request.fee_account_id,
        idempotency_key=idempotency_key,
        metadata=request.metadata,
    )

    # Compose resilience: Idempotency -> Circuit Breaker -> ExecuteTransferUseCase
    resilient_execution = IdempotencyWrapper(
        target=CircuitBreakerWrapper(
            target=use_case.execute,
            circuit_breaker=circuit_breaker,
        ),
        store=idempotency_store,
    )

    result: TransferResult = resilient_execution(command)

    return TransferResponse(
        transaction_id=result.transaction_id,
        from_account_id=result.from_account_id,
        to_account_id=result.to_account_id,
        amount=str(result.amount.amount),
        currency=str(result.amount.currency),
        fee=str(result.fee.amount),
        from_balance_after=str(result.from_balance_after.amount),
        to_balance_after=str(result.to_balance_after.amount),
        status=result.status,
        created_at=result.created_at,
        ledger_entry_ids=result.ledger_entry_ids,
        metadata=result.metadata,
    )
