# Generic Nested-Loop Orchestration Plan

Status: draft for deterministic LangGraph production-design iteration
Scope: architecture and rollout plan only; implementation begins after the
technical design and first milestone are approved

## 1. Purpose

Build a generic production delivery system that can coordinate large outcomes
across software, legal-document, research, operations, and other domains.

The harness must:

- turn a broad request into an explicit outcome and a versioned design anchor;
- represent the work as a dependency graph that can be recursively decomposed;
- give every worker one narrow, independently verifiable responsibility;
- control what context crosses each handoff;
- integrate component outputs against shared interfaces and invariants;
- route defects and discoveries back to the correct loop;
- retain traceability from the original request to final evidence;
- materialize model-directed work only in a disposable VM copied from an
  allowlisted host folder;
- provide guest-local checkpoints and rollback without exposing the host
  repository to agent tools;
- promote a reviewed result to host Git only after an explicit user decision;
- remain sequential, foreground, fresh-context, and non-nested under the
  repository's current execution rules.

The intended model is not specific to source code. A "deliverable" may be a
module, a test suite, a contract, a policy, a filing package, a research report,
or an assembled operating handbook.

## 2. Problem to Solve

A simple agent pipeline can handle a small or already well-understood change:

```text
request -> investigator -> optional design worker -> task packets
        -> domain worker -> local verification -> optional reviewer
```

Its leaf-task discipline should be retained. The weak point is before and after
the leaf task:

1. The investigator or design worker may have to understand, design, decompose,
   and package a large outcome in one context.
2. There is no persistent design baseline shared by all later roles.
3. A flat packet list cannot fully express a large tree or dependency graph.
4. There is no explicit integration work between component verification and
   final outcome verification.
5. Worker discoveries have no durable issue channel or impact-analysis step.
6. A design correction can accidentally invalidate completed work without that
   invalidation being recorded.
7. Existing fields such as `ENTRY_SYMBOLS` and `ACCEPTANCE_COMMANDS` assume a
   software repository and do not generalize to documents or human approvals.
8. A single orchestrator agent owns too many kinds of judgment: routing, design
   escalation, packet validation, execution, integration, failure recovery, and
   completion.

The goal is therefore to add a design and planning control plane around the
existing worker loop, not to make implementation workers more powerful.

## 3. Core Model

### 3.1 Four planes

The system separates responsibilities into four planes:

| Plane | Responsibility | Must not own |
| --- | --- | --- |
| Control | Run state, routing, readiness, retries, checkpoints | Domain design or implementation |
| Design | Outcome architecture, boundaries, interfaces, invariants, decisions | Execution scheduling or implementation |
| Delivery | Decomposition, handoff packages, implementation, assembly | Changing the approved outcome or design silently |
| Assurance | Local checks, integration checks, final evidence, independent review | Repairing the artifact it judges |

No single role should both define a contract, implement it, and be the final
judge of whether it was satisfied.

### 3.2 Recursive work graph

The logical unit is a `WORK_NODE`, not an agent call. A node can be:

- `OUTCOME`: the user's complete requested result;
- `SYSTEM`: a major subsystem or deliverable family;
- `WORK_PACKAGE`: one responsibility with a coherent result;
- `LEAF_TASK`: a result small enough for one worker and one verification boundary;
- `INTEGRATION`: assembly and cross-component consistency work;
- `VERIFICATION`: independent evidence for a node or the final outcome;
- `DECISION`: a user, design, regulatory, or product choice that blocks descendants.

Every non-leaf node is either decomposed or explicitly justified as directly
executable. Nodes form a directed acyclic graph: the parent relation expresses
decomposition, while dependency edges express execution order and interface
requirements. A pure tree is insufficient because several components may rely
on the same policy, interface, or foundational deliverable.

Recursion is orchestrated, not nested at runtime. The coordinator invokes a
fresh planner on one oversized node, records its children, and repeats until all
executable nodes meet the leaf criteria. Runtime nodes never create new
executable topologies or grant new authority.

### 3.3 Leaf criteria

A node may be handed to a worker only when all of the following are true:

- it has one observable outcome;
- it has one owning responsibility;
- required inputs and expected outputs are explicit;
- relevant design rules and interfaces are cited by version and section;
- dependencies are either verified or declared non-blocking;
- acceptance can be decided at one independent verification boundary;
- failure can be isolated without undoing unrelated work;
- the required context fits in one fresh worker context;
- the worker has no need to make an unapproved design or product decision.

