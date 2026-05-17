# Adapter: DeepAgent → Hermes

## Overview
Defines how the Abacus DeepAgent routes tasks to the Hermes Agent runtime. DeepAgent is the FSH Command Interface; Hermes is the execution layer.

## Routing Protocol
1. DeepAgent receives a task with `execution_engine: "hermes"`
2. DeepAgent translates the task packet to a Hermes-native instruction
3. Hermes executes via `hermes-agent --query "Execute skill {skill_name}: {objective}"`
4. Hermes returns a plain text response
5. DeepAgent parses the response, writes result to PostgreSQL
6. Status callback emitted to n8n webhook

## Task Translation
```python
# FSH Task → Hermes Query
query = f"Execute skill {task.task_type}: {task.objective}"
if task.context_artifacts:
    query += f" | Context: {json.dumps(task.context_artifacts)}"

cmd = [
    "hermes-agent",
    "--query", query,
    "--max-turns", "5",
    "--quiet",
]
if provider:
    cmd.extend(["--provider", provider])
```

## Error Handling
- `FileNotFoundError` → Hermes binary not installed, return FAILED
- `TimeoutExpired` → Execution exceeded 300s, return FAILED
- Non-zero exit code → Parse stderr for error details, return FAILED
- `ApprovalRequiredError` → Route to approval pipeline

## Environment Variables
```bash
FSH_TASK_ID       = task.task_id
FSH_PILLAR        = task.pillar
FSH_TASK_TYPE     = task.task_type
FSH_OBJECTIVE     = task.objective
FSH_APPROVAL_TOKEN = approval_token (if applicable)
OPENROUTER_API_KEY = (required for OpenRouter models)
ANTHROPIC_API_KEY  = (required for Anthropic models)
```
