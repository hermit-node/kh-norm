from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "because", "been", "before", "being", "but", "can",
    "could", "does", "for", "from", "had", "has", "have", "into", "just", "like", "more", "not", "now",
    "only", "our", "out", "over", "should", "that", "the", "their", "then", "there", "these", "they",
    "this", "through", "too", "use", "using", "was", "were", "what", "when", "where", "which", "while",
    "will", "with", "would", "you", "your",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{2,}")


def _history_parent_map(cur, schema: str) -> dict[str, str | None]:
    s = sql.Identifier(schema)
    cur.execute(sql.SQL("SELECT primary_task_id,parent_task_id,source_task_ids FROM {}.task_history").format(s))
    out: dict[str, str | None] = {}
    for primary, parent, sources in cur.fetchall():
        parent_id = str(parent) if parent else None
        out[str(primary)] = parent_id
        if isinstance(sources, list):
            for source in sources:
                out.setdefault(str(source), parent_id)
    return out


def _nearest_surviving_history_ancestor(
    start_task_id: str | None,
    current_ids: set[str],
    history_parent: dict[str, str | None],
    *,
    excluded: set[str] | None = None,
) -> str | None:
    excluded = excluded or set()
    current = str(start_task_id or "").strip()
    seen: set[str] = set()
    for _ in range(128):
        if not current or current in seen:
            return None
        seen.add(current)
        if current in current_ids and current not in excluded:
            return current
        current = str(history_parent.get(current) or "").strip()
    return None


def _maintenance_note(cur, schema: str, phase: str, note: str, details: dict[str, Any]) -> None:
    s = sql.Identifier(schema)
    # Keep audit-note failures isolated from the surrounding migration/prune transaction.
    cur.execute("SAVEPOINT norm_integrity_audit")
    try:
        cur.execute("SELECT to_regclass(%s)", (f"{schema}.runtime_maintenance_notes",))
        row = cur.fetchone()
        if not row or row[0] is None:
            cur.execute("RELEASE SAVEPOINT norm_integrity_audit")
            return
        cur.execute(
            sql.SQL("INSERT INTO {}.runtime_maintenance_notes(phase,note,details) VALUES (%s,%s,%s)").format(s),
            (phase, note, Jsonb(details)),
        )
        cur.execute("RELEASE SAVEPOINT norm_integrity_audit")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT norm_integrity_audit")
        cur.execute("RELEASE SAVEPOINT norm_integrity_audit")


