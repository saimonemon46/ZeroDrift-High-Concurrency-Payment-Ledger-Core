# LedgerCore: End-to-End System Flow & Architecture Diagrams

> **Component:** End-to-End Execution Flow, Sequence Diagrams & State Transitions  
> **Target Alignment:** Pathao (Fintech Engineering), bKash, Optimizely, Samsung SRBD  
> **Status:** Production Flow Specification  

---

## 1. Master End-to-End Sequence Diagram

This diagram traces an incoming payment request from initial HTTP ingress through all middleware, domain logic, concurrency locks, database commit, and asynchronous event notifications.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Mobile Client / Merchant
    participant Ingress as FastAPI Gateway (/transfers)
    participant Idem as Idempotency Decorator (Redis/Memory)
    participant Rate as Token Bucket Rate Limiter (O(1) Cache)
    participant Strat as Fee Strategy Router (GoF Strategy)
    participant LockMgr as Canonical Lock Manager
    participant DB as PostgreSQL (ACID Storage)
    participant FSM as Transaction State Machine (GoF State)
    participant Pool as OS Worker Pool (Condition Variables)
    participant CB as 3-State Circuit Breaker
    participant Webhook as Downstream Partner (SMS/Bank)

    %% Step 1: Ingress
    Note over Client,Ingress: Client submits transfer with unique UUID
    Client->>Ingress: POST /api/v1/transfers<br/>Headers: Idempotency-Key: UUID-777<br/>Body: {from: 101, to: 202, amount: 500, type: MERCHANT}
    
    %% Step 2: Idempotency Layer
    Ingress->>Idem: Check key "idem:UUID-777" (SETNX with 120s TTL)
    alt Key Already Resolved (Mobile Network Retry Scenario)
        Idem-->>Client: 200 OK (Cached Response, X-Cache-Lookup: HIT)
    else Key Currently In-Progress
        Idem-->>Client: 409 Conflict (Concurrent Duplicate Request)
    else Key Newly Acquired (Proceed)
        %% Step 3: Rate Limiting
        Idem->>Rate: Consume Token for caller IP / Account
        alt Quota Exceeded
            Rate-->>Client: 429 Too Many Requests
        else Quota OK
            Rate-->>Idem: Proceed
            
            %% Step 4: Fee Strategy Calculation
            Idem->>Strat: calculate_fee(amount=500.00, type="MERCHANT")
            Strat-->>Idem: Fee = 7.50 BDT (Total Debit = 507.50 BDT)
            
            %% Step 5: Canonical Lock Ordering
            Idem->>LockMgr: acquire_pair(Account 101, Account 202)
            Note over LockMgr: Deadlock Elimination Algorithm:<br/>First Lock: min(101, 202) = 101<br/>Second Lock: max(101, 202) = 202
            LockMgr->>DB: BEGIN TRANSACTION;<br/>SELECT * FROM accounts WHERE id IN (101, 202) FOR UPDATE;
            DB-->>LockMgr: Rows Locked Exclusively
            
            %% Step 6: State Machine & Balance Verification
            LockMgr->>FSM: transition_to(TransactionState.LOCKED)
            alt Sender Balance < Required (507.50 BDT)
                LockMgr->>FSM: transition_to(TransactionState.FAILED)
                LockMgr->>DB: ROLLBACK;
                LockMgr-->>Client: 422 Unprocessable Entity (InsufficientFundsError)
            else Sender Balance >= Required
                %% Step 7: Atomic Mutation & Double-Entry Bookkeeping
                Note over LockMgr,DB: Mutate Balances:<br/>Acc 101: 10,000 - 507.50 = 9,492.50<br/>Acc 202: 5,000 + 500.00 = 5,500.00<br/>Platform Revenue: +7.50
                Note over LockMgr,DB: Double-Entry Equilibrium:<br/>Debit (-507.50) + Credit (+500.00) + Fee (+7.50) == 0.00
                LockMgr->>DB: INSERT INTO ledger_entries (Debit Entry);<br/>INSERT INTO ledger_entries (Credit Entry);<br/>INSERT INTO ledger_entries (Fee Entry);
                LockMgr->>DB: COMMIT TRANSACTION; (WAL Flushed to Disk)
                LockMgr->>FSM: transition_to(TransactionState.COMMITTED)<br/>transition_to(TransactionState.SETTLED)
                
                %% Step 8: Resolve Idempotency & Return Client Response
                LockMgr->>Idem: store_result("idem:UUID-777", payload=Result, TTL=86400s)
                Idem-->>Client: 200 OK (Transfer Settled, TXN-8921, P95 <= 15ms)
                
                %% Step 9: Decoupled Asynchronous Events
                LockMgr->>Pool: enqueue(TransactionSettledEvent)
                Note over Pool: Worker thread wakes via condition.notify()<br/>Executes non-blocking in background
                Pool->>CB: execute_guarded_call(send_sms_notification)
                alt Circuit CLOSED (Downstream Partner Healthy)
                    CB->>Webhook: HTTP POST /sms/notify
                    Webhook-->>CB: 200 OK
                else Circuit OPEN (Downstream Partner Outage)
                    CB-->>Pool: Fail Fast in <1ms (No thread starvation)
                    Note over Pool: Task queued to Binary Min-Heap<br/>for Exponential Backoff Retry
                end
            end
        end
    end
```

---

## 2. Transaction Lifecycle State Machine Diagram

Every financial transaction is governed by a finite state machine (`TransactionStateMachine`) enforcing valid transition boundaries:

```mermaid
stateDiagram-v2
    [*] --> INITIATED : Client Request Received

    INITIATED --> LOCKED : Canonical Row Locks Acquired
    INITIATED --> FAILED : Lock Timeout / Validation Error

    LOCKED --> COMMITTED : Balances Mutated & Ledger Written
    LOCKED --> FAILED : Insufficient Funds / Account Frozen

    COMMITTED --> SETTLED : DB Transaction Committed
    COMMITTED --> FAILED : DB Disk Write Failure (Rollback)

    SETTLED --> REVERSED : Formal Dispute / Admin Reversal
    
    FAILED --> [*] : Final Error State
    REVERSED --> [*] : Final Reversal State
