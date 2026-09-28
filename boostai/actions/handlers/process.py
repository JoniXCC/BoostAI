"""Process actions: close, restart, trim working set.

Closing is *polite first*: BoostAI posts WM_CLOSE (the same message the window's X button
sends) so the application can ask the user to save work. Forced termination only happens
if the user explicitly ticked "force if it does not respond" for that specific action.
"""

from __future__ import annotations

import os
import time
from typing import Any

from boostai.actions.action_models import (
    ActionID,
    ExecutionResult,
    ProcessParams,
    Reversibility,
    ResultStatus,
    RiskLevel,
    Verification,
)
from boostai.actions.handlers.base import ActionContext, ActionDefinition, ActionHandler
from boostai.core.models import MB, ProcessInfo, fmt_bytes
from boostai.security.protected_processes import evaluate_process

CLOSE_TIMEOUT_S = 15.0
FORCE_TIMEOUT_S = 5.0
POLL_S = 0.5
RESTART_SETTLE_S = 10.0


def _norm(path: str | None) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def find_target(processes: list[ProcessInfo], params: ProcessParams) -> ProcessInfo | None:
    for p in processes:
        if p.pid == params.pid and abs(p.create_time - params.create_time) < 1.0 and p.name.lower() == params.name.lower():
            return p
    return None


def app_instances(target: ProcessInfo, processes: list[ProcessInfo]) -> list[ProcessInfo]:
    """All processes of the same application (same executable, same owner) - e.g. every chrome.exe."""
    if not target.exe or not target.username:
        return [target]  # identity unknown: never group unrelated processes together
    exe = _norm(target.exe)
    return [
        p for p in processes
        if _norm(p.exe) == exe and (p.username or "").lower() == (target.username or "").lower()
    ] or [target]


def _protection_reasons(instances: list[ProcessInfo], ctx: ActionContext) -> list[str]:
    service_pids = ctx.ops.service_pids()
    reasons: list[str] = []
    for inst in instances:
        verdict = evaluate_process(inst, self_pid=ctx.self_pid, service_pids=service_pids, user=ctx.user)
        for r in verdict.reasons:
            msg = f"{inst.name} (PID {inst.pid}): {r}"
            if msg not in reasons:
                reasons.append(msg)
    return reasons


class _ProcessHandlerBase(ActionHandler):
    params_model = ProcessParams
    whole_app = True

    def describe_target(self, params: ProcessParams) -> str:
        return f"{params.name} (PID {params.pid})"

    def check(self, params: ProcessParams, ctx: ActionContext) -> tuple[list[str], list[str]]:
        processes = ctx.ops.list_processes()
        target = find_target(processes, params)
        if target is None:
            return ["The process is no longer running (or its PID now belongs to a different process)."], []
        instances = app_instances(target, processes) if self.whole_app else [target]
        reasons = _protection_reasons(instances, ctx)
        return reasons, self.warnings(target, instances, params)

    def warnings(self, target: ProcessInfo, instances: list[ProcessInfo], params: ProcessParams) -> list[str]:
        return []

    def capture_state(self, params: ProcessParams, ctx: ActionContext) -> dict[str, Any]:
        processes = ctx.ops.list_processes()
        target = find_target(processes, params)
        if target is None:
            return {}
        instances = app_instances(target, processes) if self.whole_app else [target]
        pids = {p.pid for p in instances}
        root = next((p for p in instances if p.ppid not in pids), target)
        return {
            "name": target.name,
            "exe": target.exe,
            "instances": [[p.pid, p.create_time] for p in instances],
            "instance_count": len(instances),
            "private_bytes": sum(p.private for p in instances),
            "working_set": sum(p.rss for p in instances),
            "root_cmdline": ctx.ops.process_cmdline(root.pid) if self.relaunch else None,
            "available_memory": ctx.ops.memory_available(),
            "captured_at": time.time(),
        }

    relaunch = False

    @staticmethod
    def _wait_exit(ctx: ActionContext, remaining, timeout_s: float) -> list[tuple[int, float]]:
        left = remaining()
        for _ in range(int(timeout_s / POLL_S)):
            if not left:
                break
            ctx.sleep(POLL_S)
            left = remaining()
        return left

    def _close_all(self, params: ProcessParams, ctx: ActionContext, state: dict[str, Any]) -> tuple[bool, str]:
        instances = [(int(pid), float(ct)) for pid, ct in state.get("instances", [])]
        if not instances:
            return False, "Process state could not be captured; nothing was done."
        windows = sum(ctx.ops.post_close(pid) for pid, _ in instances)

        def remaining() -> list[tuple[int, float]]:
            return [(pid, ct) for pid, ct in instances if ctx.ops.is_running(pid, ct)]

        left = self._wait_exit(ctx, remaining, CLOSE_TIMEOUT_S if windows else 0.0)
        if not left:
            return True, f"{params.name} closed normally."
        if not params.force_if_unresponsive:
            why = "it has no visible window to close" if not windows else "it did not close (it may be asking to save work)"
            return False, f"{params.name} is still running because {why}. Nothing was forced."
        for pid, _ in left:
            ctx.ops.terminate(pid)
        left = self._wait_exit(ctx, remaining, FORCE_TIMEOUT_S)
        if left:
            return False, f"{len(left)} {params.name} process(es) could not be ended."
        return True, f"{params.name} did not respond to a normal close and was ended (you approved forcing)."


