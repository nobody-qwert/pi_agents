# Lean Task Packet

This template defines the handoff contract for delegated tasks.

```text
TASK_ID: <stable short id>

GOAL:
<one observable outcome>

ACCEPTANCE_CRITERIA:
- <behavior that can be checked>

EXPECTED_PATHS:
- <path>

PROTECTED_PATHS:
- <path that must not change, or none>

ENTRY_SYMBOLS:
- <verified symbol or starting file>

DEPENDS_ON:
- <task id whose verified outcome is required first, or none>

ACCEPTANCE_COMMANDS:
- <exact bounded command>

CONSTRAINTS:
- <public behavior or boundary that must remain unchanged>

KNOWN_FACTS:
- <fact verified from repository evidence>

KNOWN_FAILED_APPROACHES:
- <short fingerprint only, or none>

OUTPUT_CONTRACT:
Return status, concise summary, files changed, checks, remaining risk, and a failure fingerprint plus next recommendation when incomplete.
```