If any criterion fails, the node returns to decomposition or design instead of
being sent to a worker with a larger prompt.

### 3.4 Deterministic runtime contract

The production runtime uses LangGraph for a **fixed control graph**. An LLM
never selects an arbitrary Python function, mutates the runtime topology, grants
itself a new tool, or marks work complete by assertion alone.

```text
START
  -> INTAKE -> INVESTIGATE -> DESIGN -> DESIGN_CRITIQUE
  -> PLAN -> VALIDATE_PLAN -> DISPATCH -> EXECUTE -> LOCAL_VERIFY
  -> INTEGRATE -> OUTCOME_VERIFY -> COMPLETE

LOCAL_VERIFY / INTEGRATE / OUTCOME_VERIFY
  -> TRIAGE -> LOCAL_REPAIR | DESIGN_REVISION | USER_APPROVAL | BLOCKED

DESIGN_REVISION -> DESIGN
USER_APPROVAL  -> permitted prior gate
LOCAL_REPAIR   -> DISPATCH
```

The arrows above are application code. Conditional edges are selected from
validated status values, not from free-form model prose. The graph can pause at
an approval or environment blocker and resume durably from its checkpoint.

The work graph is dynamic **data**, not a dynamic executable graph. A planning
agent may propose child nodes and dependencies, but it returns a typed
`ProposedWorkPlan`. A deterministic validator must prove that the proposal has
unique IDs, valid references, no cycles, valid owners, leaf-ready tasks, and
complete acceptance coverage before the coordinator writes it to the approved
work queue. The dispatcher draws only approved, dependency-ready nodes from
that queue.

This distinction is the primary production rail:

```text
LLM proposal -> schema validation -> policy/graph validation -> approved state
     |                  |                    |
     +---- rejected ----+--------------------+-> triage or redesign
```

Agents may make bounded domain judgments inside their node. Only deterministic
application services may change run state, approve a work node, issue an
execution packet, transition a node to verified, invalidate work after a design
revision, or finish a run.

#### Authoritative state and write boundaries

The runtime stores these typed records in durable storage:

| Record | Written by | Purpose |
| --- | --- | --- |
| `RunRecord` | coordinator service | run identity, current gate, tenant, risk class, terminal state |
| `CharterRecord` | intake acceptance service | authoritative outcome and user-owned decisions |
| `DesignRevision` | design acceptance service | immutable versioned design and decisions |
| `WorkNodeRecord` | plan validator/coordinator | approved node, edges, owner, state, dependencies |
| `PacketRecord` | packet service | immutable, version-pinned worker input |
| `ArtifactRecord` | artifact service | content location, hash, producer, version, access policy |
| `EvidenceRecord` | verifier/integration acceptance service | check result and supporting artifact references |
| `IssueRecord` | triage service | observed problem, classification, affected-node set |
| `ApprovalRecord` | authenticated human approval service | approver, authority, decision, timestamp, revision |
| `WorkspaceRecord` | workspace service | selected source, baseline fingerprint, guest identity, lifecycle state |
| `CheckpointRecord` | checkpoint service | guest commit/tree hash, accepted node, evidence, rollback lineage |
| `PromotionRecord` | promotion service | reviewed export, confirmed version, target branch/commit/tag, result |

Agent output is an append-only proposal or report associated with a run and
node. It does not write an authoritative record directly. This makes the audit
trail reconstructable even when an agent response is malformed or rejected.

#### Required technical controls

- Use typed, versioned schemas (for example, Pydantic models) at every agent and
  external-system boundary; reject unknown or invalid fields.
- Treat model text as untrusted input. Parse it into an allowed result type;
  never execute a tool call, path, URL, command, or transition supplied in free
  text without separate policy validation.
- Give each runtime node a narrow tool and credential set. The planner cannot
  write product artifacts; the verifier cannot repair them; only an explicitly
  authorized worker can request a scoped artifact mutation.
- Make state transitions idempotent with stable run/node/attempt IDs and
  optimistic concurrency checks, so resume/retry cannot duplicate an approval,
  artifact, or external action.
- Persist checkpoints and approved state in durable storage. LangGraph
  checkpoints support execution recovery; the domain records remain the source
  of truth for audit and reporting.
- Enforce limits in code: allowed retry count, wall-clock deadline, token/cost
  budget, artifact-size budget, and maximum active work items.
- Require an authenticated approval record for user-owned, regulated, or
  high-risk decisions. An LLM's `approved` field is never such a record.
- Run untrusted tools or artifact generation in isolated execution environments;
  LangGraph routing is not a filesystem, network, or credential sandbox.

