# LedgerCore: Module & Component Specifications

> **Target:** Exhaustive Class, Method, Signature & Ownership Specification  
> **Standards:** Python 3.12+ Strict Typing (`typing`), Clean DataClasses, Immutable Value Objects  

---

## 1. Directory & Ownership Blueprint

```text
ledger_core/app/
├── domain/                      [OWNER: Core Invariant Lead]
│   ├── value_objects.py         # Money, Currency, AccountId, TxId
│   ├── models.py                # Account, Transaction, LedgerEntry
│   ├── state_machine.py         # TransactionState & StateMachine
│   └── strategies/
│       └── fee_strategy.py      # IFeeStrategy, P2PStrategy, MerchantStrategy
│
├── ports/                       [OWNER: Architecture Lead]
│   ├── repository_ports.py      # IAccountRepository, ILedgerRepository
│   ├── cache_port.py            # ICachePort
│   ├── idempotency_port.py      # IIdempotencyStore
│   └── event_publisher_port.py  # IEventPublisher
│
├── application/                 [OWNER: Application Orchestration Lead]
│   ├── use_cases/
│   │   ├── execute_transfer.py
│   │   ├── audit_reconciliation.py
│   │   └── query_balance.py
│   └── decorators/
│       ├── idempotency_decorator.py
│       └── circuit_decorator.py
│
├── infrastructure/              [OWNER: Systems & Core CS Lead]
│   ├── concurrency/
│   │   └── lock_manager.py      # CanonicalLockManager
│   ├── dsa/
│   │   ├── lru_ttl_cache.py     # LRUTTLCache
│   │   └── priority_retry_heap.py # PriorityRetryHeap
│   ├── os_threading/
│   │   └── worker_pool.py       # ThreadSafeWorkerPool
│   ├── resilience/
│   │   └── circuit_breaker.py   # CircuitBreaker
│   └── persistence/
│       └── memory_repositories.py # In-Memory atomic repositories
│
└── api/                         [OWNER: Ingress & Protocol Lead]
    ├── schemas.py               # Pydantic v2 schemas
    ├── dependencies.py          # Container & DI graph
    └── routes/
        ├── transfers.py
        ├── accounts.py
        └── audit.py
```

---

## 2. Domain Layer Specifications (`app/domain/`)

### 2.1 Value Objects (`app/domain/value_objects.py`)

#### `Money` (Immutable Value Object)
```python
@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "BDT"

    def __post_init__(self):
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money amount must be a decimal.Decimal instance.")
        if self.amount.as_tuple().exponent < -2:
            raise ValueError("Money precision cannot exceed 2 decimal places.")

    def add(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def is_positive(self) -> bool:
        return self.amount > Decimal("0.00")

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")
```

### 2.2 Entities (`app/domain/models.py`)

#### `Account` (Entity)
```python
@dataclass
class Account:
    account_id: str
    owner_name: str
    balance: Money
    status: str = "ACTIVE"  # ACTIVE, FROZEN, CLOSED
    lock: threading.RLock = field(default_factory=threading.RLock)

    def debit(self, amount: Money) -> None:
        if not self.has_sufficient_balance(amount):
            raise InsufficientFundsError(f"Account {self.account_id} balance {self.balance} < {amount}")
        self.balance = self.balance.subtract(amount)

    def credit(self, amount: Money) -> None:
        self.balance = self.balance.add(amount)

    def has_sufficient_balance(self, amount: Money) -> bool:
        return self.balance.amount >= amount.amount
```

#### `LedgerEntry` (Immutable Entity)
```python
@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    transaction_id: str
    account_id: str
    amount: Decimal  # Positive for Credit, Negative for Debit
    balance_after: Decimal
    entry_type: str  # DEBIT or CREDIT
    timestamp: float
```

### 2.3 State Machine (`app/domain/state_machine.py`)
```python
class TransactionState(str, Enum):
    INITIATED = "INITIATED"
    LOCKED = "LOCKED"
    COMMITTED = "COMMITTED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"

class TransactionStateMachine:
    VALID_TRANSITIONS: dict[TransactionState, set[TransactionState]] = {
        TransactionState.INITIATED: {TransactionState.LOCKED, TransactionState.FAILED},
        TransactionState.LOCKED: {TransactionState.COMMITTED, TransactionState.FAILED},
        TransactionState.COMMITTED: {TransactionState.SETTLED, TransactionState.FAILED},
        TransactionState.SETTLED: {TransactionState.REVERSED},
        TransactionState.FAILED: set(),
        TransactionState.REVERSED: set(),
    }
```

---

## 3. Ports Layer Specifications (`app/ports/`)

### 3.1 Repository Ports (`app/ports/repository_ports.py`)
```python
class IAccountRepository(ABC):
    @abstractmethod
    def get_by_id(self, account_id: str) -> Optional[Account]: ...

    @abstractmethod
    def save(self, account: Account) -> None: ...

    @abstractmethod
    def get_all(self) -> list[Account]: ...


class ILedgerRepository(ABC):
    @abstractmethod
    def append_entry(self, entry: LedgerEntry) -> None: ...

    @abstractmethod
    def get_entries_for_account(self, account_id: str) -> list[LedgerEntry]: ...

    @abstractmethod
    def get_all_entries(self) -> list[LedgerEntry]: ...
```

