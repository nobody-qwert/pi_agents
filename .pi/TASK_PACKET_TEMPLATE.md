# Lean Task Packet

This file is a template for the supervisor or extension. It is not automatically added to model context.

```text
TASK_ID: <stable short id>

GOAL:
<one observable outcome>

ACCEPTANCE_CRITERIA:
- <behavior that can be checked>

ALLOWED_PATHS:
- <path>

ENTRY_SYMBOLS:
- <symbol or starting file>

ACCEPTANCE_COMMANDS:
- <exact bounded command>

CONSTRAINTS:
- <public behavior or boundary that must remain unchanged>

KNOWN_FACTS:
- <fact verified from repository evidence>

KNOWN_FAILED_APPROACHES:
- <short fingerprint only, or none>

OUTPUT_CONTRACT:
Return status, concise summary, files changed, checks, remaining risk, and a failure fingerprint when incomplete.
```

