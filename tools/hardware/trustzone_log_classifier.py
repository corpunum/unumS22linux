#!/usr/bin/env python3
"""Conservatively classify fatal and hung-task indicators in kernel logs.

This module keeps independent signal sets instead of treating stack-trace
markers as faults. A hung-task warning remains a liveness concern regardless
of the task's name; callers may clear that concern only by supplying separate,
relevant progress evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_FATAL_PATTERNS = (
    ("kernel_panic", re.compile(r"\bkernel panic\b|\bpanic\s+-\s+not syncing\b", re.I)),
    ("oops", re.compile(r"\boops\s*:", re.I)),
    ("bug", re.compile(r"\bBUG\s*:|\bkernel BUG at\b", re.I)),
    ("unable_to_handle_kernel", re.compile(r"\bUnable to handle kernel\b", re.I)),
    ("general_protection_fault", re.compile(r"\bgeneral protection fault\b", re.I)),
    ("out_of_memory", re.compile(r"\bOut of memory\b", re.I)),
    ("oom_kill", re.compile(r"\boom-kill\b", re.I)),
    ("soft_lockup", re.compile(r"\bsoft\s+lockup\b", re.I)),
    ("hard_lockup", re.compile(r"\bhard\s+lockup\b", re.I)),
)
_HUNG_TASK_RE = re.compile(
    r"\bblocked for more than\s+(?P<seconds>\d+)\s+seconds?\b", re.I
)
_TASK_ID_RE = re.compile(r"\btask\s+(?P<name>[^\s:]+):(?P<pid>\d+)\b", re.I)
_CALL_TRACE_RE = re.compile(r"\bCall trace\s*:", re.I)
_PARTIAL_TIMESTAMP_RE = re.compile(r"^\s*\[\s*\d+(?:\.\d+)?[^\]]*$")


@dataclass(frozen=True)
class FatalIndicator:
    line_number: int
    kind: str


@dataclass(frozen=True)
class HungTaskWarning:
    line_number: int
    task_name: str | None
    pid: int | None
    blocked_seconds: int


@dataclass(frozen=True)
class KernelLogClassification:
    fatal_indicators: tuple[FatalIndicator, ...]
    hung_task_warnings: tuple[HungTaskWarning, ...]
    call_trace_lines: tuple[int, ...]
    malformed_line_numbers: tuple[int, ...]
    input_available: bool
    encoding_errors: bool
    unterminated_final_line: bool
    capture_complete: bool
    coverage_complete: bool
    material_liveness_unresolved: bool
    assessment: str

    @property
    def has_fatal_indicator(self) -> bool:
        return bool(self.fatal_indicators)


def classify_kernel_log(
    log: str | bytes | None,
    *,
    relevant_progress_evidence: bool = False,
    capture_complete: bool = True,
) -> KernelLogClassification:
    """Classify a log without treating ``Call trace:`` as a fault.

    ``relevant_progress_evidence`` must come from an independent observation
    relevant to the potentially blocked work. Thread names and a stack trace
    are not progress evidence. ``capture_complete=False`` lets a collector
    report known truncation even when its byte stream ends on a line boundary.
    """
    if type(relevant_progress_evidence) is not bool:
        raise TypeError("relevant_progress_evidence must be a bool")
    if type(capture_complete) is not bool:
        raise TypeError("capture_complete must be a bool")

    input_available = isinstance(log, (str, bytes))
    encoding_errors = False
    text = ""
    if isinstance(log, bytes):
        try:
            text = log.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            text = log.decode("utf-8", errors="replace")
            encoding_errors = True
    elif isinstance(log, str):
        text = log

    unterminated_final_line = bool(text) and not text.endswith(("\n", "\r"))
    lines = text.splitlines()
    fatal_indicators: list[FatalIndicator] = []
    hung_task_warnings: list[HungTaskWarning] = []
    call_trace_lines: list[int] = []
    malformed_line_numbers: list[int] = []

    for line_number, line in enumerate(lines, start=1):
        if "\x00" in line or "\ufffd" in line or _PARTIAL_TIMESTAMP_RE.match(line):
            malformed_line_numbers.append(line_number)

        for kind, pattern in _FATAL_PATTERNS:
            if pattern.search(line):
                fatal_indicators.append(FatalIndicator(line_number, kind))

        hung_match = _HUNG_TASK_RE.search(line)
        if hung_match:
            task_match = _TASK_ID_RE.search(line)
            hung_task_warnings.append(HungTaskWarning(
                line_number=line_number,
                task_name=task_match.group("name") if task_match else None,
                pid=int(task_match.group("pid")) if task_match else None,
                blocked_seconds=int(hung_match.group("seconds")),
            ))

        if _CALL_TRACE_RE.search(line):
            call_trace_lines.append(line_number)

    coverage_complete = (
        input_available
        and bool(text.strip())
        and capture_complete
        and not encoding_errors
        and not unterminated_final_line
        and not malformed_line_numbers
    )
    if fatal_indicators:
        assessment = "fatal"
    elif hung_task_warnings:
        assessment = "hung_task_warning"
    elif call_trace_lines:
        assessment = "trace_only"
    elif coverage_complete:
        assessment = "no_indicators"
    else:
        assessment = "incomplete"

    return KernelLogClassification(
        fatal_indicators=tuple(fatal_indicators),
        hung_task_warnings=tuple(hung_task_warnings),
        call_trace_lines=tuple(call_trace_lines),
        malformed_line_numbers=tuple(malformed_line_numbers),
        input_available=input_available,
        encoding_errors=encoding_errors,
        unterminated_final_line=unterminated_final_line,
        capture_complete=capture_complete,
        coverage_complete=coverage_complete,
        material_liveness_unresolved=(
            bool(hung_task_warnings) and not relevant_progress_evidence
        ),
        assessment=assessment,
    )


__all__ = [
    "FatalIndicator",
    "HungTaskWarning",
    "KernelLogClassification",
    "classify_kernel_log",
]
