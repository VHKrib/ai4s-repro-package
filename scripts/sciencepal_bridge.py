#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SciencePal API bridge (minimal end-to-end harness).

Subcommands:
  initiate     POST /api/agent/initiate   (multipart/form-data, NO auto-retry)
  status       GET  /api/agent-run/{id}
  poll         loop status until terminal / timeout (records transitions)
  text         GET  /api/agent-run/{id}/stream  (SSE, captures text deltas)
  upload       POST /api/sandboxes/{sid}/files  (multipart upload)
  list-files   GET  /api/sandboxes/{sid}/files
  read-content GET  /api/sandboxes/{sid}/files/content
  catalog      GET  a few harmless list endpoints (agent catalog discovery)

Design rules:
  * API key is read at runtime from an env var or a local key file and NEVER printed.
    Every log line / stdout payload passes through _redact().
  * initiate NEVER auto-retries (a retry creates a second sandbox + run and
    bills twice). A lock file records each successful/timeout initiate; a
    second initiate requires an explicit --i-accept-another-billing flag.
  * All output on stdout is a single structured JSON object (log goes to file).
  * client timeout for initiate is >= 300s (sandbox creation is inlined and
    routinely exceeds 60s; a client timeout is NOT a failure).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

# --- self-heal a broken CA bundle env var (this host points SSL_CERT_FILE /
# CURL_CA_BUNDLE at a nonexistent path, which makes every TLS call fail with
# "Could not find a suitable TLS CA certificate bundle"). Falls back to certifi.
def _fix_ca_env() -> None:
    try:
        import certifi

        good = certifi.where()
    except Exception:
        return
    for var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        cur = os.environ.get(var)
        if cur and not os.path.exists(cur):
            os.environ[var] = good
    if not os.environ.get("REQUESTS_CA_BUNDLE"):
        os.environ["REQUESTS_CA_BUNDLE"] = good


_fix_ca_env()

BASE = "https://sciencepal.ai/api"

# All local paths come from the environment with package-relative defaults, so the
# bridge can run from any checkout. The API key is never read from a shared path by
# default: put it in <package>/secrets/sciencepal_key.txt or point A4S_SCIENCEPAL_KEY_FILE
# at a file containing only the key.
PKG_ROOT = Path(os.environ.get("A4S_ROOT") or Path(__file__).resolve().parent.parent)
KEY_PATH = Path(os.environ.get("A4S_SCIENCEPAL_KEY_FILE") or PKG_ROOT / "secrets" / "sciencepal_key.txt")
LOG_DIR = Path(os.environ.get("A4S_LOGS") or PKG_ROOT / "logs")
LOCK_PATH = Path(os.environ.get("A4S_SCIENCEPAL_LOCK") or PKG_ROOT / "sciencepal_initiate_lock.json")
RUNS_DIR = Path(os.environ.get("A4S_RUNS") or PKG_ROOT / "runs")
BIOLOGY_AGENT = "10000000-0000-0000-0000-000000000002"

_LOG_FH = None


# ---------------------------------------------------------------- redaction
def _read_key() -> str:
    raw = KEY_PATH.read_text(encoding="utf-8", errors="replace")
    return raw.strip().lstrip("\ufeff").strip()


def _redact(text: str, key: str) -> str:
    if not text:
        return text
    if key:
        text = text.replace(key, "<redacted-key>")
        # also mask fragments, in case a truncated key leaks
        if len(key) >= 12:
            text = text.replace(key[:8], "<redacted-key8>")
    low = text.lower()
    i = 0
    out = []
    while True:
        j = low.find("bearer ", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i : j + 7])
        k = j + 7
        while k < len(text) and text[k] not in '"\r\n ,;}':
            k += 1
        out.append("<redacted>")
        i = k
    return "".join(out)


def log(msg: str, key: str = "") -> None:
    global _LOG_FH
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {_redact(str(msg), key)}"
    print(line, file=sys.stderr, flush=True)
    if _LOG_FH:
        _LOG_FH.write(line + "\n")
        _LOG_FH.flush()


