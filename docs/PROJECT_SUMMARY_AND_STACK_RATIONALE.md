# LedgerCore: Project Summary and Stack Rationale

## What I am building

- A high-concurrency financial ledger engine for transferring money safely between accounts.
- A system that acts as the source of truth for balances, transactions, and ledger history.
- A double-entry accounting system where every debit has a matching credit, so money is never created or destroyed.
- A payment-style backend designed to handle concurrent users, retry storms, partial failures, and operational stress without corrupting balances.

## Core goals

- Ensure account balances remain accurate under heavy load.
- Prevent double-spending and lost updates.
- Prevent duplicate processing caused by client retries or network issues.
- Keep ledger history immutable and auditable.
- Handle failures safely without leaving the system in an inconsistent state.
- Scale to support growing transaction volume and large ledger history.

## Why this matters

- In payment systems, correctness is more important than raw throughput.
- A small accounting bug can lead to financial loss, regulatory issues, and trust damage.
- The system must be resilient to concurrency, crashes, retries, and external service outages.

## What I am going to use and why

### Python
- Python is fast to prototype and easy to reason about for domain logic.
- It has a strong ecosystem for backend services and clean architecture patterns.
- It is suitable for modeling financial rules, edge cases, and validation logic clearly.

### FastAPI
- FastAPI gives a lightweight and modern API layer for building the ledger service.
- It is well-suited for synchronous request handling and clean route-based service design.
- It supports structured validation, clear schemas, and maintainable backend APIs.

### PostgreSQL
- PostgreSQL provides ACID guarantees, which are essential for money movement.
- It supports row-level locking, transactional integrity, and durable ledger storage.
- It is a reliable foundation for financial records and audit trails.

### Redis
- Redis provides fast in-memory coordination for idempotency checks and hot data access.
- It supports atomic operations such as check-and-set locking, which prevents duplicate execution.
- It helps reduce pressure on the database for frequently accessed values.

### Threading and worker pool
- The system is I/O-heavy, so using worker threads is appropriate for retries and background tasks.
- A bounded worker pool helps manage concurrency without overwhelming the system.
- Condition variables enable efficient sleeping instead of CPU-burning polling.

### Decimal for money
- Decimal avoids floating-point precision bugs such as $0.1 + 0.2$ rounding issues.
- Financial systems require exact accounting and stable rounding behavior.
- It is the correct type for amounts, fees, and balances.

### Indexing and table partitioning
- As ledger data grows, B+ tree indexes improve query performance substantially.
- Partitioning helps keep active ledger data manageable and reduces read overhead.
- This is necessary when the ledger reaches millions or hundreds of millions of rows.

### Custom cache and retry structures
- A custom O(1) LRU cache helps serve hot balance reads with low latency.
- A priority queue supports efficient retry scheduling with backoff.
- These structures are chosen because the system needs predictable performance under load.

### Hash chaining / audit trail
- Hash chaining provides tamper-evident accounting history.
- It allows the system to detect if historical ledger data was altered unexpectedly.
- This is important for trust, reconciliation, and financial auditability.

## Low-Level Design (LLD)

### 1. Domain model
- Account
  - account_id
  - balance
  - currency
  - status
- Transaction
  - transaction_id
  - sender_id
  - receiver_id
  - amount
  - status
  - created_at
- LedgerEntry
  - entry_id
  - account_id
  - transaction_id
  - type: debit or credit
  - amount
  - balance_after
  - created_at

### 2. Key business rules
- Every transfer must maintain double-entry accounting.
- A debit and credit must be recorded together.
- Balance checks happen before mutation.
- Account locks must be acquired before updates to avoid lost updates.
- Transfers must be idempotent using a unique request key.
- Invalid states must not be allowed.

### 3. Main components
- AccountRepository
  - reads and writes account state
- LedgerRepository
  - persists ledger entries
- TransactionService
  - orchestrates transfer execution
- LockManager
  - ensures consistent lock ordering
- IdempotencyStore
  - stores processed transfer keys and result metadata
- CacheService
  - reduces database load for hot reads
- RetryQueue
  - schedules async retries and downstream task recovery
- CircuitBreaker
  - protects dependencies from overload and failure storms

### 4. Transfer flow
- Receive transfer request
- Validate payload
- Check idempotency key
- Determine lock order for sender and receiver
- Acquire locks in canonical order
- Read current balances
- Validate funds and business rules
- Create ledger entries
- Commit transaction atomically
- Update cache and return response
- Store idempotency result for future retries

### 5. Concurrency strategy
- Use pessimistic row-level locking with SELECT ... FOR UPDATE.
- Lock accounts in a deterministic order to prevent circular waits and deadlocks.
- This ensures that only one transaction modifies an account row at a time.

### 6. Failure handling
- If a database write fails, the transaction rolls back.
- If a downstream dependency fails, the ledger remains consistent.
- If a client retries the same request, idempotency prevents duplicate charges.
- If a worker fails, the retry queue replays the operation safely.

### 7. Architectural layering
- Domain layer
  - entities, value objects, invariants
- Application layer
  - use cases, orchestration, business logic
- Ports / interfaces
  - repository and service contracts
- Infrastructure layer
  - PostgreSQL adapters, Redis adapters, HTTP handlers, worker pool

## Summary

I am building a production-grade payment ledger core: a fault-tolerant, concurrency-safe, auditable money engine that preserves accounting correctness even under heavy load, retries, and partial failures.

The chosen stack—Python, FastAPI, PostgreSQL, Redis, worker threads, and financial-safe data structures—fits the problem because ledger systems need correctness, durability, speed, and resilience above all else.