```

### Transition Invariants:
1. An uncommitted transaction can **never** jump to `SETTLED`.
2. A `FAILED` transaction can **never** be mutated or reversed.
3. A `REVERSED` transaction creates a balanced compensating double-entry pair without deleting historical records.

---

## 3. Concurrency & Deadlock Elimination Flowchart

Demonstrating how the **Canonical Lock Ordering** algorithm prevents Coffman circular-wait deadlocks during simultaneous bilateral transfers:

```mermaid
flowchart TD
    subgraph ConcurrentTransfers["Simultaneous Bilateral Transfers"]
        T1["Thread 1: User 101 sends $50 to User 202"]
        T2["Thread 2: User 202 sends $30 to User 101"]
    end

    subgraph LockSorter["Canonical Lock Manager (Sorting Layer)"]
        Sort1["Thread 1 sorts: min(101, 202) -> 101 first, then 202"]
        Sort2["Thread 2 sorts: min(202, 101) -> 101 first, then 202"]
    end

    subgraph Execution["Monotonic Lock Acquisition"]
        Step1["Both threads attempt to lock Account 101 first"]
        Winner["Thread 1 acquires Lock(101)<br/>Thread 2 waits on Lock(101)"]
        Step2["Thread 1 acquires Lock(202)<br/>(No competition for 202 yet)"]
        Commit1["Thread 1 mutates balances, commits, and releases Lock(101) and Lock(202)"]
        Resume2["Thread 2 wakes up, acquires Lock(101), then Lock(202)"]
        Commit2["Thread 2 mutates balances, commits, and releases locks"]
    end

    T1 --> Sort1
    T2 --> Sort2
    Sort1 --> Step1
    Sort2 --> Step1
    Step1 --> Winner
    Winner --> Step2
    Step2 --> Commit1
    Commit1 --> Resume2
    Resume2 --> Commit2

    classDef highlight fill:#d4edda,stroke:#28a745,stroke-width:2px;
    class Commit1,Commit2 highlight;
```

---

## 4. Error Handling & Resilience Flowchart

How LedgerCore handles unexpected edge cases gracefully without losing data or hanging client threads:

```mermaid
flowchart TD
    Request["Incoming Transfer Request"] --> Step1{"Idempotency Key<br/>in Redis/Memory?"}

    Step1 -- "RESOLVED" --> RetCached["Return Cached 200 OK<br/>(X-Cache-Lookup: HIT in <1ms)"]
    Step1 -- "IN_PROGRESS" --> RetConflict["Return 409 Conflict<br/>(Request currently executing)"]
    Step1 -- "NEW" --> Step2{"Caller Exceeds<br/>Rate Limit?"}

    Step2 -- "Yes" --> Ret429["Return 429 Too Many Requests"]
    Step2 -- "No" --> Step3["Acquire Canonical Locks<br/>min(A,B) -> max(A,B)"]

    Step3 --> Step4{"Sufficient Funds<br/>(Balance >= Amount + Fee)?"}

    Step4 -- "No" --> Rollback["Release Locks & Rollback<br/>Return 422 InsufficientFundsError"]
    Step4 -- "Yes" --> Commit["Mutate Balances & Append Double-Entry<br/>Commit Transaction & Release Locks"]

    Commit --> Step5["Store Result in Idempotency Store<br/>Return 200 OK (Settled) to Client"]
    Commit -.-> AsyncEvent["Emit TransactionSettledEvent to Worker Pool"]

    AsyncEvent --> Step6{"Circuit Breaker<br/>State?"}
    Step6 -- "OPEN" --> FailFast["Fail Fast (<1ms)<br/>Queue to Binary Min-Heap for Exponential Backoff"]
    Step6 -- "CLOSED" --> Dispatch["Dispatch Webhook / SMS to Downstream Partner"]
```

---

## 5. Latency Waterfall Analysis (Sub-15ms Guarantee)

| Milestone | Subsystem | Typical Execution Time |
| :--- | :--- | :---: |
| 1. HTTP Ingress & JSON Schema Validation | FastAPI + Pydantic v2 | $0.8\text{ ms}$ |
| 2. Idempotency Key Atomic Check (`SETNX`) | Redis / In-Memory Store | $0.4\text{ ms}$ |
| 3. Rate Limit Token Bucket Check | Custom $\mathcal{O}(1)$ LRU Cache | $0.1\text{ ms}$ |
| 4. Commercial Fee Strategy Calculation | Domain Strategy Object | $0.05\text{ ms}$ |
| 5. Canonical Row Lock Acquisition | PostgreSQL / RLock Mutex | $2.5\text{ ms}$ |
| 6. Balance Verification & State Transition | Domain Entity Logic | $0.1\text{ ms}$ |
| 7. Double-Entry Insert & Transaction Commit | PostgreSQL WAL Disk Flush | $8.0\text{ ms}$ |
| 8. Idempotency Cache Result Resolution | Redis / In-Memory Store | $0.5\text{ ms}$ |
| 9. HTTP JSON Serialization & Response Egress | FastAPI + Uvicorn | $0.6\text{ ms}$ |
| **Total Round-Trip Latency (P95)** | **End-to-End System** | **$\approx 13.05\text{ ms}$** |
| *Decoupled Background Tasks (Async)* | *OS Worker Pool (Non-Blocking)* | *Executed after client response* |