def repair_task_lineage_cur(cur, schema: str) -> dict[str, Any]:
    """Repair stale task parent UUIDs/IDs before UUID edge migration runs.

    Resolution order is intentionally conservative:
      1. surviving legacy parent_task_id,
      2. surviving parent_task_uuid,
      3. nearest surviving ancestor recorded in compact task_history,
      4. detach when no defensible parent remains.
    """
    s = sql.Identifier(schema)
    cur.execute(sql.SQL("""
        SELECT task_id,task_uuid::text,parent_task_id,parent_task_uuid::text,
               parent_step_id,parent_node_uuid::text,task_kind,task_depth
        FROM {}.task_runs ORDER BY started_at,task_id
    """).format(s))
    rows = cur.fetchall()
    if not rows:
        return {"checked": 0, "changed": 0, "remapped": 0, "detached": 0, "conflicts": 0}

    by_id: dict[str, dict[str, Any]] = {}
    uuid_to_id: dict[str, str] = {}
    for row in rows:
        item = {
            "task_id": str(row[0]), "task_uuid": str(row[1]),
            "parent_task_id": str(row[2]) if row[2] else None,
            "parent_task_uuid": str(row[3]) if row[3] else None,
            "parent_step_id": str(row[4]) if row[4] else None,
            "parent_node_uuid": str(row[5]) if row[5] else None,
            "task_kind": str(row[6] or "root"), "task_depth": int(row[7] or 0),
        }
        by_id[item["task_id"]] = item
        uuid_to_id[item["task_uuid"]] = item["task_id"]
    current_ids = set(by_id)
    history_parent = _history_parent_map(cur, schema)

    cur.execute(sql.SQL("SELECT node_id::text,task_id,legacy_step_id FROM {}.task_nodes").format(s))
    nodes = cur.fetchall()
    node_owner = {str(node_id): str(task_id) for node_id, task_id, _ in nodes}
    node_by_legacy = {(str(task_id), str(step_id)): str(node_id) for node_id, task_id, step_id in nodes}

    decisions: dict[str, dict[str, Any]] = {}
    conflicts = 0
    for task_id, item in by_id.items():
        legacy_parent = item["parent_task_id"]
        uuid_parent_id = uuid_to_id.get(item["parent_task_uuid"] or "")
        candidate: str | None = None
        source = "none"
        if legacy_parent in current_ids:
            candidate = legacy_parent
            source = "legacy_id"
            if uuid_parent_id and uuid_parent_id != legacy_parent:
                conflicts += 1
        elif uuid_parent_id in current_ids:
            candidate = uuid_parent_id
            source = "uuid"
        elif legacy_parent:
            candidate = _nearest_surviving_history_ancestor(legacy_parent, current_ids, history_parent)
            source = "history_ancestor" if candidate else "orphan"
        elif item["parent_task_uuid"]:
            source = "orphan"

        if candidate == task_id:
            candidate = None
            source = "self_cycle"
        decisions[task_id] = {"parent": candidate, "source": source}

    # Break any cycles in the resolved current-task graph rather than preserving an impossible lineage.
    for child_id in sorted(decisions):
        parent = decisions[child_id]["parent"]
        seen = {child_id}
        while parent:
            if parent in seen:
                decisions[child_id] = {"parent": None, "source": "cycle_detached"}
                break
            seen.add(parent)
            parent = decisions.get(parent, {}).get("parent")

    depth_cache: dict[str, int] = {}
    def depth_for(task_id: str, trail: set[str] | None = None) -> int:
        if task_id in depth_cache:
            return depth_cache[task_id]
        trail = set(trail or ())
        if task_id in trail:
            return 0
        trail.add(task_id)
        parent = decisions.get(task_id, {}).get("parent")
        depth = 0 if not parent else depth_for(parent, trail) + 1
        depth_cache[task_id] = min(depth, 512)
        return depth_cache[task_id]

    changed = remapped = detached = 0
    samples: list[dict[str, Any]] = []
    expected: dict[str, tuple[str | None, str | None]] = {}
    for task_id, item in by_id.items():
        decision = decisions[task_id]
        parent_id = decision["parent"]
        original_parent_id = item["parent_task_id"]
        original_parent_uuid = item["parent_task_uuid"]
        parent_uuid = by_id[parent_id]["task_uuid"] if parent_id else None

        # parent_step_id only has meaning when the exact original parent survived.
        parent_step_id = item["parent_step_id"] if parent_id and parent_id == original_parent_id else None
        parent_node_uuid: str | None = None
        if parent_id:
            if parent_step_id:
                parent_node_uuid = node_by_legacy.get((parent_id, parent_step_id))
            if not parent_node_uuid and item["parent_node_uuid"] and node_owner.get(item["parent_node_uuid"]) == parent_id:
                parent_node_uuid = item["parent_node_uuid"]
        new_depth = depth_for(task_id)

        is_changed = (
            original_parent_id != parent_id or original_parent_uuid != parent_uuid or
            item["parent_step_id"] != parent_step_id or item["parent_node_uuid"] != parent_node_uuid or
            item["task_depth"] != new_depth
        )
        if is_changed:
            changed += 1
            if parent_id and (parent_id != original_parent_id or parent_uuid != original_parent_uuid):
                remapped += 1
            if not parent_id and (original_parent_id or original_parent_uuid):
                detached += 1
            cur.execute(sql.SQL("""
                UPDATE {}.task_runs SET parent_task_id=%s,parent_task_uuid=%s::uuid,
                    parent_step_id=%s,parent_node_uuid=%s::uuid,task_depth=%s
                WHERE task_id=%s
            """).format(s), (parent_id, parent_uuid, parent_step_id, parent_node_uuid, new_depth, task_id))
            if len(samples) < 24:
                samples.append({
                    "task_id": task_id,
                    "old_parent_task_id": original_parent_id,
                    "old_parent_task_uuid": original_parent_uuid,
                    "new_parent_task_id": parent_id,
                    "resolution": decision["source"],
                })
        expected[item["task_uuid"]] = (parent_uuid, parent_node_uuid)

    # Remove only derived child/recovery edges that disagree with repaired parent lineage.
    cur.execute(sql.SQL("""
        SELECT edge_id::text,source_task_uuid::text,source_node_id::text,target_task_uuid::text,edge_type
        FROM {}.task_dependency_edges WHERE edge_type IN ('child','recovery')
    """).format(s))
    edges_removed = 0
    for edge_id, source_uuid, source_node, target_uuid, _edge_type in cur.fetchall():
        want_parent_uuid, want_parent_node = expected.get(str(target_uuid), (None, None))
        if want_parent_uuid != str(source_uuid) or (want_parent_node or None) != (str(source_node) if source_node else None):
            cur.execute(sql.SQL("DELETE FROM {}.task_dependency_edges WHERE edge_id=%s::uuid").format(s), (str(edge_id),))
            edges_removed += 1

    details = {
        "checked": len(rows), "changed": changed, "remapped": remapped, "detached": detached,
        "conflicts": conflicts, "derived_edges_removed": edges_removed, "samples": samples,
    }
    if changed or edges_removed:
        _maintenance_note(
            cur, schema, "task_lineage_repair",
            "Task lineage was reconciled before UUID dependency migration; exact surviving identities were preferred and irrecoverable parents were detached.",
            details,
        )
    return details