class CloseProcessHandler(_ProcessHandlerBase):
    definition = ActionDefinition(
        action_id=ActionID.CLOSE_USER_PROCESS,
        name="Close application",
        description="Politely closes all windows of a user application; optionally ends it if it does not respond.",
        risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.NOT_REVERSIBLE,
        affected_component="User application process",
        requirements=("Process owned by the current user", "Not a protected/system/security process"),
        validation_checks=(
            "PID + creation time match (no PID reuse)",
            "Deterministic protected-process rules",
            "Not hosting a Windows service",
        ),
        execution_method="WM_CLOSE to visible windows; TerminateProcess only if explicitly approved",
        verification_method="Confirms the process exited and measures memory released",
        warning="Unsaved work in this application may be lost. Save your work first.",
    )

    def warnings(self, target, instances, params) -> list[str]:
        msg = f"Closing {target.name} will close all of its windows ({len(instances)} process(es)). Save your work first."
        return [msg] + (["Forcing may lose unsaved work."] if params.force_if_unresponsive else [])

    def execute(self, params: ProcessParams, ctx: ActionContext, state: dict[str, Any]) -> ExecutionResult:
        ok, message = self._close_all(params, ctx, state)
        return ExecutionResult(success=ok, message=message, new_state={"running": not ok})

    def verify(self, params, ctx, state, result) -> Verification:
        ctx.sleep(2.0)
        exe = _norm(state.get("exe"))
        after = [p for p in ctx.ops.list_processes() if exe and _norm(p.exe) == exe and p.create_time <= state.get("captured_at", 0)]
        before_mem = int(state.get("private_bytes", 0))
        after_mem = sum(p.private for p in after)
        freed = before_mem - after_mem
        avail_delta = ctx.ops.memory_available() - int(state.get("available_memory", 0))
        before = {"private_bytes": before_mem, "processes": state.get("instance_count", 0)}
        after_d = {"private_bytes": after_mem, "processes": len(after)}
        improvement = {"memory_released": freed, "available_memory_change": avail_delta}
        if not result.success:
            return Verification(ResultStatus.FAILED, result.message, before, after_d, improvement)
        if after:
            return Verification(ResultStatus.PARTIAL, f"{len(after)} instance(s) are still running.", before, after_d, improvement)
        return Verification(
            ResultStatus.SUCCESS,
            f"{params.name} closed. Its processes were using {fmt_bytes(before_mem)} of private memory; "
            f"available RAM changed by {fmt_bytes(avail_delta)}.",
            before, after_d, improvement,
        )


