"""Codex CLI tier — drives `codex exec` headless for a structured Decision.

`codex` is a shell ALIAS to run_with_proxy, so every spawn MUST go through a
login shell: ["zsh", "-lc", "<full command string>"]. The whole command is
assembled as one string with user-supplied parts shlex.quote()-d.

codex-cli 0.137.0 invocation:
    codex exec --skip-git-repo-check --ephemeral --sandbox read-only \
        --output-schema <schemafile> -o <outfile> [--model <m>] <prompt>

codex exec has NO separate system-prompt flag, so the system text is prepended
into the prompt. The final structured answer is read from <outfile>; if that is
empty/unparseable we fall back to scanning stdout for a JSON object.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import tempfile
from functools import lru_cache
from typing import Any, Optional

from .schema import REQUIRED_KEYS


@lru_cache(maxsize=1)
def is_available() -> bool:
    """True if the `codex` CLI responds to --version through a login shell."""
    try:
        proc = subprocess_run_version()
        return proc == 0
    except Exception:
        return False


def subprocess_run_version() -> int:
    """Run `codex --version` via a login shell; return its exit code."""
    import subprocess

    try:
        completed = subprocess.run(
            ["zsh", "-lc", "codex --version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        return completed.returncode
    except Exception:
        return 1


def _extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a single JSON object from free text.

    Strips ```json / ``` fences, then tries a direct parse, then falls back to
    the first balanced { ... } span found in the text.
    """
    if not text:
        return None
    s = text.strip()
    # strip markdown code fences
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    # direct parse first
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # scan for the first balanced object
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break
        start = s.find("{", start + 1)
    return None


def _validate(obj: Any, required: Optional[list] = None) -> dict:
    """Ensure the parsed object is a dict carrying every required key."""
    if not isinstance(obj, dict):
        raise ValueError(f"codex output is not a JSON object: {type(obj).__name__}")
    missing = [k for k in (required or REQUIRED_KEYS) if k not in obj]
    if missing:
        raise ValueError(f"codex output missing required keys: {missing}")
    return obj


async def run(
    prompt: str,
    system: str,
    schema: dict,
    *,
    model: str,
    timeout_s: int,
    login_shell: bool = True,
) -> dict:
    """Run `codex exec` and return a dict matching DECISION_SCHEMA. Raises on failure."""
    schema_path: Optional[str] = None
    out_path: Optional[str] = None
    try:
        # temp schema file
        sfd, schema_path = tempfile.mkstemp(prefix="codex_schema_", suffix=".json")
        with os.fdopen(sfd, "w", encoding="utf-8") as fh:
            json.dump(schema, fh)
        # temp output file (created up front so codex can write to it)
        ofd, out_path = tempfile.mkstemp(prefix="codex_out_", suffix=".txt")
        os.close(ofd)

        full_prompt = f"{system}\n\n{prompt}"

        parts = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox", "read-only",
            "--output-schema", schema_path,
            "-o", out_path,
        ]
        if model:
            parts += ["--model", model]
        parts.append(full_prompt)

        cmd_str = " ".join(shlex.quote(p) for p in parts)

        if login_shell:
            argv = ["zsh", "-lc", cmd_str]
        else:
            argv = parts

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,   # else codex blocks/errs reading stdin
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise TimeoutError(f"codex exec timed out after {timeout_s}s")

        stdout = (stdout_b or b"").decode("utf-8", "replace")
        stderr = (stderr_b or b"").decode("utf-8", "replace")

        # primary: read the structured output file
        obj: Optional[dict] = None
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                file_text = fh.read()
            obj = _extract_json_object(file_text)
        except OSError:
            obj = None

        # fallback: scan stdout for a JSON object
        if obj is None:
            obj = _extract_json_object(stdout)

        if obj is None:
            detail = (stderr or stdout or "").strip()[:1000]
            raise ValueError(
                f"codex exec produced no parseable JSON "
                f"(rc={proc.returncode}): {detail}"
            )

        return _validate(obj, schema.get("required"))
    finally:
        for path in (schema_path, out_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