### 3.5 Execution boundary and workspace lifecycle

The Docker Compose application is the trusted control plane. Model-directed
filesystem, shell, build, test, browser, and artifact-generation tools execute
inside a disposable QEMU/KVM guest, not in the API, runner, VM-manager
container, or host working tree.

At run creation, the user selects a folder from administrator-configured host
project roots. The workspace service resolves the selection against that
allowlist, records the source Git commit and working-tree fingerprint when
available, and copies a sanitized snapshot into:

```text
/home/piagent/workspaces/<run-id>/<project-name>
```

The source is mounted read-only only into the trusted VM-manager service for the
copy operation. It is never mounted into the guest. Secrets, environment files,
host SSH material, dependency caches, build output, and the source `.git`
directory are excluded by default.

After import, a workspace checkpoint service initializes a new Git repository
inside the guest and creates an immutable baseline commit. This is deliberately
separate from the host repository. Accepted work-node outputs may create
service-owned checkpoint commits. The user may also mark the current guest state
as a candidate, which pauses mutations, runs configured checks, and records a
`USER_ACCEPTED` checkpoint without pretending it has passed independent outcome
verification. The user may request rollback to any restorable checkpoint. Guest
Git is a rollback mechanism, not the authoritative audit store: checkpoint
metadata, tree hash, producer, evidence, and design version remain durable
domain records.

One disposable guest is used for a complete run in the first release. Roles
receive different tool sets: implementation roles may mutate the guest copy;
verification roles are read-only; no role receives a host shell. The guest
provides a Chromium desktop and browser-automation tools through an authenticated
per-run display channel. Web access goes through a policy-controlled egress
proxy that denies host, private, link-local, metadata, and management endpoints.

The host source changes only through an explicit promotion workflow:

```text
review result and evidence -> choose version -> confirm promotion
  -> recheck source baseline -> export to isolated host worktree
  -> validate -> create branch + commit + optional annotated tag
```

Promotion never writes into the user's current checkout. For a clean Git source
whose recorded baseline is still current, it creates an isolated worktree and a
branch such as `orchestrator/<version>-<run-id>`, then commits the exported tree.
The UI proposes the next minor semantic version when valid version tags exist,
but the user owns and confirms the exact label. A tag is created only when the
confirmed label is unique. If the source is dirty, its HEAD changed, validation
fails, or paths escape policy, direct promotion is refused and the result
remains in a separate versioned review repository. A non-Git source is likewise
exported to a newly initialized result repository unless the user separately
authorizes repository creation at the source.

Promotion applies the reviewed delta between the sanitized imported baseline
and the selected guest checkpoint. Paths excluded from import are protected and
cannot appear as deletions or modifications in that delta. This preserves host
`.env` files, credentials, caches, ignored artifacts, and other host-only state.

## 4. Canonical Run Artifacts

Each run has an artifact bundle. In production, the typed durable records in
Section 3.4 are authoritative; the bundle is a human-readable projection and
evidence package, not the state machine's source of truth. Markdown remains
useful for review, export, and local prototyping, while structured records and
object storage provide durable production state.

Proposed runtime layout:

```text
runs/<run-id>/
  RUN.md                 # current state, artifact versions, active gate
  CHARTER.md             # user outcome, scope, constraints, approvals
  DESIGN.md              # authoritative design baseline
  DECISIONS.md           # accepted/rejected decisions and reasons
  WORK_GRAPH.md          # nodes, edges, owners, state, traceability
  ISSUES.md              # findings and routing decisions
  EVIDENCE.md            # acceptance and review evidence index
  WORKSPACE.md           # source fingerprint, guest identity, import/export policy
  PROMOTIONS.md          # explicit publish decisions and resulting Git references
  packets/<task-id>.md   # immutable packet revision handed to a worker
  reports/<task-id>.md   # compact worker/verifier result
  checkpoints/<id>.md    # accepted guest checkpoint and tree hash
```

Runtime bundles must be stored outside product artifacts and governed by tenant,
retention, encryption, and access policy. They are not committed to the target
repository in production. A local development projection may be written under a
run directory for inspection.

### 4.1 `CHARTER.md`: outcome authority

The charter records:

- requested outcome and intended users;
- included and excluded scope;
- domain, jurisdiction, environment, and time assumptions;
- constraints and protected existing artifacts;
- outcome-level acceptance criteria;
- questions requiring user authority;
- required human or professional approvals;
- risk class and evidence expectations.

