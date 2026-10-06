from __future__ import annotations

from typing import Any
import uuid

from .resource_status import merge_resource_status

PROTOCOL_VERSION = 2
USER_COMMAND_CATEGORIES = (
    "straightforward_direction",
    "simple_task",
    "maintenance",
    "information_retrieval",
    "task",
    "task_step",
    "append",
)


def command_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [PROTOCOL_VERSION]},
            "kind": {"type": "string", "enum": ["command"]},
            "category": {"type": "string", "enum": list(USER_COMMAND_CATEGORIES)},
            "intent": {"type": "string", "maxLength": 500},
            "requires_file_mutation": {"type": "boolean"},
            "requires_external_research": {"type": "boolean"},
            "input_has_media": {"type": "boolean"},
            "output_requires_media": {"type": "boolean"},
            "previous_task_id": {"type": "string", "maxLength": 120},
            "previous_step_id": {"type": "string", "maxLength": 120},
            "dependency_reason": {"type": "string", "maxLength": 500},
            "expected_output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["answer", "artifact", "artifact_and_response", "action_result"]},
                    "format": {"type": "string", "maxLength": 80},
                },
                "required": ["type", "format"],
                "additionalProperties": False,
            },
        },
        "required": ["schema_version", "kind", "category", "intent", "requires_file_mutation", "requires_external_research", "input_has_media", "output_requires_media", "expected_output"],
        "additionalProperties": False,
    }


def final_verification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [PROTOCOL_VERSION]},
            "kind": {"type": "string", "enum": ["final_verification"]},
            "verdict": {"type": "string", "enum": ["accept", "revise"]},
            "schema_complete": {"type": "boolean"},
            "requirements_complete": {"type": "boolean"},
            "artifact_verified": {"type": "boolean"},
            "issues": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 600}},
            "repair_scope": {"type": "string", "enum": ["none", "response", "requirements", "artifact", "schema"]},
        },
        "required": ["schema_version", "kind", "verdict", "schema_complete", "requirements_complete", "artifact_verified", "issues", "repair_scope"],
        "additionalProperties": False,
    }


