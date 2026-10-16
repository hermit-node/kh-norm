# Norm shared objects — draft contract library

**Location:** `C:\Norm\objects` (portable source package: `objects/`).

**Purpose:** Stable, annotated data classes for boundaries between the *existing* N1 supervisor, N2 worker, Redis state, PostgreSQL history and recovery children. N1 decides and authorizes; N2 executes, uses tools, composes and self-verifies. These classes provide data contracts, **not another controller/agent**.

**Status:** Proposed contracts only. This package is intentionally **not imported into the running orchestration** until adapters and tests are approved. The established `core/norm_runtime/models.py` (`TaskPlan`, `TaskStep`, `StepResult`, `StepStatus`), `prompt_queue.py` (`PromptJob`) and `protocol.py` (schema version **2**, final candidate/verification schemas) continue to be authoritative for live r9 behavior. Do not invent parallel step-status or queue types. `CONTRACT_DRAFT_VERSION=1` is a version of this documentation/contract proposal, not a replacement runtime protocol version.

## Contracts: owner, when and why

| Class | Created/owned by | When to use | Why |
|---|---|---|---|
| `UserPromptReference` | task ingress / planning | Attached to approved work and recovery contexts | Preserve original words and verbatim constraints as *reference*, not a new execution request |
| `StepAssignment` | N1/coordinator | Specify the current assignment and expected return type | Makes completion criteria explicit without restricting N2's exploratory work |
| `EvidenceReference` | N2 after a tool observation; independently checked by N1 | Cite an existing Redis/PostgreSQL record | Fetch proof selectively without repeating work or hauling the whole context into each step |
| `ArtifactReference` | tool/file evidence path | Identify a produced or inspected artifact | Ground path/hash/verification separately from an N2 claim |
| `StepSubmission` | N2 after self-verification | Submit a step or structured analysis for review | Match the assignment's expected result *before* N1 judges semantics |
| `RecoveryAnalysis` | N2 analysis child | Assess surviving and missing work | N1 reviews **assessment accuracy**, not completion of original deliverable |
| `RecoveryUnit` | N2 recovery analysis, N1-reviewed | Propose bounded remaining work | Allows nonoverlapping scoped execution under existing child machinery |
| `ReviewDecision` | N1 | Accept or reject a specific submitted result kind | A retry identifies missing evidence or an exact actionable defect; don't ask for unrelated outputs |
| `RecoveryHandoff` | recovery coordinator after a real terminal failure | Close old task as `PASSED_TO:<new_task_id>` | The successor is explicitly `task_kind=recovery`, inherits a *distilled* handoff with evidence pointers and original intent |
| `FinalCandidate` | N2 after self-verification | Submit actual user reply and artifacts for final N1 review | Does not mark the task complete until N1 accepts |

Each class has a Python docstring headed `WHEN` and `WHY`, as well as immutable typed fields and basic invariants. Use `to_dict()` for JSON-compatible plain dictionaries; **do not pickle classes across Redis processes**. Existing runtime serializers must remain compatible when adapters are introduced.

## Exploration is deliberate, not an exception

An assignment's `expected_result` is the **return contract**, never a ceiling on N2's investigation. N2 may notice related defects, inspect unforeseen dependencies, develop and test a repair, or propose additional useful work through the existing N1/tool authorization process. None of these discoveries should be suppressed merely because the starting task was narrower. At review time N1 judges the assigned deliverable and evidence while distinguishing unrelated discoveries from unmet original requirements. A child producing recovery JSON is not automatically responsible for finishing the operator's PDF; likewise, discovering a real browser defect does not itself certify the PDF complete. This contract library changes no exploration, tool, or authorization policy.

## Required handoff rule

`validate_assignment_submission(assignment, submission)` checks task identity and `StepAssignment.expected_result` against `StepSubmission.result_kind` **before** semantic N1 review. If `expected_result=recovery_analysis`, the child must return structured recovery JSON; N1 cannot reject it because the original task's PDF or README is unfinished. The parent must parse/validate the exact structured payload before accepting that child as complete. Old/user prompt is *verbatim reference context* for scope alignment; only the approved assignment instructs execution.

`RecoveryAnalysis.to_parent_dict()` deliberately uses the **existing** `_parse_recovery_analysis()` field names: `useful_points`, `completed_work`, `remaining_units`, `merge_requirements`. `RecoveryAnalysis.from_parent_dict()` is a draft deterministic parser for that shape. This does not silently change the runtime parser or its scope checks.

## Migration order (requires explicit design approval)

1. Inventory existing payloads at **actual** call sites and write adapter tests. Preserve `TaskPlan`, `PromptJob` and schema-2 final protocol intact.
2. Validate recovery-analysis child results **before** N1 acceptance, and pass the child's expected result kind to N1. Avoid accepting a narrative that its parent expects to parse as JSON.
3. Introduce `EvidenceReference` indices to existing durable records so N1 reviews proof by ID, not a truncated dump. No new evidence database.
4. Define legacy serialization adapters and targeted tests for accepted step, rejected step, correct recovery JSON, wrong-result-kind rejection, and `PASSED_TO` lineage. Wire one boundary at a time.

**No current runtime call sites are migrated by merely adding these files.** Nothing in here invokes Ollama, directly or indirectly.

## Smoke test

From `C:\Norm`:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s objects -p 'test_*.py' -v
```

## r10 activation scope

The portable r10 package retains these typed contracts and adds
`core/norm_runtime/contract_boundary.py` as a narrow runtime adapter for
oversize-recovery analysis children and PASSED_TO recovery handoffs.
Analysis children are checked for typed assignment/result agreement before
N1 final acceptance. Other contract classes remain optional for future
adapter work: task planning, queue serialization, durable state, and N1/N2
process ownership remain unchanged.