### 3.2 Idempotency & Cache Ports (`app/ports/`)
```python
class IIdempotencyStore(ABC):
    @abstractmethod
    def try_acquire(self, key: str, ttl_seconds: int = 120) -> bool: ...

    @abstractmethod
    def get_result(self, key: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def store_result(self, key: str, result: dict[str, Any], ttl_seconds: int = 86400) -> None: ...


class ICachePort(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    def put(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...
```

---

## 4. Application Layer Specifications (`app/application/`)

### 4.1 Use Case: `ExecuteTransferUseCase`
* **Signature:** `execute(command: TransferCommand) -> TransferResult`
* **Workflow:**
  1. Retrieve `sender` and `receiver` accounts from repository.
  2. Compute commercial fee using `IFeeStrategy`.
  3. Verify `sender.has_sufficient_balance(amount + fee)`.
  4. Acquire locks in canonical lexicographical sequence via `CanonicalLockManager`.
  5. State transition: `INITIATED` $\rightarrow$ `LOCKED`.
  6. Execute atomic mutations:
     * `sender.debit(amount + fee)`
     * `receiver.credit(amount)`
  7. Generate immutable `LedgerEntry` pair.
  8. Persist to repository.
  9. State transition: `LOCKED` $\rightarrow$ `COMMITTED` $\rightarrow$ `SETTLED`.
  10. Publish `TransactionSettledEvent` asynchronously.
  11. Return `TransferResult(transaction_id, status="SETTLED")`.

### 4.2 Use Case: `AuditReconciliationUseCase`
* **Signature:** `reconcile() -> AuditReport`
* **Algorithm:**
  1. Acquire global read-consistency snapshot.
  2. Compute $\text{Total Balances} = \sum \text{Account.balance.amount}$.
  3. Compute $\text{Total Ledger Net} = \sum \text{LedgerEntry.amount}$.
  4. Calculate $\Delta = \text{Total Balances} - \text{Total Ledger Net}$.
  5. Invariant Assertion: $\Delta == \text{Decimal('0.00')}$.
  6. Return `AuditReport(total_accounts, total_entries, discrepancy, status="BALANCED")`.

---

## 5. Infrastructure Layer Specifications (`app/infrastructure/`)

### 5.1 `CanonicalLockManager` (`app/infrastructure/concurrency/lock_manager.py`)
```python
class CanonicalLockManager:
    """Eliminates Coffman Circular Wait deadlocks via deterministic sorting."""

    @contextmanager
    def acquire_pair(self, acc_a: Account, acc_b: Account):
        if acc_a.account_id == acc_b.account_id:
            raise ValueError("Cannot lock identical accounts.")

        # Deterministic sorting (min first, then max)
        first, second = (acc_a, acc_b) if acc_a.account_id < acc_b.account_id else (acc_b, acc_a)

        with first.lock:
            with second.lock:
                yield (acc_a, acc_b)
```

### 5.2 `LRUTTLCache` (`app/infrastructure/dsa/lru_ttl_cache.py`)
* Implements `ICachePort`.
* Uses `threading.RLock` around doubly-linked node pointer adjustments.
* Stores timestamps for active and passive TTL evictions.

### 5.3 `ThreadSafeWorkerPool` (`app/infrastructure/os_threading/worker_pool.py`)
* Thread pool managing $N$ background daemon threads.
* Implements `enqueue(task: Callable) -> None`.
* Intercepts `SIGINT` / `SIGTERM` to set `_shutdown_requested = True`, waking all sleeping threads with `condition.notify_all()`, and joining workers until queue size is 0.

### 5.4 `CircuitBreaker` (`app/infrastructure/resilience/circuit_breaker.py`)
* Tracks rolling failures in a 10s sliding window.
* Failure threshold: 50% failures out of minimum 5 requests.
* State transitions: `CLOSED` $\rightarrow$ `OPEN` $\rightarrow$ `HALF_OPEN`.

---

## 6. Ingress API Layer Specifications (`app/api/`)

### 6.1 Pydantic v2 Schemas (`app/api/schemas.py`)
```python
class TransferRequestSchema(BaseModel):
    from_account_id: str = Field(..., min_length=3, max_length=64)
    to_account_id: str = Field(..., min_length=3, max_length=64)
    amount: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    payment_type: str = Field(default="P2P", description="P2P or MERCHANT")

class TransferResponseSchema(BaseModel):
    transaction_id: str
    from_account_id: str
    to_account_id: str
    debited_amount: Decimal
    fee_charged: Decimal
    status: str
    timestamp: float

class AuditResponseSchema(BaseModel):
    total_account_balances: Decimal
    total_ledger_net: Decimal
    discrepancy: Decimal
    status: str
    ledger_entries_count: int
```