def normalize_command(value: dict[str, Any], fallback_text: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("command envelope must be an object")
    category = str(value.get("category") or "").strip()
    if category not in USER_COMMAND_CATEGORIES:
        raise ValueError(f"unsupported command category: {category!r}")
    expected = value.get("expected_output")
    if not isinstance(expected, dict):
        raise ValueError("expected_output must be an object")
    output_type = str(expected.get("type") or "").strip()
    if output_type not in {"answer", "artifact", "artifact_and_response", "action_result"}:
        raise ValueError("invalid expected_output.type")
    output_format = str(expected.get("format") or "text").strip()[:80] or "text"
    requires_file_mutation = bool(value.get("requires_file_mutation"))
    if output_type in {"artifact", "artifact_and_response"} and not requires_file_mutation:
        raise ValueError("artifact output requires requires_file_mutation=true")
    intent = str(value.get("intent") or fallback_text or "").strip()[:500]
    if not intent:
        raise ValueError("command intent must not be blank")
    result = {
        "schema_version": PROTOCOL_VERSION,
        "kind": "command",
        "category": category,
        "intent": intent,
        "requires_file_mutation": requires_file_mutation,
        "requires_external_research": bool(value.get("requires_external_research")),
        "input_has_media": bool(value.get("input_has_media")),
        "output_requires_media": bool(value.get("output_requires_media")),
        "expected_output": {"type": output_type, "format": output_format},
    }
    if category == "append":
        previous_task_id = str(value.get("previous_task_id") or "").strip()[:120]
        previous_step_id = str(value.get("previous_step_id") or "").strip()[:120]
        dependency_reason = str(value.get("dependency_reason") or "").strip()[:500]
        if not previous_task_id:
            raise ValueError("append command requires previous_task_id")
        if not dependency_reason:
            raise ValueError("append command requires dependency_reason")
        result.update({
            "previous_task_id": previous_task_id,
            "previous_step_id": previous_step_id,
            "dependency_reason": dependency_reason,
        })
        previous_task_uuid = str(value.get("previous_task_uuid") or "").strip()
        previous_node_id = str(value.get("previous_node_id") or "").strip()
        if previous_task_uuid:
            uuid.UUID(previous_task_uuid)
            result["previous_task_uuid"] = previous_task_uuid
        if previous_node_id:
            uuid.UUID(previous_node_id)
            result["previous_node_id"] = previous_node_id
    return result


def validate_final_candidate(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("final candidate must be an object")
    required = {"schema_version", "kind", "task_id", "step_id", "status", "command", "user_reply", "artifacts", "requirements", "evidence_count"}
    missing = sorted(required.difference(value))
    if missing:
        raise ValueError("final candidate missing fields: " + ", ".join(missing))
    if int(value.get("schema_version")) != PROTOCOL_VERSION or value.get("kind") != "final_candidate":
        raise ValueError("invalid final candidate protocol identity")
    if value.get("status") != "completed":
        raise ValueError("final candidate status must be completed")
    if not isinstance(value.get("task_id"), str) or not value["task_id"]:
        raise ValueError("final candidate task_id is required")
    if not isinstance(value.get("step_id"), str) or not value["step_id"]:
        raise ValueError("final candidate step_id is required")
    command = normalize_command(value.get("command") or {}, "final response")
    reply = value.get("user_reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("final candidate user_reply must not be blank")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("final candidate artifacts must be a list")
    clean_artifacts = []
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item.get("path"):
            raise ValueError("each artifact requires a path")
        clean_artifacts.append({
            "path": item["path"],
            "sha256": str(item.get("sha256") or ""),
            "verified": bool(item.get("verified")),
        })
    requirements = value.get("requirements")
    if not isinstance(requirements, dict):
        raise ValueError("final candidate requirements must be an object")
    try:
        evidence_count = max(0, int(value.get("evidence_count", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence_count must be an integer") from exc
    return {
        "schema_version": PROTOCOL_VERSION,
        "kind": "final_candidate",
        "task_id": value["task_id"],
        "step_id": value["step_id"],
        "status": "completed",
        "command": command,
        "user_reply": reply.strip(),
        "artifacts": clean_artifacts,
        "requirements": requirements,
        "evidence_count": evidence_count,
        "resource_status": merge_resource_status(value.get("resource_status")),
    }


def validate_final_verification(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("verification result must be an object")
    required = {"schema_version", "kind", "verdict", "schema_complete", "requirements_complete", "artifact_verified", "issues", "repair_scope"}
    missing = sorted(required.difference(value))
    if missing:
        raise ValueError("verification result missing fields: " + ", ".join(missing))
    if int(value.get("schema_version")) != PROTOCOL_VERSION or value.get("kind") != "final_verification":
        raise ValueError("invalid verification protocol identity")
    verdict = str(value.get("verdict") or "").strip().lower()
    if verdict not in {"accept", "revise"}:
        raise ValueError("verdict must be accept or revise")
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    issues = [str(issue).strip()[:600] for issue in issues if str(issue).strip()][:8]
    repair_scope = str(value.get("repair_scope") or "").strip().lower()
    if repair_scope not in {"none", "response", "requirements", "artifact", "schema"}:
        raise ValueError("invalid repair_scope")
    result = {
        "schema_version": PROTOCOL_VERSION,
        "kind": "final_verification",
        "verdict": verdict,
        "schema_complete": bool(value.get("schema_complete")),
        "requirements_complete": bool(value.get("requirements_complete")),
        "artifact_verified": bool(value.get("artifact_verified")),
        "issues": issues,
        "repair_scope": repair_scope,
    }
    if verdict == "accept":
        if issues or repair_scope != "none" or not all((result["schema_complete"], result["requirements_complete"], result["artifact_verified"])):
            raise ValueError("accept verdict must be complete, verified, issue-free, and repair_scope=none")
    elif not issues:
        raise ValueError("revise verdict requires at least one issue")
    return result


def step_verification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [PROTOCOL_VERSION]},
            "kind": {"type": "string", "enum": ["step_verification"]},
            "verdict": {"type": "string", "enum": ["accept", "revise"]},
            "requirements_complete": {"type": "boolean"},
            "evidence_supported": {"type": "boolean"},
            "issues": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 600}},
            "repair_scope": {"type": "string", "enum": ["none", "response", "requirements", "evidence"]},
        },
        "required": ["schema_version", "kind", "verdict", "requirements_complete", "evidence_supported", "issues", "repair_scope"],
        "additionalProperties": False,
    }


def validate_step_verification(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("step verification must be an object")
    required = {"schema_version", "kind", "verdict", "requirements_complete", "evidence_supported", "issues", "repair_scope"}
    missing = sorted(required.difference(value))
    if missing:
        raise ValueError("step verification missing fields: " + ", ".join(missing))
    if int(value.get("schema_version")) != PROTOCOL_VERSION or value.get("kind") != "step_verification":
        raise ValueError("invalid step verification protocol identity")
    verdict = str(value.get("verdict") or "").strip().lower()
    if verdict not in {"accept", "revise"}:
        raise ValueError("invalid step verification verdict")
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise ValueError("step verification issues must be a list")
    issues = [str(issue).strip()[:600] for issue in issues if str(issue).strip()][:8]
    repair_scope = str(value.get("repair_scope") or "").strip().lower()
    if repair_scope not in {"none", "response", "requirements", "evidence"}:
        raise ValueError("invalid step repair_scope")
    result = {
        "schema_version": PROTOCOL_VERSION,
        "kind": "step_verification",
        "verdict": verdict,
        "requirements_complete": bool(value.get("requirements_complete")),
        "evidence_supported": bool(value.get("evidence_supported")),
        "issues": issues,
        "repair_scope": repair_scope,
    }
    if verdict == "accept":
        if issues or repair_scope != "none" or not result["requirements_complete"] or not result["evidence_supported"]:
            raise ValueError("accept step verdict must be complete, supported, issue-free, and repair_scope=none")
    elif not issues:
        raise ValueError("revise step verdict requires at least one issue")
    return result