class RestartProcessHandler(_ProcessHandlerBase):
    relaunch = True
    definition = ActionDefinition(
        action_id=ActionID.RESTART_PROCESS,
        name="Restart application",
        description="Closes a user application and starts it again with its original command line.",
        risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.NOT_APPLICABLE,
        affected_component="User application process",
        requirements=("Process owned by the current user", "Executable path known", "Not protected"),
        validation_checks=(
            "PID + creation time match",
            "Deterministic protected-process rules",
            "Relaunch uses the application's own recorded executable (never AI-provided text)",
        ),
        execution_method="Polite close (WM_CLOSE), then relaunch the recorded executable without a shell",
        verification_method="Compares the application's private memory before and after the restart",
        warning="Restarting may close open windows or documents. Save your work first.",
    )

    def warnings(self, target, instances, params) -> list[str]:
        msg = f"Restarting {target.name} may close active windows. Save your work before continuing."
        return [msg] + (["Forcing may lose unsaved work."] if params.force_if_unresponsive else [])

    def check(self, params, ctx):
        reasons, warnings = super().check(params, ctx)
        processes = ctx.ops.list_processes()
        target = find_target(processes, params)
        if target is not None and not target.exe:
            reasons.append("the executable path is unknown, so it could not be restarted safely")
        return reasons, warnings

    def execute(self, params: ProcessParams, ctx: ActionContext, state: dict[str, Any]) -> ExecutionResult:
        ok, message = self._close_all(params, ctx, state)
        if not ok:
            return ExecutionResult(False, message)
        exe = state.get("exe")
        argv = list(state.get("root_cmdline") or [])
        if not argv or _norm(argv[0]) != _norm(exe):
            argv = [exe]  # never run a command line whose program differs from the verified executable
        launched = ctx.ops.launch(argv, os.path.dirname(exe) if exe else None)
        if not launched:
            return ExecutionResult(False, f"{message} It could not be started again - please reopen it manually.", partial=True)
        return ExecutionResult(True, f"{message} Restarted.", new_state={"relaunched": True})

    def verify(self, params, ctx, state, result) -> Verification:
        before_mem = int(state.get("private_bytes", 0))
        before = {"private_bytes": before_mem, "processes": state.get("instance_count", 0)}
        if not result.success:
            status = ResultStatus.PARTIAL if result.partial else ResultStatus.FAILED
            return Verification(status, result.message, before, {}, {})
        ctx.sleep(RESTART_SETTLE_S)
        exe = _norm(state.get("exe"))
        started_after = float(state.get("captured_at", 0))
        new = [p for p in ctx.ops.list_processes() if _norm(p.exe) == exe and p.create_time >= started_after - 1]
        after_mem = sum(p.private for p in new)
        after = {"private_bytes": after_mem, "processes": len(new)}
        freed = before_mem - after_mem
        improvement = {"memory_released": freed}
        if not new:
            return Verification(ResultStatus.PARTIAL, "The application closed but no new instance was detected.", before, after, improvement)
        note = f" (measured {int(RESTART_SETTLE_S)} s after restart; usage may rise again as it reloads content)"
        if freed >= max(100 * MB, 0.10 * before_mem):
            return Verification(
                ResultStatus.SUCCESS,
                f"Memory: {fmt_bytes(before_mem)} -> {fmt_bytes(after_mem)}; recovered ~{fmt_bytes(freed)}{note}.",
                before, after, improvement,
            )
        return Verification(
            ResultStatus.NO_MEANINGFUL_CHANGE,
            f"No meaningful improvement detected: {fmt_bytes(before_mem)} -> {fmt_bytes(after_mem)}{note}.",
            before, after, improvement,
        )


class TrimWorkingSetHandler(_ProcessHandlerBase):
    whole_app = False
    definition = ActionDefinition(
        action_id=ActionID.TRIM_PROCESS_WORKING_SET,
        name="Trim working set",
        description=(
            "Asks Windows to move a process's idle pages out of physical RAM. Does not free its committed memory; "
            "the effect is often temporary because pages are reloaded when the app uses them."
        ),
        risk=RiskLevel.LOW,
        reversibility=Reversibility.NOT_APPLICABLE,
        affected_component="Process working set",
        requirements=("Process owned by the current user", "Not protected"),
        validation_checks=("PID + creation time match", "Deterministic protected-process rules"),
        execution_method="SetProcessWorkingSetSizeEx(-1, -1)",
        verification_method="Compares working set before/after; reports that private memory is unchanged",
    )

    def warnings(self, target, instances, params) -> list[str]:
        return ["The application may feel briefly slower while pages are reloaded."]

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.trim_working_set(params.pid)
        return ExecutionResult(ok, "Working set trim requested." if ok else "Windows refused the trim request.")

    def verify(self, params, ctx, state, result) -> Verification:
        before = {"working_set": state.get("working_set", 0), "private_bytes": state.get("private_bytes", 0)}
        if not result.success:
            return Verification(ResultStatus.FAILED, result.message, before, {}, {})
        ctx.sleep(2.0)
        target = find_target(ctx.ops.list_processes(), params)
        if target is None:
            return Verification(ResultStatus.FAILED, "The process exited during verification.", before, {}, {})
        after = {"working_set": target.rss, "private_bytes": target.private}
        reduced = int(before["working_set"]) - target.rss
        improvement = {"working_set_reduction": reduced}
        if reduced >= 50 * MB:
            return Verification(
                ResultStatus.SUCCESS,
                f"Working set {fmt_bytes(before['working_set'])} -> {fmt_bytes(target.rss)}. Committed (private) memory "
                f"is unchanged at {fmt_bytes(target.private)}; the reduction may be temporary.",
                before, after, improvement,
            )
        return Verification(ResultStatus.NO_MEANINGFUL_CHANGE, "No meaningful improvement detected.", before, after, improvement)
