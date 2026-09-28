"""Conservative temp/cache cleanup limited to the catalog in ``cleanup_catalog``."""

from __future__ import annotations

import os
from typing import Any

from boostai.actions.action_models import (
    ActionID,
    CleanupParams,
    ExecutionResult,
    Reversibility,
    ResultStatus,
    RiskLevel,
    Verification,
)
from boostai.actions.cleanup_catalog import CATALOG, TEMP_TARGETS, estimate, iter_candidates, resolve_roots
from boostai.actions.handlers.base import ActionContext, ActionDefinition, ActionHandler
from boostai.core.models import MB, fmt_bytes
from boostai.metrics.disk import system_drive
from boostai.security.safety_rules import FILE_ATTRIBUTE_REPARSE_POINT, validate_cleanup_root


class _CleanupBase(ActionHandler):
    params_model = CleanupParams
    temp_only: bool = True

    def describe_target(self, params: CleanupParams) -> str:
        return ", ".join(CATALOG[t].label if t in CATALOG else t for t in params.target_ids)

    def requires_admin(self, params: CleanupParams, ctx: ActionContext) -> bool:
        return any(CATALOG[t].requires_admin for t in params.target_ids if t in CATALOG)

    def admin_reason(self, params) -> str:
        return "the Windows TEMP folder is only writable by administrators"

    def check(self, params: CleanupParams, ctx: ActionContext) -> tuple[list[str], list[str]]:
        reasons: list[str] = []
        for tid in params.target_ids:
            if tid not in CATALOG:
                reasons.append(f"'{tid}' is not an approved cleanup location")
            elif self.temp_only and tid not in TEMP_TARGETS:
                reasons.append(f"'{tid}' is not a temporary-files location (use the cache cleanup action)")
            elif not self.temp_only and tid in TEMP_TARGETS:
                reasons.append(f"'{tid}' is a temporary-files location (use the temp cleanup action)")
        if reasons:
            return reasons, []
        running = {p.name.lower() for p in ctx.ops.list_processes()}
        warnings: list[str] = []
        for tid in params.target_ids:
            target = CATALOG[tid]
            blocking = [n for n in target.requires_closed if n.lower() in running]
            if blocking:
                reasons.append(f"{target.label}: close {', '.join(blocking)} first")
            if target.note:
                warnings.append(f"{target.label}: {target.note}")
        warnings.append("Deleted files cannot be restored. Only the listed locations are touched.")
        return reasons, warnings

    def capture_state(self, params: CleanupParams, ctx: ActionContext) -> dict[str, Any]:
        est = estimate(list(params.target_ids), ctx.settings.cleanup_min_age_hours, is_admin=ctx.is_admin)
        return {
            "targets": {e.target_id: {"files": e.files, "bytes": e.bytes} for e in est},
            "estimated_bytes": sum(e.bytes for e in est),
            "disk_free": ctx.ops.disk_free(system_drive()),
        }

    def execute(self, params: CleanupParams, ctx: ActionContext, state: dict[str, Any]) -> ExecutionResult:
        deleted = skipped = freed = 0
        dirs_removed = 0
        for tid in params.target_ids:
            target = CATALOG[tid]
            for path, size in iter_candidates(target, ctx.settings.cleanup_min_age_hours):
                if ctx.ops.delete_file(path):
                    deleted += 1
                    freed += size
                else:
                    skipped += 1
            dirs_removed += self._remove_empty_dirs(target, ctx)
        msg = f"Deleted {deleted} files ({fmt_bytes(freed)}); skipped {skipped} in-use or protected files."
        return ExecutionResult(
            success=deleted > 0 or skipped == 0,
            message=msg,
            new_state={"deleted": deleted, "skipped": skipped, "freed_bytes": freed, "dirs_removed": dirs_removed},
        )

    @staticmethod
    def _remove_empty_dirs(target, ctx: ActionContext) -> int:
        """Remove now-empty sub-folders (never the root itself, never through links)."""
        if not target.recursive:
            return 0
        removed = 0
        for root in resolve_roots(target):
            if not validate_cleanup_root(root)[0]:
                continue
            for current, dirs, _files in os.walk(root, topdown=False, followlinks=False):
                if os.path.normcase(current) == os.path.normcase(root):
                    continue
                try:
                    if os.lstat(current).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                except (OSError, AttributeError):
                    continue
                if ctx.ops.remove_empty_dir(current):
                    removed += 1
        return removed

    def verify(self, params, ctx, state, result) -> Verification:
        new = result.new_state
        free_after = ctx.ops.disk_free(system_drive())
        before = {"disk_free": state.get("disk_free"), "estimated_bytes": state.get("estimated_bytes")}
        after = {"disk_free": free_after, "deleted_files": new.get("deleted", 0)}
        measured = free_after - int(state.get("disk_free") or 0)
        improvement = {"freed_bytes": new.get("freed_bytes", 0), "disk_free_change": measured}
        if not result.success:
            return Verification(ResultStatus.FAILED, result.message, before, after, improvement)
        if new.get("freed_bytes", 0) < 1 * MB:
            return Verification(ResultStatus.NO_MEANINGFUL_CHANGE, "Nothing significant to remove. " + result.message,
                                before, after, improvement)
        return Verification(
            ResultStatus.SUCCESS,
            f"{result.message} Measured change in free space on {system_drive()}: {fmt_bytes(measured)} "
            "(other programs writing to disk can affect this number).",
            before, after, improvement,
        )


class ClearTempHandler(_CleanupBase):
    temp_only = True
    definition = ActionDefinition(
        action_id=ActionID.CLEAR_SAFE_TEMP_FILES,
        name="Remove safe temporary files",
        description="Deletes old files from approved temporary folders only (TEMP, crash dumps, error reports).",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.NOT_REVERSIBLE,
        affected_component="Temporary files",
        requirements=("Targets from the approved catalog only", "Files older than the age limit"),
        validation_checks=(
            "Target IDs must be in the catalog",
            "Roots may not be personal folders, drive roots or junctions",
            "No link following; each file re-checked to be inside its root",
        ),
        execution_method="os.remove on individual files; locked files skipped",
        verification_method="Counts deleted bytes and measures free disk space before/after",
        warning="Deleted temporary files cannot be restored.",
    )


class ClearCacheHandler(_CleanupBase):
    temp_only = False
    definition = ActionDefinition(
        action_id=ActionID.CLEAR_SPECIFIC_SAFE_CACHE,
        name="Clear a specific cache",
        description="Clears one approved cache (shader, thumbnail or browser *cache only* - never cookies/history/passwords).",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.NOT_REVERSIBLE,
        affected_component="Application caches",
        requirements=("Cache ID from the approved catalog", "Owning application closed where required"),
        validation_checks=("Cache ID must be in the catalog", "Required applications must be closed"),
        execution_method="os.remove on individual files inside the named cache folders",
        verification_method="Counts deleted bytes and measures free disk space before/after",
        warning="Caches are rebuilt automatically; the first use afterwards may be slower.",
    )