def prepare_task_prune_cur(cur, schema: str, task_ids: list[str]) -> dict[str, Any]:
    """Reparent surviving children before validated history pruning deletes task rows."""
    delete_ids = {str(v) for v in task_ids if str(v)}
    if not delete_ids:
        return {"children_reparented": 0, "children_detached": 0}
    s = sql.Identifier(schema)
    cur.execute(sql.SQL("""
        SELECT task_id,task_uuid::text,parent_task_id,parent_task_uuid::text,task_kind,task_depth
        FROM {}.task_runs
    """).format(s))
    rows = cur.fetchall()
    by_id: dict[str, dict[str, Any]] = {}
    uuid_to_id: dict[str, str] = {}
    for task_id, task_uuid, parent_id, parent_uuid, kind, depth in rows:
        item = {
            "task_id": str(task_id), "task_uuid": str(task_uuid),
            "parent_task_id": str(parent_id) if parent_id else None,
            "parent_task_uuid": str(parent_uuid) if parent_uuid else None,
            "task_kind": str(kind or "root"), "task_depth": int(depth or 0),
        }
        by_id[item["task_id"]] = item
        uuid_to_id[item["task_uuid"]] = item["task_id"]
    existing_delete_ids = delete_ids.intersection(by_id)
    delete_uuids = {by_id[task_id]["task_uuid"] for task_id in existing_delete_ids}
    survivor_ids = set(by_id) - existing_delete_ids
    history_parent = _history_parent_map(cur, schema)

    def nearest_surviving_from_deleted(start_id: str | None) -> str | None:
        current = str(start_id or "").strip()
        seen: set[str] = set()
        for _ in range(128):
            if not current or current in seen:
                return None
            seen.add(current)
            if current in survivor_ids:
                return current
            row = by_id.get(current)
            if row:
                next_id = row["parent_task_id"]
                if not next_id and row["parent_task_uuid"]:
                    next_id = uuid_to_id.get(row["parent_task_uuid"])
            else:
                next_id = history_parent.get(current)
            if not next_id:
                return _nearest_surviving_history_ancestor(current, survivor_ids, history_parent, excluded=existing_delete_ids)
            current = str(next_id)
        return None

    reparented = detached = 0
    samples: list[dict[str, Any]] = []
    for child_id, child in by_id.items():
        if child_id in existing_delete_ids:
            continue
        old_parent_id = child["parent_task_id"]
        old_parent_uuid = child["parent_task_uuid"]
        parent_is_deleted = old_parent_id in existing_delete_ids or old_parent_uuid in delete_uuids
        if not parent_is_deleted:
            continue
        removed_parent_id = (
            old_parent_id if old_parent_id in existing_delete_ids
            else uuid_to_id.get(old_parent_uuid or "") or old_parent_id
        )
        new_parent_id = nearest_surviving_from_deleted(removed_parent_id)
        if new_parent_id == child_id:
            new_parent_id = None
        new_parent_uuid = by_id[new_parent_id]["task_uuid"] if new_parent_id else None
        new_depth = (by_id[new_parent_id]["task_depth"] + 1) if new_parent_id else 0
        cur.execute(sql.SQL("""
            UPDATE {}.task_runs SET parent_task_id=%s,parent_task_uuid=%s::uuid,
                parent_step_id=NULL,parent_node_uuid=NULL,task_depth=%s
            WHERE task_id=%s
        """).format(s), (new_parent_id, new_parent_uuid, new_depth, child_id))
        if new_parent_id:
            reparented += 1
            cur.execute(sql.SQL("SELECT node_id::text FROM {}.task_nodes WHERE task_id=%s AND active ORDER BY ordinal LIMIT 1").format(s), (child_id,))
            row = cur.fetchone()
            target_node = str(row[0]) if row else None
            edge_type = "recovery" if child["task_kind"] == "recovery" else "child"
            cur.execute(sql.SQL("""
                SELECT 1 FROM {}.task_dependency_edges
                WHERE source_task_uuid=%s::uuid AND source_node_id IS NULL
                  AND target_task_uuid=%s::uuid AND target_node_id IS NOT DISTINCT FROM %s::uuid AND edge_type=%s LIMIT 1
            """).format(s), (new_parent_uuid, child["task_uuid"], target_node, edge_type))
            if not cur.fetchone():
                cur.execute(sql.SQL("""
                    INSERT INTO {}.task_dependency_edges(
                        edge_id,source_task_uuid,source_node_id,target_task_uuid,target_node_id,edge_type,metadata
                    ) VALUES (%s::uuid,%s::uuid,NULL,%s::uuid,%s::uuid,%s,%s)
                """).format(s), (
                    str(uuid.uuid4()), new_parent_uuid, child["task_uuid"], target_node, edge_type,
                    Jsonb({"reparented_before_prune": True, "removed_parent_task_id": removed_parent_id}),
                ))
        else:
            detached += 1
        if len(samples) < 24:
            samples.append({"task_id": child_id, "removed_parent_task_id": removed_parent_id, "new_parent_task_id": new_parent_id})

    details = {
        "deleting_tasks": len(existing_delete_ids), "children_reparented": reparented,
        "children_detached": detached, "samples": samples,
    }
    if reparented or detached:
        _maintenance_note(
            cur, schema, "deep_history_prune_lineage_repair",
            "Surviving child tasks were reparented to the nearest surviving ancestor, or explicitly detached, before validated task pruning.",
            details,
        )
    return details


