# Must-Know Concepts Before Starting LedgerCore

## Database & transaction correctness
- ACID
- Transaction isolation levels
- Row-level locking
- Pessimistic locking
- Optimistic locking
- Lost update
- Dirty read
- Non-repeatable read
- Phantom read
- Write-ahead logging
- Atomic commit
- Rollback semantics
- Transaction boundary design
- Idempotent writes
- Append-only ledger design
- Double-entry accounting
- Ledger invariants
- Reconciliation
- Audit trail integrity

## Concurrency & locking
- Race condition
- Critical section
- Mutex
- Reentrant lock
- Condition variable
- Deadlock
- Circular wait
- Lock ordering
- Canonical lock ordering
- Thread safety
- Thread pool
- Producer-consumer pattern
- Busy waiting vs sleeping
- Shared vs exclusive lock
- Starvation
- Livelock

## Core CS / system design
- CAP theorem
- Consistency
- Availability
- Partition tolerance
- Eventual consistency
- At-most-once delivery
- At-least-once delivery
- Exactly-once semantics
- Retry logic
- Exponential backoff
- Full jitter
- Rate limiting
- Token bucket
- Circuit breaker
- Bulkhead
- Timeout handling
- Idempotency key
- Retry storm
- Thundering herd
- Backpressure
- Queueing
- Sagas
- Compensating transaction
- Outbox pattern
- Consumer offset handling

## Data structures & algorithms
- Hash map
- Doubly linked list
- Sentinel nodes
- LRU cache
- TTL expiration
- O(1) operations
- O(log n) heap operations
- Binary heap
- Priority queue
- B+ tree
- Index-only scan
- Partition pruning
- Range partitioning
- Hash chaining
- Merkle-style audit trail

## Distributed systems
- Sharding
- Horizontal scaling
- Cross-shard transactions
- Distributed locks
- Distributed consensus basics
- Message queues
- Kafka/RabbitMQ concepts
- Failure detection
- Timeouts and retries
- Network partitions
- Leader/follower model
- Fan-out / async processing
- Event-driven architecture

## OS / runtime
- Process vs thread
- GIL
- I/O-bound vs CPU-bound
- Signal handling
- SIGTERM
- Graceful shutdown
- Thread scheduling
- Blocking I/O
- Non-blocking I/O
- Synchronization primitives
- Context switching
- Memory model basics

## LLD / architecture
- Clean architecture
- Domain layer
- Application layer
- Infrastructure layer
- Ports and adapters
- Dependency inversion
- Interface segregation
- Single responsibility
- Open/closed principle
- Strategy pattern
- State machine
- Decorator pattern
- Observer pattern
- Factory pattern
- Value object
- Entity
- Aggregate
- Repository pattern
- Unit of work

## Financial-specific concepts
- Ledger balance
- Debit
- Credit
- Settlement
- Reversal
- Refund
- Dispute handling
- Reserve funds
- Escrow
- Money movement invariants
- Immutable audit logs
- Dual write safety
- Account state transitions

## Best starting priority
- ACID
- Row-level locking
- Pessimistic locking
- Deadlocks
- Lock ordering
- Idempotency
- Retry logic
- Double-entry accounting
- Transaction boundaries
- Concurrency patterns
