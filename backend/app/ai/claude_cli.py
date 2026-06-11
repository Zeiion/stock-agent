"""Claude tier — drives the headless `claude -p` CLI for a structured Decision.

`claude` is a shell ALIAS to run_with_proxy, so the subprocess must go through a
login shell (`zsh -lc "<cmd>"`). We build the whole invocation as ONE shell
string, shlex.quote()-ing every interpolated part (schema JSON, system, model,
prompt), and run it in a neutral cwd.

With `--json-schema` the typed object lands in the JSON envelope field
`structured_result`; the envelope also carries free-text `result` and an
`is_error` flag. We prefer `structured_result`, falling back to JSON parsed out
of `result` (markdown fences tolerated).

Verified CLI facts (claude 2.1.168):
  - `--max-turns 1` FAILS with error_max_turns (the schema response uses a tool
    turn), so we use `--max-turns 4`.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import tempfile
from functools import lru_cache
from typing import Any, Optional

from .schema import REQUIRED_KEYS

# Matches a ```json ... ``` (or bare ``` ... ```) fenced block.
_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*(.*?)\s*```",
    re.DOTALL,
)


def _extract_json_object(text: str) -> dict:
    """Pull a JSON object out of a possibly markdown-fenced text blob.

    Tries, in order: a fenced block, the raw text, then the first balanced
    {...} span found by bracket scanning. Raises ValueError if none parse.
    """
    if not isinstance(text, str):
        raise ValueError(f"expected str, got {type(text).__name__}")

    candidates: list[str] = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(text.strip())

    # Last resort: scan for the first balanced top-level object.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
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
                    candidates.append(text[start : i + 1])
                    break

    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"no JSON object found in text: {text[:200]!r}")


def _missing_keys(decision: dict, required: Optional[list] = None) -> list[str]:
    return [k for k in (required or REQUIRED_KEYS) if k not in decision]


@lru_cache(maxsize=1)
def is_available() -> bool:
    """True if `claude --version` exits 0 via the login shell. Cached."""
    import subprocess

    try:
        proc = subprocess.run(
            ["zsh", "-lc", "claude --version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _build_command(prompt: str, system: str, schema: dict, model: str) -> str:
    """Assemble the one-line `claude -p ...` command string (all parts quoted)."""
    schema_json = json.dumps(schema, separators=(",", ":"))
    parts = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        shlex.quote(schema_json),
        "--max-turns",
        "4",
        "--no-session-persistence",
        "--model",
        shlex.quote(model),
        "--append-system-prompt",
        shlex.quote(system),
        shlex.quote(prompt),
    ]
    return " ".join(parts)


async def run(
    prompt: str,
    system: str,
    schema: dict,
    *,
    model: str,
    timeout_s: int,
    login_shell: bool = True,
) -> dict:
    """Run the claude CLI and return a dict matching DECISION_SCHEMA required keys.

    Raises on any failure (CLI error, timeout, unparseable output, missing keys)
    so the brain can fall through to the next tier.
    """
    cmd_str = _build_command(prompt, system, schema, model)

    if login_shell:
        argv = ["zsh", "-lc", cmd_str]
    else:
        argv = shlex.split(cmd_str)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,   # headless: never block reading stdin
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=tempfile.gettempdir(),
    )

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise TimeoutError(f"claude CLI timed out after {timeout_s}s")

    stdout = (out_b or b"").decode("utf-8", "replace").strip()
    stderr = (err_b or b"").decode("utf-8", "replace").strip()

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {proc.returncode}: {stderr or stdout or '(no output)'}"
        )
    if not stdout:
        raise RuntimeError(f"claude CLI produced no stdout (stderr: {stderr})")

    try:
        env: Any = json.loads(stdout)
    except (ValueError, TypeError) as e:
        # The whole stdout was not the JSON envelope; try to recover an object.
        try:
            env = _extract_json_object(stdout)
        except ValueError:
            raise RuntimeError(
                f"claude CLI returned non-JSON stdout: {stdout[:300]!r}"
            ) from e

    if not isinstance(env, dict):
        raise RuntimeError(f"claude CLI envelope not an object: {type(env).__name__}")

    if env.get("is_error"):
        detail = env.get("result") or env.get("error") or stderr or "(unknown)"
        raise RuntimeError(f"claude CLI reported is_error: {detail}")

    # claude 2.1.168 puts the schema-validated object in `structured_output`
    # (older builds used `structured_result`). Fall back to parsing free-text.
    decision: Optional[dict] = (env.get("structured_output")
                                or env.get("structured_result"))
    if not decision:
        result_text = env.get("result")
        if not result_text:
            raise RuntimeError(
                f"claude CLI envelope missing structured_output and result: "
                f"{stdout[:300]!r}"
            )
        decision = _extract_json_object(result_text)

    if not isinstance(decision, dict):
        raise RuntimeError(
            f"claude CLI decision not an object: {type(decision).__name__}"
        )

    missing = _missing_keys(decision, schema.get("required"))
    if missing:
        raise RuntimeError(
            f"claude CLI decision missing required keys {missing}; "
            f"got keys {sorted(decision.keys())}"
        )

    return decision