The charter is the authority for **what** is being delivered. Design may refine
how it is delivered but cannot broaden the charter silently.

### 4.2 `DESIGN.md`: the design anchor

The design baseline records:

- component/deliverable map and responsibilities;
- boundaries and dependency direction;
- shared vocabulary and canonical definitions;
- interfaces between components, including document cross-references;
- global invariants and consistency rules;
- assembly and integration strategy;
- verification strategy;
- known risks and unresolved design questions;
- a monotonically increasing `DESIGN_VERSION`.

Workers receive only the relevant sections plus the version identifier, not the
entire design by default. Every output records the design version it used.

Once execution starts, a design is baselined, not frozen forever. Revisions go
through change control and impact analysis. Completed work is never assumed to
remain valid after a relevant design change.

### 4.3 `WORK_GRAPH.md`: delivery authority

Each node contains at least:

```text
NODE_ID:
PARENT_ID:
TYPE:
GOAL:
OWNER_ROLE:
STATE:
DESIGN_REFS:
DEPENDS_ON:
INPUTS:
OUTPUTS:
INTERFACES:
ACCEPTANCE_REFS:
CHILDREN:
```

Allowed states:

```text
PROPOSED -> DESIGNED -> DECOMPOSED | READY -> IN_PROGRESS
         -> IMPLEMENTED -> LOCALLY_VERIFIED -> INTEGRATED -> VERIFIED

Any active state -> BLOCKED | CHANGE_REQUESTED | INVALIDATED
```

State changes require evidence or a routing reason. `IMPLEMENTED` is a worker
claim; only an assurance role can move the node to `LOCALLY_VERIFIED` or
`VERIFIED`.

### 4.4 `DECISIONS.md`: design history

Each decision records its identifier, question, decision owner, chosen option,
rejected material alternative, rationale, affected design sections, and affected
work nodes. This prevents later workers from reopening settled choices without
new evidence.

### 4.5 `ISSUES.md`: feedback channel

An issue is a compact observation, not a transcript. It records:

- issue ID and reporter role;
- affected artifact/node and observed evidence;
- expected versus actual result;
- proposed classification;
- severity and blocking status;
- design version;
- routing outcome and impacted nodes.

Issue classifications are:

- `LOCAL_DEFECT`: packet implementation is wrong but its contract is sound;
- `INTERFACE_MISMATCH`: individually plausible outputs do not compose;
- `DESIGN_GAP`: boundary, invariant, or architecture is incomplete or wrong;
- `REQUIREMENT_GAP`: charter or user intent is incomplete;
- `EVIDENCE_GAP`: result may be correct but required proof is absent;
- `ENVIRONMENT_BLOCKER`: required tool, source, permission, or authority is unavailable.

## 5. Narrow Agent Roles

The coordinator invokes every role in a fresh, foreground context. Roles return
structured claims; the coordinator records accepted claims in the run artifacts.

| Role | Receives | Produces | Explicit boundary |
| --- | --- | --- | --- |
| Run coordinator | Run index and next eligible states | Routing decision and checkpoint | Does not investigate, design, decompose, implement, or approve |
| Intake analyst | User request and known environment | Draft charter and exact authority questions | Does not design the solution |
| Current-state investigator | Approved charter and protected artifacts | Existing ownership, constraints, evidence, gaps | Does not choose new architecture |
| Design authority | Charter plus investigation capsule | Versioned design proposal and decisions | Does not create leaf implementation packets |
| Design critic | Proposed design, charter, risk rules | Contradictions, uncovered criteria, or acceptance recommendation | Does not rewrite or implement the design |
| Work planner | One approved design node | Child graph with ownership, dependencies, and gates | Does not implement or silently alter design |
| Handoff engineer | One ready leaf, relevant artifact references | Minimal canonical task packet | Does not add requirements or solve the task |
| Domain worker | One packet | One bounded artifact outcome and work report | Does not broaden scope or change design |
| Local verifier | Packet, output, evidence rules | Pass/fail/evidence-gap result | Does not repair the output |
| Integrator | Verified sibling outputs and interface contracts | Assembled output or interface issue | Does not redesign incompatible contracts |
| Outcome verifier | Charter, integrated result, evidence index | Criterion-by-criterion final verdict | Does not infer missing acceptance criteria |
| Issue triager | One issue and referenced baselines | Classification, impact set, next loop | Does not perform the repair |
| Specialist reviewer | Risk-selected artifacts and evidence | Independent findings or approval record | Does not replace legally required human approval |

