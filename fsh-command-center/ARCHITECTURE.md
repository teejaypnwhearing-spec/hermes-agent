# FSH Command Center - Architectural Decisions

This document outlines key architectural decisions made in the design and implementation of the FSH Command Center, providing rationale and guidance for future development and maintenance.

## 1. Event-Driven Approval vs. Polling

**Decision**: The FSH Command Center utilizes an event-driven mechanism for handling task approvals, specifically by raising an `ApprovalRequiredError` within the adapter layer, which triggers the creation of an `approval_request` in the database and an asynchronous notification via n8n. This approach replaces an earlier design that relied on blocking polling for approval status.

**Rationale**:
*   **Efficiency**: Polling mechanisms, especially in a distributed system, consume resources (CPU, network I/O) unnecessarily as they repeatedly check for status changes. Event-driven approvals, conversely, only activate when a specific event (an approval request or decision) occurs, leading to more efficient resource utilization.
*   **Responsiveness**: Event-driven systems are inherently more responsive. As soon as an approval decision is made, the system can react immediately, reducing latency in task execution. Polling introduces delays, as the system must wait for the next polling interval to detect a change.
*   **Scalability**: Polling can become a bottleneck as the number of tasks and approval requests grows. Each poll adds load to the database and orchestrator. An event-driven model scales more gracefully, as the notification and processing of approvals are decoupled and can be handled independently.
*   **Decoupling**: The event-driven approach decouples the task execution flow from the human approval process. The orchestrator does not block while waiting for approval; instead, it hands off the approval responsibility and resumes when an explicit approval event is received. This improves system resilience and maintainability.

## 2. Pillar Defaults: Code vs. Configuration

**Decision**: Pillar-specific default values for `execution_engine`, `approval_level`, `compliance_flags`, and `storage_targets` are defined directly within the `config/pillar_defaults.py` file as Python dataclasses, rather than being stored in a dynamic configuration system or database.

**Rationale**:
*   **Version Control and Auditability**: Storing defaults in code allows them to be managed under version control (Git). This provides a clear history of changes, facilitates code reviews, and ensures that default behaviors are consistently applied across different environments and deployments. Changes to critical operational parameters are auditable alongside code changes.
*   **Type Safety and Validation**: Python dataclasses and type hints provide compile-time and runtime validation of the default values, reducing the likelihood of configuration errors. This is particularly important for sensitive parameters like `approval_level` and `compliance_flags`.
*   **Developer Experience**: Developers can easily inspect, understand, and modify pillar defaults directly within the codebase using familiar programming constructs. This reduces the cognitive load associated with managing external configuration systems.
*   **Deployment Consistency**: Embedding defaults in code ensures that the system starts with the correct baseline configuration, minimizing the risk of misconfiguration during deployment. While overrides are possible (and encouraged for specific tasks), the strong defaults provide a reliable foundation.
*   **Performance**: Retrieving defaults from in-memory Python objects is faster than querying a database or an external configuration service for every task submission.

## 3. Adding New Adapters Without Breaking Isolation

**Guidance**: To add a new execution engine (adapter) to the FSH Command Center while maintaining pillar isolation and architectural integrity, follow these steps:

1.  **Define the Adapter Interface**: The new adapter must inherit from `FSHAdapterBase` (defined in `adapters/base.py`) and implement its abstract methods: `translate_in`, `execute`, and `translate_out`. This ensures adherence to the core contract for task processing.

2.  **Enforce Pillar Isolation**: Within the `translate_in` method of the new adapter, explicitly define and check the `MANUS_PILLARS` (or equivalent for the new adapter) that the adapter is authorized to handle. If a task's `pillar` does not match the adapter's allowed pillars, raise a `PillarIsolationError`. This prevents cross-pillar state leakage and enforces the principle of least privilege.

3.  **Implement Approval Gates**: If the new adapter handles tasks that involve external actions, financial transactions, or irreversible operations, it must implement appropriate approval gates. This typically involves checking the `approval_level` in the incoming `FSHTask` and raising an `ApprovalRequiredError` if the required level is not met or an `approval_token` is missing for a previously requested approval.

4.  **Map the Adapter in the Orchestrator**: Update the `_ADAPTER_MAP` dictionary in `orchestrator/orchestrator.py` to associate the new execution engine string (e.g., "new_engine") with the new adapter's class. This allows the orchestrator to correctly route tasks to the new adapter.

5.  **Update Database Enums**: If the new execution engine introduces a new `execution_engine_enum` value, update the `execution_engine_enum` type in `database/core_schema.sql` (or via a new migration script) to include the new engine. This ensures database-level consistency.

6.  **Configure Pillar Defaults**: Add an entry for the new adapter in `config/pillar_defaults.py` if it is intended to be the default execution engine for any existing or new pillars. Define its default `approval_level`, `compliance_flags`, and `storage_targets`.

7.  **Testing**: Develop a comprehensive suite of unit and integration tests for the new adapter, similar to `tests/test_adapters.py`. These tests should cover:
    *   `translate_in` logic, including pillar isolation and payload validation.
    *   `execute` method, including approval gate enforcement and error handling.
    *   `translate_out` method, ensuring correct result formatting.
    *   Mock external API calls to ensure the adapter interacts correctly with its target system without relying on live services during unit testing.

By following these guidelines, new adapters can be integrated into the FSH Command Center in a controlled and secure manner, preserving the system's core architectural principles.