def _tokens(text: str) -> set[str]:
    return {
        token.lower() for token in _TOKEN_RE.findall(str(text or ""))
        if token.lower() not in _STOPWORDS and not token.isdigit()
    }


def _cluster_score(tokens: set[str], cluster_tokens: Counter[str]) -> tuple[float, int]:
    if not tokens or not cluster_tokens:
        return 0.0, 0
    existing = set(cluster_tokens)
    shared = len(tokens & existing)
    if not shared:
        return 0.0, 0
    overlap = shared / max(1, min(len(tokens), len(existing)))
    jaccard = shared / max(1, len(tokens | existing))
    return (0.7 * overlap) + (0.3 * jaccard), shared


def _recovery_thread_title(items: list[dict[str, Any]]) -> str:
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(_tokens(item["content"]))
    useful = [word for word, _count in counts.most_common(4)]
    if useful:
        return "Recovered memory: " + " / ".join(useful)[:80]
    first = re.sub(r"\s+", " ", str(items[0]["content"] or "")).strip()
    return "Recovered memory: " + (first[:72] or "unclassified")


def repair_memory_threads_cur(cur, schema: str) -> dict[str, Any]:
    """Ensure every memory remains attached to a meaningful thread.

    Existing links are never rewritten. Unthreaded memories first inherit the thread(s)
    of their surviving source message. Only the remaining orphans are conservatively
    clustered into clearly-labelled recovered threads within the same project.
    """
    s = sql.Identifier(schema)
    cur.execute(sql.SQL("""
        SELECT mi.memory_id,mi.project_id,mi.memory_type,mi.content,mi.source_message_id,
               mi.created_at,mi.updated_at,COUNT(mt.thread_id)
        FROM {}.memory_items mi
        LEFT JOIN {}.memory_threads mt ON mt.memory_id=mi.memory_id
        GROUP BY mi.memory_id,mi.project_id,mi.memory_type,mi.content,mi.source_message_id,mi.created_at,mi.updated_at
        ORDER BY mi.created_at,mi.memory_id
    """).format(s, s))
    memories = [
        {
            "memory_id": str(r[0]), "project_id": str(r[1]), "memory_type": str(r[2]),
            "content": str(r[3]), "source_message_id": str(r[4]) if r[4] else None,
            "created_at": r[5], "updated_at": r[6], "thread_count": int(r[7] or 0),
        }
        for r in cur.fetchall()
    ]
    intact = sum(1 for m in memories if m["thread_count"] > 0)
    source_mapped = 0
    remaining: list[dict[str, Any]] = []

    for memory in memories:
        if memory["thread_count"] > 0:
            continue
        source_id = memory["source_message_id"]
        mapped = False
        if source_id:
            cur.execute(sql.SQL("""
                SELECT mt.thread_id,mt.is_primary,mt.relevance
                FROM {}.message_threads mt JOIN {}.threads t ON t.thread_id=mt.thread_id
                WHERE mt.message_id=%s AND t.project_id=%s
                ORDER BY mt.is_primary DESC,mt.relevance DESC
            """).format(s, s), (source_id, memory["project_id"]))
            source_threads = cur.fetchall()
            for thread_id, is_primary, relevance in source_threads:
                rel = max(float(relevance or 0.0), 1.0 if is_primary else 0.85)
                cur.execute(sql.SQL("""
                    INSERT INTO {}.memory_threads(memory_id,thread_id,relevance)
                    VALUES (%s,%s,%s) ON CONFLICT(memory_id,thread_id)
                    DO UPDATE SET relevance=GREATEST(memory_threads.relevance,EXCLUDED.relevance)
                """).format(s), (memory["memory_id"], str(thread_id), rel))
                mapped = True
            if mapped:
                source_mapped += 1
        if not mapped:
            remaining.append(memory)

    clusters_created = 0
    clustered_memories = 0
    singleton_threads = 0
    thread_samples: list[dict[str, Any]] = []
    by_project: dict[str, list[dict[str, Any]]] = {}
    for memory in remaining:
        by_project.setdefault(memory["project_id"], []).append(memory)

    for project_id, project_memories in by_project.items():
        clusters: list[dict[str, Any]] = []
        for memory in sorted(project_memories, key=lambda m: (m["created_at"], m["memory_id"])):
            mem_tokens = _tokens(memory["content"])
            best_index: int | None = None
            best_score = 0.0
            best_shared = 0
            for index, cluster in enumerate(clusters):
                score, shared = _cluster_score(mem_tokens, cluster["tokens"])
                latest: datetime = cluster["latest"]
                age_days = abs((memory["created_at"] - latest).total_seconds()) / 86400.0 if memory["created_at"] and latest else 9999.0
                coherent = (shared >= 2 and score >= 0.34 and age_days <= 45) or (shared >= 3 and score >= 0.48)
                if coherent and score > best_score:
                    best_index, best_score, best_shared = index, score, shared
            if best_index is None:
                clusters.append({
                    "items": [memory], "tokens": Counter(mem_tokens),
                    "earliest": memory["created_at"], "latest": memory["updated_at"] or memory["created_at"],
                })
            else:
                cluster = clusters[best_index]
                cluster["items"].append(memory)
                cluster["tokens"].update(mem_tokens)
                cluster["latest"] = max(cluster["latest"], memory["updated_at"] or memory["created_at"])

        for cluster in clusters:
            items = cluster["items"]
            thread_id = str(uuid.uuid4())
            title = _recovery_thread_title(items)
            cur.execute(sql.SQL("""
                INSERT INTO {}.threads(thread_id,project_id,parent_thread_id,title,status,created_at,updated_at)
                VALUES (%s,%s,NULL,%s,'active',%s,%s)
            """).format(s), (thread_id, project_id, title, cluster["earliest"], cluster["latest"]))
            for memory in items:
                cur.execute(sql.SQL("""
                    INSERT INTO {}.memory_threads(memory_id,thread_id,relevance)
                    VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
                """).format(s), (memory["memory_id"], thread_id, 0.72 if len(items) > 1 else 0.60))
            clusters_created += 1
            clustered_memories += len(items)
            if len(items) == 1:
                singleton_threads += 1
            if len(thread_samples) < 20:
                thread_samples.append({"thread_id": thread_id, "project_id": project_id, "title": title, "memories": len(items)})

    details = {
        "memories_checked": len(memories), "intact": intact, "source_mapped": source_mapped,
        "recovered_memories": clustered_memories, "recovered_threads": clusters_created,
        "singleton_recovered_threads": singleton_threads, "thread_samples": thread_samples,
    }
    if source_mapped or clustered_memories:
        _maintenance_note(
            cur, schema, "memory_thread_repair",
            "Unthreaded memories were first remapped from surviving source-message relationships, then conservatively grouped into recovered threads without altering intact memory links.",
            details,
        )
    return details