Some roles can share an agent definition later if their contracts remain
separate, but their invocations and outputs should not be combined merely to
save calls. Conversely, roles should not be created for mechanical operations
that the coordinator can perform deterministically.

### 5.1 Domain profiles

The core roles stay generic. A selected domain profile supplies terminology,
worker types, evidence rules, risk gates, and templates.

Examples:

- software: source implementer, test implementer, security reviewer, build and
  test commands;
- legal documents: jurisdiction researcher, document drafter, consistency
  reviewer, citation/authority checker, licensed-professional approval gate;
- research: source collector, analyst, methodology reviewer, citation verifier;
- operations: procedure writer, control designer, audit-evidence reviewer.

A domain profile cannot weaken the core traceability, independence, or change
control rules.

## 6. Handoff Contract

The existing lean task packet is retained conceptually but generalized. A leaf
packet should use this shape:

```text
RUN_ID:
TASK_ID:
NODE_ID:
TASK_TYPE:

GOAL:
<one observable outcome>

DESIGN_BASELINE:
- version and exact relevant sections/decision IDs

ACCEPTANCE_CRITERIA:
- criterion ID and observable result

INPUT_ARTIFACTS:
- artifact ID/path, version, and purpose

OUTPUT_ARTIFACTS:
- artifact ID/path and required form

INTERFACES:
- interface/definition/cross-reference that must be preserved

STARTING_POINTS:
- relevant path, symbol, source, template, or authority

DEPENDS_ON:
- verified node ID or none

EXPECTED_TOUCH_POINTS:
- informed starting paths/artifacts

PROTECTED_TOUCH_POINTS:
- paths/artifacts that must not change

ACCEPTANCE_CHECKS:
- method: command | inspection | cross-check | human approval
  procedure: exact bounded procedure
  evidence: required record

AUTHORITY_LIMITS:
- decisions the worker must not make

KNOWN_FACTS:
- compact verified fact

KNOWN_FAILED_APPROACHES:
- failure fingerprint or none

ISSUE_CONTRACT:
- report evidence and proposed classification; do not redesign in place

OUTPUT_CONTRACT:
- status, outputs, checks, risks, issues, and design version used
```

`STARTING_POINTS` replaces the software-specific `ENTRY_SYMBOLS`.
`ACCEPTANCE_CHECKS` includes commands where appropriate but also supports
document inspection, cross-document consistency, cited-authority checks, and
explicit human approval. The packet embeds no large source or design blobs; it
references canonical artifacts and carries only the context necessary for the
leaf outcome.

## 7. Orchestration Loops

### 7.1 Loop A: charter and authority

```text
user request -> intake -> charter check
                         | complete -> investigation
                         | authority gap -> user decision
```

The harness must pause for the user when jurisdiction, product behavior, risk
acceptance, budget, or another authority-owned choice would materially change
the outcome. It should not ask the user to resolve facts that investigation can
discover.

### 7.2 Loop B: design

```text
charter + current-state evidence
          -> design authority -> design critic
                 ^                  |
                 | material issue   | accepted
                 +------------------+-> baseline DESIGN_VERSION
```

The design critic checks coverage, internal consistency, boundary ownership,
integration feasibility, verification feasibility, and domain risk gates. It
does not create a competing design. After a bounded number of materially
different revisions, unresolved authority questions go to the user and
unresolved evidence gaps go back to investigation.

### 7.3 Loop C: recursive planning

```text
approved design node -> work planner -> graph validator
                              | leaf-ready -> handoff queue
                              | too broad  -> expand child node
                              | design gap -> issue triage
```

Graph validation checks:

- every charter criterion maps to one or more nodes and final evidence;
- every output has an owner and consumer;
- dependencies are acyclic;
- interface-producing nodes precede interface-consuming nodes;
- integration and outcome-verification nodes exist;
- each executable leaf meets all leaf criteria;
- no node requires authority its owner does not have.

Planning continues until the ready frontier contains only leaf tasks. The
entire graph need not be decomposed to maximum depth up front: distant branches
may remain coarse until their dependencies or design stabilize.

### 7.4 Loop D: leaf delivery

```text
ready leaf -> handoff engineer -> domain worker -> local verifier
                 ^                    |              |
                 | malformed packet   | issue        | pass
                 +--------------------+--------------+-> node verified locally
```

The worker may make local implementation choices inside the approved design and
authority limits. It must report `BLOCKED_SCOPE` or an issue when success needs
a changed outcome, interface, protected artifact, or design baseline.