def open_log(tag: str) -> Path:
    global _LOG_FH
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    p = LOG_DIR / f"sciencepal_bridge_{tag}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    _LOG_FH = p.open("a", encoding="utf-8")
    return p


# ---------------------------------------------------------------- http core
class Api:
    def __init__(self, key: str, verbose: bool = True):
        self.key = key
        self.verbose = verbose
        self.s = requests.Session()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}

    def call(
        self,
        method: str,
        path: str,
        timeout: float = 60,
        params: dict | None = None,
        data: dict | None = None,
        files=None,
        stream: bool = False,
        label: str | None = None,
    ) -> dict:
        url = path if path.startswith("http") else BASE + path
        label = label or f"{method} {path}"
        hdrs = self._headers()
        shown_headers = {k: ("<redacted>" if k.lower() == "authorization" else v) for k, v in hdrs.items()}
        log(f"--> {label} url={url} params={params} data={data} files={[f[0] for f in (files or [])][:3]} timeout={timeout}")
        log(f"    headers={shown_headers}")
        t0 = time.time()
        try:
            r = self.s.request(
                method,
                url,
                headers=hdrs,
                params=params,
                data=data,
                files=files,
                timeout=(30, timeout),
                stream=stream,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as e:
            el = time.time() - t0
            log(f"<-- {label} TIMEOUT after {el:.1f}s ({type(e).__name__})")
            return {
                "label": label,
                "url": url,
                "method": method,
                "http_status": None,
                "outcome": "client_timeout",
                "elapsed_s": round(el, 2),
                "error": _redact(f"{type(e).__name__}: {e}", self.key),
                "note": "client timeout is NOT a server failure",
            }
        except Exception as e:
            el = time.time() - t0
            log(f"<-- {label} ERROR after {el:.1f}s {type(e).__name__}: {_redact(str(e), self.key)}")
            return {
                "label": label,
                "url": url,
                "method": method,
                "http_status": None,
                "outcome": "transport_error",
                "elapsed_s": round(el, 2),
                "error": _redact(f"{type(e).__name__}: {e}", self.key),
            }
        el = time.time() - t0
        body_txt = ""
        raw_bytes = b""
        try:
            if stream:
                body_txt = ""
            else:
                raw_bytes = r.content  # exact bytes off the wire (never re-encoded)
                body_txt = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_txt = "<unreadable body>"
        log(f"<-- {label} HTTP {r.status_code} in {el:.2f}s len={len(body_txt)} ct={r.headers.get('content-type')}")
        if body_txt:
            log("    body: " + body_txt[:4000])
        parsed = None
        ct_low = (r.headers.get("content-type") or "").lower()
        if body_txt and "json" in ct_low:
            try:
                parsed = r.json()
            except Exception:
                parsed = None
        elif body_txt and body_txt.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(body_txt)
            except Exception:
                parsed = None
        return {
            "label": label,
            "url": url,
            "method": method,
            "http_status": r.status_code,
            "ok": 200 <= r.status_code < 300,
            "elapsed_s": round(el, 2),
            "content_type": r.headers.get("content-type"),
            "request_id": r.headers.get("x-request-id") or r.headers.get("request-id"),
            "json": parsed,
            "body_text": None if parsed is not None else _redact(body_txt[:4000], self.key),
            "_raw": raw_bytes,
            "_resp": r,
        }


def emit(obj: dict) -> None:
    obj = {k: v for k, v in obj.items() if not k.startswith("_")}


    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if not k.startswith("_")}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o

    print(json.dumps(clean(obj), ensure_ascii=False, indent=2, default=str))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- commands