The current worker/debugger safeguards remain:

- claims are independently verified;
- unchanged failed experiments are not repeated;
- a debugger supplies new evidence, not another speculative implementation;
- repair attempts per packet remain bounded;
- pre-existing changes remain protected.

### 7.5 Loop E: integration and outcome assurance

```text
verified component set -> integrator -> interface verification
                              | issue          | pass
                              v                v
                         issue triage -> aggregate node verified
                                               |
                                               v
                                   outcome verifier + required approvals
```

Local success is not completion. The integrator checks the contracts between
components and assembles the higher-level artifact. Examples include API
compatibility, shared terminology, consistent parties and dates across legal
documents, complete references, or coherent operating procedures.

The outcome verifier evaluates every charter criterion against the integrated
artifact and evidence index. Required human, licensed, regulatory, or business
approvals remain explicit gates and are never simulated by an agent verdict.

### 7.6 Loop F: controlled redesign

Issue triage determines the smallest correct loop:

| Classification | Route |
| --- | --- |
| Local defect | Existing leaf repair/debug loop |
| Interface mismatch with sound design | Integration repair or affected leaf packets |
| Design gap | Design authority revision |
| Requirement gap | Intake and user authority |
| Evidence gap | Verifier or evidence-producing task |
| Environment blocker | Coordinator reports or waits for the missing capability |

For a design revision:

1. Record the issue and affected decision/design sections.
2. Produce and critique a new `DESIGN_VERSION`.
3. Compute the impacted work-node set from design and interface references.
4. Mark impacted ready/in-progress nodes `CHANGE_REQUESTED`.
5. Mark impacted completed nodes `INVALIDATED` until reverified or replaced.
6. Replan only the affected subgraph.
7. Preserve unaffected verified nodes and their evidence.

This is the main outer iteration loop. Workers and testers report evidence; they
do not edit the design informally.

### 7.7 Loop G: workspace review, rollback, and promotion

```text
selected host folder -> copied guest baseline -> accepted checkpoints
                                  |                    |
                                  | rollback           | user marks good version
                                  v                    v
                         prior checkpoint       promotion preview
                                                       |
                                  baseline conflict <--+--> confirmed publish
                                                               |
                                                               v
                                                    host branch + commit/tag
```

Rollback changes only the disposable guest workspace and records a new lineage
event; it never rewrites authoritative evidence or the host repository.
Promotion is a user-owned external action. Before enabling the confirmation
button, the application presents the exported diff, changed-file manifest,
checks, unresolved issues, destination repository, proposed branch, commit
message, and version label. A successful promotion records the exact source and
result commits and is idempotent for the same run, checkpoint, target, and
version.

## 8. Context Engineering Rules

Each handoff is assembled from references, not accumulated conversation history.
The handoff engineer includes only:

- the leaf goal and authority limits;
- relevant charter criteria;
- exact design sections, decision IDs, and interface contracts;
- verified dependencies and input artifact versions;
- known facts needed to begin;
- acceptance procedures and required evidence;
- protected artifacts and failure fingerprints.

It excludes:

- full prior-agent transcripts;
- unrelated branches of the work graph;
- rejected alternatives unless they prevent a known repeated failure;
- complete source trees or full design documents when exact sections suffice;
- speculative next work.

Every packet and report carries stable IDs and versions. A consumer must be able
to determine whether its context is stale without reading narrative history.

### 8.1 Production observability and audit trail

OpenTelemetry is the vendor-neutral telemetry boundary; LangGraph is the
workflow runtime. The runtime emits correlated traces, logs, and metrics to an
OTLP collector, which may feed a self-hosted or managed observability backend.
The audit records above remain durable even if telemetry sampling drops a trace.

Required trace hierarchy:

```text
run
  -> control-gate
    -> node-attempt
      -> agent invocation
        -> model call / tool call / artifact operation
      -> validation or approval decision
```

Every event includes at least `run_id`, `node_id`, `attempt_id`,
`design_version`, `packet_version`, `actor_role`, `outcome`, and a correlation
ID. Track latency, model/token/cost use, retries, validation failures,
approval-wait time, queue age, and terminal outcomes as metrics.

Logs and traces must be safe by default: record hashes, artifact references,
redacted summaries, and policy decisions rather than indiscriminately exporting
full prompts, legal documents, credentials, or personally identifying data.
Full content capture, where justified, requires tenant policy, encryption,
access controls, retention rules, and a clear viewer authorization model.

The human-facing run view should show the approved work graph, current node,
state transitions, produced artifacts, evidence, approvals, and blockers. Raw
agent transcripts are supporting evidence available only to authorized users;
they are not the primary source of truth for the run.

## 9. Examples

### 9.1 Mario-like game

The outcome node might decompose into game rules, engine/runtime, level model,
player movement, collision, enemies, rendering, audio, content, persistence,
and acceptance scenarios. The design baseline owns coordinate systems, update
order, state transitions, asset contracts, and module boundaries.

The planner could decompose `player movement` into input mapping, movement state,
physics interaction, animation contract, and focused tests. These are not all
independent: the movement state/interface must be verified before animation and
some collision work consumes it. An integration node then verifies input,
movement, collisions, camera, and animation together. A gameplay outcome gate
checks user-visible scenarios rather than merely aggregating unit-test results.

### 9.2 Company operating-document package

The charter must first establish authority-owned facts such as jurisdiction,
entity type, owners, governance model, employees/contractors, regulated
activities, and intended operating scope. Missing high-impact facts block design
rather than being guessed.

The design baseline is an operating and document architecture: canonical party
names and definitions, governance powers, approval thresholds, record hierarchy,
document dependencies, required filings, policy ownership, and professional
review gates.

Possible work packages include formation records, ownership/governance records,
commercial templates, employment documents, privacy/data policies, financial
controls, and operating procedures. Each can be decomposed further, but shared
definitions and authority rules are designed once. Integration checks that the
documents agree about parties, powers, dates, thresholds, defined terms, and
cross-references. Final completion requires the human/legal/regulatory approvals
named in the charter; agent review is supporting evidence, not legal sign-off.

The same orchestration model therefore applies even though the workers,
artifacts, evidence methods, and risk gates differ from software.

## 10. Failure and Stop Rules

- No worker receives a node that fails the leaf criteria.
- No agent silently changes charter, design, dependency, or acceptance scope.
- No completed node is trusted after an impacting baseline revision until its
  evidence is revalidated.
- No integration failure is automatically blamed on the last worker; triage
  identifies whether the packet, output, interface, or design owns the defect.
- No loop repeats without a changed artifact, new evidence, or revised
  hypothesis.
- No model-directed tool executes against a writable host project path.
- No rollback operation changes the host repository or erases audit records.
- No result promotion occurs without an authenticated user confirmation,
  baseline recheck, successful export validation, and unique idempotency key.
- No promotion overwrites a dirty or advanced host checkout; conflicts remain
  staged for review.
- Bounded loop budgets are configured per role and node; exhaustion produces a
  concrete blocker, not an invented completion.
- High-risk domains require explicit sources, effective dates, jurisdiction,
  and human/professional approval where applicable.
- The run completes only when all outcome criteria have evidence, all required
  integration nodes pass, no blocking issues remain, and all mandatory approvals
  are recorded.

## 11. Incremental Rollout

The harness should be changed in small stages so each stage is usable and
testable.

### Phase 0: agree on the model

Deliverables:

- review and revise this plan;
- decide runtime artifact location and retention policy;
- decide whether `WORK_GRAPH` begins as Markdown only or Markdown plus a
  machine-readable index;
- approve the disposable-VM workspace, guest checkpoint, and host promotion
  contract;
- select the first end-to-end pilot task and its risk limits.

Exit gate: the user approves the role boundaries, artifact ownership, and
change-control model.

### Phase 1: deterministic runtime foundation

Changes:

- create the Python production package and its deterministic runtime boundaries;
- define versioned schemas for every authoritative record and proposal;
- implement the fixed LangGraph control graph, durable checkpointer, state
  repository, artifact repository, and authenticated approval boundary;
- implement transition guards, idempotency keys, retry/budget policies, and a
  contract-fixture test suite before connecting the LM Studio model runtime.

Exit gate: automated tests prove that invalid transitions, malformed plans,
unapproved graph changes, duplicated external actions, and agent-declared
completion are rejected without invoking a real agent.

### Phase 2: planning, packets, and bounded execution

Changes:

- implement charter, design-revision, proposed-work-plan, and packet schemas;
- add deterministic graph validation, leaf-readiness checks, ready-queue
  selection, and immutable packet issuance;
- integrate allowlisted folder selection, copy-in to a fresh disposable guest,
  guest-only Git checkpoints, rollback, interactive desktop access, and
  policy-controlled web egress;
- connect the first software-domain planner, worker, and verifier adapters;
- record all agent output as proposals/reports, then accept only validated data.