def cmd_initiate(a: Api, ns) -> dict:
    # --- billing guard: one initiate per explicit operator decision
    if LOCK_PATH.exists() and not ns.i_accept_another_billing:
        prev = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        return {
            "cmd": "initiate",
            "refused": True,
            "reason": "initiate lock present; refusing to create a second sandbox/run (would double-bill)",
            "previous": prev,
            "hint": "re-run with --i-accept-another-billing only after checking the previous run",
        }
    data = {
        "prompt": ns.prompt,
        "agent_id": ns.agent_id,
        "agent_selection_explicit": "true",
        "stream": "true" if ns.stream else "false",
    }
    if ns.model_name:
        data["model_name"] = ns.model_name
    if ns.token_budget:
        data["token_budget"] = str(ns.token_budget)
    if ns.enable_thinking is not None:
        data["enable_thinking"] = "true" if ns.enable_thinking else "false"
    if ns.reasoning_effort:
        data["reasoning_effort"] = ns.reasoning_effort
    if ns.enable_context_manager is not None:
        data["enable_context_manager"] = "true" if ns.enable_context_manager else "false"

    # multipart/form-data: files= is what forces the multipart encoder in requests
    files = [(k, (None, v)) for k, v in data.items()]
    res = a.call("POST", "/agent/initiate", timeout=ns.timeout, files=files, label="POST /agent/initiate")

    j = res.get("json") or {}
    out = {
        "cmd": "initiate",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "outcome": res.get("outcome", "http"),
        "request_id": res.get("request_id"),
        "sandbox_id": j.get("sandbox_id"),
        "thread_id": j.get("thread_id"),
        "agent_run_id": j.get("agent_run_id"),
        "server_keys": sorted(j.keys()) if isinstance(j, dict) else None,
        "response": j if j else res.get("body_text"),
        "client_timeout_s": ns.timeout,
        "retried": False,
    }
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(
        json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "prompt": ns.prompt,
                "agent_id": ns.agent_id,
                "http_status": res.get("http_status"),
                "sandbox_id": j.get("sandbox_id"),
                "thread_id": j.get("thread_id"),
                "agent_run_id": j.get("agent_run_id"),
                "elapsed_s": res.get("elapsed_s"),
                "outcome": res.get("outcome", "http"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def cmd_status(a: Api, ns) -> dict:
    res = a.call("GET", f"/agent-run/{ns.run_id}", timeout=ns.timeout, label=f"GET /agent-run/{ns.run_id}")
    j = res.get("json") or {}
    return {
        "cmd": "status",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "agent_run_id": j.get("id") or ns.run_id,
        "thread_id": j.get("threadId"),
        "status": j.get("status"),
        "started_at": j.get("startedAt"),
        "completed_at": j.get("completedAt"),
        "error": j.get("error"),
        "response_keys": sorted(j.keys()) if isinstance(j, dict) else None,
        "response": j if j else res.get("body_text"),
    }


def cmd_poll(a: Api, ns) -> dict:
    t0 = time.time()
    transitions = []
    last = None
    final = None
    while True:
        res = a.call("GET", f"/agent-run/{ns.run_id}", timeout=60, label="GET /agent-run (poll)")
        j = res.get("json") or {}
        st = j.get("status")
        rec = {
            "t_rel_s": round(time.time() - t0, 1),
            "http_status": res.get("http_status"),
            "status": st,
            "elapsed_s": res.get("elapsed_s"),
        }
        if st != last:
            transitions.append(rec)
            log(f"    status transition -> {st} at +{rec['t_rel_s']}s (HTTP {res.get('http_status')})")
            last = st
        if res.get("http_status") is None:
            transitions.append({"t_rel_s": rec["t_rel_s"], "http_status": None, "status": "poll_transport_error"})
        if st in ("completed", "failed", "stopped"):
            final = j
            break
        if time.time() - t0 > ns.timeout:
            break
        time.sleep(ns.interval)
    return {
        "cmd": "poll",
        "agent_run_id": ns.run_id,
        "terminal_status": (final or {}).get("status"),
        "poll_total_s": round(time.time() - t0, 1),
        "transitions": transitions,
        "timed_out": final is None,
        "final": final,
    }


def _harvest(obj, sink: list) -> None:
    """Pull the assistant text out of one SSE data object.

    Observed shapes on sciencepal.ai:
      {"type":"assistant","content":{"role":"assistant","content":"..."}}
      {"type":"assistant_response_end","content":"{json chat.completion}"}
      {"type":"status","content":"{json status text}"}
      {"choices":[{"message":{"content":"..."}}]}
    Only real assistant prose is harvested; status/telemetry JSON is dropped.
    """
    if not isinstance(obj, dict):
        return
    t = obj.get("type")
    content = obj.get("content")

    def _chat_completion(c):
        if isinstance(c, dict):
            for ch in c.get("choices") or []:
                m = (ch or {}).get("message") or {}
                if isinstance(m.get("content"), str) and m["content"].strip():
                    sink.append(m["content"])
                    return True
        return False

    if _chat_completion(obj):
        return
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                inner = json.loads(stripped)
            except Exception:
                inner = None
            if inner is not None:
                # nested chat.completion (assistant_response_end)
                if _chat_completion(inner):
                    return
                # status payloads: keep only human-readable failures
                if isinstance(inner, dict) and inner.get("status_type") in ("error", "run_error", "failed"):
                    sink.append(json.dumps(inner, ensure_ascii=False))
                return
        if t in (None, "assistant", "message", "text", "delta"):
            if stripped:
                sink.append(content)
        return
    if isinstance(content, dict):
        # {"type":"assistant","content":{"role":"assistant","content":"..."}}
        c = content.get("content")
        if isinstance(c, str) and c.strip():
            sink.append(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                    sink.append(part["text"])
        return
    for k in ("output_text", "text", "delta"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            sink.append(v)


def cmd_text(a: Api, ns) -> dict:
    res = a.call(
        "GET",
        f"/agent-run/{ns.run_id}/stream",
        timeout=ns.timeout,
        stream=True,
        label=f"GET /agent-run/{ns.run_id}/stream (SSE)",
    )
    r = res.pop("_resp", None)
    chunks = []
    event_names = []
    raw_lines = 0
    text_parts = []
    if r is not None and res.get("http_status") and 200 <= res["http_status"] < 300:
        deadline = time.time() + ns.timeout
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    break
                if raw is None:
                    continue
                raw_lines += 1
                chunks.append(raw)
                if raw.startswith("event:"):
                    event_names.append(raw[6:].strip())
                if raw.startswith("data:"):
                    payload = raw[5:].strip()
                    if payload and payload != "[DONE]":
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            obj = None
                        if obj is None:
                            text_parts.append(payload)
                        else:
                            _harvest(obj, text_parts)
                if len(chunks) > 4000:
                    break
        except Exception as e:
            res["read_error"] = _redact(f"{type(e).__name__}: {e}", a.key)
        try:
            r.close()
        except Exception:
            pass
    text = "\n".join(t for t in text_parts if t).strip()
    # the same prose arrives twice (type=assistant, then assistant_response_end
    # wrapping a chat.completion) -- keep each distinct block once
    seen = set()
    uniq = []
    for part in text_parts:
        p = part.strip()
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    text = "\n".join(uniq).strip()
    out = {
        "cmd": "text",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "content_type": res.get("content_type"),
        "raw_lines": raw_lines,
        "event_names": event_names[:40],
        "distinct_event_names": sorted(set(event_names)),
        "assembled_text": text,
        "assembled_text_len": len(text),
        "first_lines": chunks[:40],
        "read_error": res.get("read_error", None),
        "body_text_when_not_2xx": res.get("body_text") if not (res.get("http_status") and 200 <= res["http_status"] < 300) else None,
    }
    if text:
        p = RUNS_DIR / f"sciencepal_text_{ns.run_id}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        out["saved_text_path"] = str(p)
    return out


def cmd_upload(a: Api, ns) -> dict:
    src = Path(ns.file)
    if not src.exists():
        return {"cmd": "upload", "error": f"local file not found: {src}"}
    sha = sha256_file(src)
    size = src.stat().st_size
    field = ns.field
    data = {}
    if ns.path:
        data["path"] = ns.path
    if getattr(ns, "overwrite", False):
        data["overwrite"] = "true"
    with src.open("rb") as fh:
        files = [(field, (ns.remote_name or src.name, fh.read(), "application/octet-stream"))]
    res = a.call(
        "POST",
        f"/sandboxes/{ns.sandbox_id}/files",
        timeout=ns.timeout,
        data=data or None,
        files=files,
        label=f"POST /sandboxes/{ns.sandbox_id}/files (field={field})",
    )
    j = res.get("json") or {}
    return {
        "cmd": "upload",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "field_name_used": field,
        "local_path": str(src),
        "remote_name": ns.remote_name or src.name,
        "bytes": size,
        "sha256_local": sha,
        "response": j if j else res.get("body_text"),
    }


def cmd_list(a: Api, ns) -> dict:
    params = {}
    if ns.path:
        params["path"] = ns.path
    res = a.call(
        "GET",
        f"/sandboxes/{ns.sandbox_id}/files",
        timeout=ns.timeout,
        params=params or None,
        label=f"GET /sandboxes/{ns.sandbox_id}/files",
    )
    j = res.get("json")
    items = []
    if isinstance(j, dict):
        for k in ("files", "data", "items", "results"):
            if isinstance(j.get(k), list):
                items = j[k]
                break
    elif isinstance(j, list):
        items = j
    return {
        "cmd": "list-files",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "count": len(items),
        "items": items[:200],
        "response_shape_keys": sorted(j.keys()) if isinstance(j, dict) else type(j).__name__,
        "response_raw": None if items else j or res.get("body_text"),
    }


def cmd_read(a: Api, ns) -> dict:
    params = {"path": ns.path}
    if ns.field:
        params["field"] = ns.field
    res = a.call(
        "GET",
        f"/sandboxes/{ns.sandbox_id}/files/content",
        timeout=ns.timeout,
        params=params,
        label="GET /sandboxes/{sid}/files/content",
    )
    ct = res.get("content_type") or ""
    raw = res.get("_raw") or b""
    unpack = "raw-bytes"
    j = res.get("json")
    body_txt = None
    # Only unwrap JSON when the server actually declared JSON. An octet-stream
    # body that merely *looks* like JSON must be kept as raw bytes, otherwise
    # re-serialising it changes the bytes and breaks checksum equality.
    if j is not None and "json" in ct.lower():
        for k in ("content", "text", "data", "body"):
            if isinstance(j, dict) and isinstance(j.get(k), str):
                body_txt = j[k]
                unpack = f"json[{k}]"
                raw = body_txt.encode("utf-8")
                break
        if body_txt is None:
            body_txt = json.dumps(j, ensure_ascii=False)
            unpack = "json(dump)"
            raw = body_txt.encode("utf-8")
    elif raw:
        body_txt = raw.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw).hexdigest()
    saved = None
    if ns.save_to and raw:
        Path(ns.save_to).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.save_to).write_bytes(raw)
        saved = ns.save_to
    return {
        "cmd": "read-content",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "path": ns.path,
        "content_type": ct,
        "unpack_mode": unpack,
        "bytes": len(raw),
        "sha256_remote": sha,
        "content_len": len(body_txt or ""),
        "content_head": (body_txt or "")[:600],
        "saved_to": saved,
    }


def cmd_thread_sandbox(a: Api, ns) -> dict:
    """Resolve sandbox_id from a thread_id (initiate returns sandbox_id=null)."""
    res = a.call("GET", f"/thread/{ns.thread_id}/sandbox", timeout=ns.timeout, label="GET /thread/{id}/sandbox")
    j = res.get("json") or {}
    return {
        "cmd": "thread-sandbox",
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "thread_id": j.get("thread_id") or ns.thread_id,
        "project_id": j.get("project_id"),
        "sandbox_id": j.get("sandbox_id"),
        "sandbox_info": j.get("sandbox_info"),
        "response": j if j else res.get("body_text"),
    }


def cmd_get(a: Api, ns) -> dict:
    """Generic authenticated GET, for endpoint discovery (no billing side effects)."""
    res = a.call("GET", ns.path, timeout=ns.timeout, params=None, label=f"GET {ns.path}")
    j = res.get("json")
    return {
        "cmd": "get",
        "path": ns.path,
        "http_status": res.get("http_status"),
        "elapsed_s": res.get("elapsed_s"),
        "json": j if (isinstance(j, (list, dict)) and len(json.dumps(j)) < 30000) else res.get("body_text"),
    }


def cmd_catalog(a: Api, ns) -> dict:
    paths = ["/agents", "/agent-catalog", "/catalogs/agents", "/agents/catalog"]
    out = []
    for p in paths:
        res = a.call("GET", p, timeout=30, label=f"GET {p}")
        j = res.get("json")
        n = None
        if isinstance(j, list):
            n = len(j)
        elif isinstance(j, dict):
            for k in ("agents", "data", "items", "results"):
                if isinstance(j.get(k), list):
                    n = len(j[k])
                    break
        out.append(
            {
                "path": p,
                "http_status": res.get("http_status"),
                "elapsed_s": res.get("elapsed_s"),
                "count": n,
                "json": j if (isinstance(j, (list, dict)) and len(json.dumps(j)) < 20000) else res.get("body_text"),
            }
        )
    return {"cmd": "catalog", "probes": out}


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="SciencePal API bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("initiate")
    p.add_argument("--prompt", required=True)
    p.add_argument("--agent-id", default=BIOLOGY_AGENT)
    p.add_argument("--model-name", default=None)
    p.add_argument("--token-budget", type=int, default=None)
    p.add_argument("--enable-thinking", dest="enable_thinking", action="store_true", default=None)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--enable-context-manager", dest="enable_context_manager", action="store_true", default=None)
    p.add_argument("--stream", action="store_true", default=False)
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--i-accept-another-billing", dest="i_accept_another_billing", action="store_true")

    p = sub.add_parser("status")
    p.add_argument("--run-id", required=True)
    p.add_argument("--timeout", type=float, default=60.0)

    p = sub.add_parser("poll")
    p.add_argument("--run-id", required=True)
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=600.0)

    p = sub.add_parser("text")
    p.add_argument("--run-id", required=True)
    p.add_argument("--timeout", type=float, default=60.0)

    p = sub.add_parser("upload")
    p.add_argument("--sandbox-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--path", required=True, help="target path INSIDE the sandbox, e.g. /workspace/result.json")
    p.add_argument("--remote-name", default=None)
    p.add_argument("--field", default="file")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--timeout", type=float, default=120.0)

    p = sub.add_parser("list-files")
    p.add_argument("--sandbox-id", required=True)
    p.add_argument("--path", default="/", help="directory inside the sandbox (required by the API)")
    p.add_argument("--timeout", type=float, default=60.0)

    p = sub.add_parser("thread-sandbox")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--timeout", type=float, default=60.0)

    p = sub.add_parser("read-content")
    p.add_argument("--sandbox-id", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--field", default=None)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--save-to", default=None)

    p = sub.add_parser("catalog")
    p.add_argument("--timeout", type=float, default=30.0)

    p = sub.add_parser("get")
    p.add_argument("--path", required=True)
    p.add_argument("--timeout", type=float, default=60.0)

    ns = ap.parse_args()
    logp = open_log(ns.cmd)
    key = _read_key()
    log(f"=== sciencepal_bridge.py {ns.cmd} === logfile={logp} key_len={len(key)} (value never logged)")
    api = Api(key)

    fn = {
        "initiate": cmd_initiate,
        "status": cmd_status,
        "poll": cmd_poll,
        "text": cmd_text,
        "upload": cmd_upload,
        "list-files": cmd_list,
        "read-content": cmd_read,
        "catalog": cmd_catalog,
        "get": cmd_get,
        "thread-sandbox": cmd_thread_sandbox,
    }[ns.cmd]
    out = fn(api, ns)
    out["log_file"] = str(logp)
    emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