Exit gate: a medium software task is represented as approved work-node data;
only dependency-ready leaves are dispatched, and every verified leaf traces to a
charter criterion and one design revision.

### Phase 3: integration, triage, and controlled redesign

Changes:

- add integration and outcome-verification gates;
- implement issue classification, impact analysis, revision pinning, node
  invalidation, and affected-subgraph replanning;
- add bounded local repair and design-revision loops;
- add human approval pause/resume flows.

Exit gate: an injected interface/design defect produces an auditable issue,
revises the design only when required, invalidates only the impacted nodes, and
cannot bypass re-verification.

### Phase 4: observability, security, and operations

Changes:

- instrument the runtime with OpenTelemetry traces, metrics, and safe logs;
- build an authorized run view from authoritative records and correlated trace
  links;
- add tenant isolation, secrets/tool policy, redaction, retention, encryption,
  and execution-isolation controls;
- exercise crash recovery, concurrency conflicts, approval timeouts, and
  artifact-store failures.

Exit gate: an authorized operator can reconstruct a run from records and trace
links, while unauthorized users cannot retrieve protected content or approve a
restricted transition.

### Phase 5: second domain profile and pilot

Changes:

- add a document-centric profile with non-command acceptance checks,
  cross-document consistency rules, and professional approval gates;
- pilot a low-risk fictional company-document package only after the software
  pilot passes;
- compare quality, cost, recovery, and operator burden across both profiles.

Exit gate: the same fixed runtime executes software and document pilots without
embedding either domain's concepts in the core state machine.

## 12. Proposed Production Package After Plan Approval

This is a likely change map, not an instruction to implement everything at once:

```text
production/
  pyproject.toml
  src/orchestration/
    graph.py                 # fixed LangGraph control graph
    schemas/                 # Pydantic records and proposal/result types
    services/                # validators, packet, transition, approval services
    repositories/            # durable state and artifact boundaries
    adapters/                # model, tool, domain-worker integrations
    telemetry/               # OpenTelemetry instrumentation and redaction
    policies/                # role capability, retry, budget, retention rules
  tests/
    unit/                    # schemas, guards, graph validation
    integration/             # checkpoint/recovery and state transitions
    adversarial/             # scope escape, stale state, unsafe proposal tests
```

Production behavior is defined and tested entirely in this package. External
prompt experiments or development harnesses are not runtime dependencies.

## 13. Design Questions for the Next Iteration

The first review should settle these questions before Phase 1:

1. Which durable store and checkpointer implementation meet the required
   availability, data residency, retention, and recovery requirements?
2. Which exact status enums and transition table define the first fixed control
   graph, including all terminal and approval states?
3. Which proposal/result schemas must be accepted in Phase 1 before a real LLM
   adapter is enabled?
4. Which artifact operations are allowed to an executor, and must every product
   mutation be mediated by a sandboxed artifact service?
5. What content may be captured in traces, and what must be hashed, redacted,
   encrypted, or excluded for each tenant/risk class?
6. What is the first representative pilot: a medium software system, a
   fictional company package, or both in sequence?
7. Which decisions always require authenticated human confirmation regardless of
   domain risk?
8. Which repositories use semantic version tags, and what fallback label policy
   applies when a selected source has no valid version baseline?

The recommended starting choices are: Postgres-backed authoritative state and
checkpointing with object storage for artifacts; a static control graph with
typed status enums; agents returning proposals only; an artifact service as the
sole product-mutation boundary; OTel traces that default to references and
redacted summaries; and a software pilot before a fictional legal-document
case.

## 14. Success Criteria for the New Harness

The architecture is successful when:

- a broad request can be represented without sending broad scope to a worker;
- every leaf output traces to charter criteria, design version, dependencies,
  and acceptance evidence;
- fresh agents can resume from canonical artifacts without prior transcripts;
- a selected host folder is copied into a disposable guest where all
  model-directed tools and mutable work remain isolated;
- the user can inspect the guest desktop, roll back to an accepted guest Git
  checkpoint, and continue the same run;
- a reviewed result can be promoted idempotently to a new host Git branch and
  user-confirmed version commit without changing the current checkout;
- a component defect, integration mismatch, design flaw, and requirement gap are
  routed to different explicit loops;
- design changes invalidate exactly the work whose assumptions changed;
- local verification, integration verification, and final outcome verification
  are visibly distinct;
- adding a new domain changes profiles and specialist roles, not the core
  orchestration state machine;
- the coordinator remains a narrow state and routing authority rather than a
  second designer or implementer.
