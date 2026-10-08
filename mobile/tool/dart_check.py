"""Type-check the Dart sources by driving the analysis server over LSP.

Why this exists
---------------
On affected machines the Dart VM cannot spawn subprocesses that use pipes: the
spawn fails with Windows `ERROR_PIPE_BUSY` (231) at `process_win.cc:742`.
When that happens, everything that shells out dies at startup:

    flutter create / flutter run / flutter test   -> "CreateFile failed 231"
    dart analyze                                  -> same
    dart run / dart compile                       -> same (native-assets hooks)

This harness exists as the fallback for those machines. On machines where
`dart analyze` completes normally, the normal analyzer is the preferred gate;
do not claim the fallback is generally required.

The analysis server is an AOT snapshot that speaks LSP over stdio. Python *can*
spawn it with pipes, so this drives it directly. It is the same engine
`dart analyze` would have used, with the same `analysis_options.yaml`.

Three things about driving it that are not obvious, and each one silently
produces "0 diagnostics" rather than an error:

1. **`initialize` must declare the workspace capabilities.** With only
   `textDocument.publishDiagnostics`, this build ignores `workspaceFolders`
   entirely, never creates an analysis context, and answers every `didOpen` with
   nothing. It looks exactly like a clean tree. Declaring
   `workspace.workspaceFolders` (+ `didChangeConfiguration`) fixes it.
2. **A clean file produces no `publishDiagnostics` at all** — not even an empty
   list to clear it. So "no diagnostics arrived" cannot be told apart from "not
   analysed yet". The completion signal is `$/analyzerStatus`: opening a document
   flips `isAnalyzing` to true and back to false. This script waits on that, and
   reports a harness failure if it never sees a status.
3. **Opening all ~97 documents at once wedges it.** It keeps answering the
   status stream but never emits diagnostics. Open in small batches and wait for
   idle after each.

`textDocument/diagnostic` (pull) is not implemented by this build — it answers
`-32601 Unknown method` — so push is the only channel.

Two things that do work, for the record:

    dart format --line-length 100 lib tool
    dart --packages=.dart_tool/package_config.json <script.dart>

The second bypasses dartdev entirely — `dart <file>` goes through dartdev, which
is what runs the native-assets build hooks for `objective_c` (pulled in by
`flutter_secure_storage_darwin`) and blows up. Passing `--packages` explicitly
runs the VM straight on the script. Use that to execute
`tool/verify_contract.dart`.

Usage:
    python tool/dart_check.py [--settle SECONDS] [--batch N] [project_root] [file.dart ...]

With no file arguments it checks everything under `lib/`, `test/` and `tool/`.

Exit code 0 = no errors, 1 = errors found, 2 = harness failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

FLUTTER_ROOT = os.environ.get("FLUTTER_ROOT", r"C:\Users\user\flutter")
DART_SDK = os.path.join(FLUTTER_ROOT, "bin", "cache", "dart-sdk")
RUNTIME = os.path.join(DART_SDK, "bin", "dartaotruntime.exe")
SNAPSHOT = os.path.join(DART_SDK, "bin", "snapshots", "analysis_server_aot.dart.snapshot")

SEVERITY = {1: "ERROR", 2: "WARNING", 3: "INFO", 4: "HINT"}


def path_to_uri(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    return "file:///" + p.lstrip("/")


class AnalysisServer:
    def __init__(self, root: str, verbose: bool = False, dump: bool = False):
        self.root = os.path.abspath(root)
        self.verbose = verbose
        self.dump = dump
        self.stderr_lines: list[str] = []
        self.notices: list[str] = []
        self.proc = subprocess.Popen(
            [RUNTIME, SNAPSHOT, "--lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
            env={**os.environ, "FLUTTER_ROOT": FLUTTER_ROOT},
        )
        self.messages: queue.Queue = queue.Queue()
        self.next_id = 1
        self.diag: dict[str, list] = {}
        self.seen_methods: dict[str, int] = {}
        self.analyzing = False
        self.status_seen = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---- plumbing ---------------------------------------------------------
    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def _read_stdout(self) -> None:
        stream = self.proc.stdout
        assert stream is not None
        while True:
            length = None
            while True:
                line = stream.readline()
                if not line:
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            if length is None:
                continue
            try:
                msg = json.loads(stream.read(length))
            except Exception:
                return
            method = msg.get("method")
            if method:
                self.seen_methods[method] = self.seen_methods.get(method, 0) + 1
            if self.dump:
                kind = "req " if "id" in msg and method else ("resp" if "id" in msg else "note")
                print(f"  << {kind} {method or msg.get('id')} {str(msg.get('params', ''))[:200]}")
            if method == "textDocument/publishDiagnostics":
                self.diag[msg["params"]["uri"]] = msg["params"]["diagnostics"]
            elif method == "$/analyzerStatus":
                self.analyzing = bool(msg["params"].get("isAnalyzing"))
                self.status_seen += 1
            elif method in ("window/showMessage", "window/logMessage"):
                # The server reports its own failures here — an unresolvable
                # package, a bad analysis_options.yaml — and says nothing about
                # it on stderr. Without surfacing these, a wedged server looks
                # exactly like a clean bill of health.
                text = msg["params"].get("message", "")
                self.notices.append(f"{method}: {text}")
            self.messages.put(msg)

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 120.0) -> dict:
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg.get("id") == rid:
                return msg
        raise TimeoutError(f"no reply to {method} within {timeout}s")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ---- protocol ---------------------------------------------------------
    def start(self) -> None:
        reply = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": path_to_uri(self.root),
                "capabilities": {
                    "workspace": {
                        "workspaceFolders": True,
                        "didChangeConfiguration": {"dynamicRegistration": True},
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                    },
                    "textDocument": {"publishDiagnostics": {}},
                },
                "initializationOptions": {},
                "workspaceFolders": [
                    {"uri": path_to_uri(self.root), "name": os.path.basename(self.root)}
                ],
            },
        )
        if "error" in reply:
            raise RuntimeError(f"initialize failed: {reply['error']}")
        self.notify("initialized", {})

    def _wait_idle(self, baseline: int, timeout: float) -> bool:
        """Block until the server has finished analysing what we just opened.

        `$/analyzerStatus` is the only completion signal this build offers. It
        does **not** push an empty diagnostic list to clear a clean file — a
        file with no problems produces no `publishDiagnostics` at all — so "no
        diagnostics arrived" cannot be told apart from "not analysed yet". The
        status stream can: opening a document flips `isAnalyzing` to true and
        then back to false.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.25)
            if self.status_seen > baseline and not self.analyzing:
                return True
        return False

    def analyze(self, files: list[str], settle: float, batch_size: int = 6) -> dict[str, list]:
        """Open [files] in batches and collect their diagnostics.

        Batched because opening all ~50 documents at once makes the server
        publish nothing within any sane timeout — it answers the status stream
        but never produces diagnostics, and the run looks clean when it is
        really just unfinished.
        """
        timed_out = 0
        for start in range(0, len(files), batch_size):
            batch = files[start : start + batch_size]
            baseline = self.status_seen
            for f in batch:
                with open(f, encoding="utf-8") as fh:
                    text = fh.read()
                self.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": path_to_uri(f),
                            "languageId": "dart",
                            "version": 1,
                            "text": text,
                        }
                    },
                )
            if not self._wait_idle(baseline, settle):
                timed_out += 1
        if timed_out:
            self.notices.append(f"{timed_out} batch(es) never reported idle")
        return dict(self.diag)

    def stop(self) -> None:
        try:
            self.request("shutdown", {}, timeout=15)
            self.notify("exit", {})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def collect(root: str) -> list[str]:
    files: list[str] = []
    for sub in ("lib", "test", "tool"):
        for base, _dirs, names in os.walk(os.path.join(root, sub)):
            files += [os.path.join(base, n) for n in sorted(names) if n.endswith(".dart")]
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="project root (default: .)")
    parser.add_argument("files", nargs="*", help="specific files (default: all of lib/test/tool)")
    parser.add_argument("--settle", type=float, default=90.0, help="max seconds to wait per batch")
    parser.add_argument("--batch", type=int, default=6, help="docs to open at once (default 6)")
    parser.add_argument("-v", "--verbose", action="store_true", help="dump server stderr")
    parser.add_argument("--dump", action="store_true", help="dump the raw LSP stream")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    files = args.files or collect(root)
    if not files:
        print(f"no .dart files under {root}", file=sys.stderr)
        return 2
    if not os.path.isdir(DART_SDK):
        print(f"dart sdk not found at {DART_SDK}", file=sys.stderr)
        return 2

    server = AnalysisServer(root, verbose=args.verbose, dump=args.dump)
    try:
        server.start()
        diags = server.analyze(files, args.settle, args.batch)
    except Exception as exc:  # a harness failure, reported as such
        print(f"harness failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        for line in server.stderr_lines[-20:]:
            print(f"  server: {line}", file=sys.stderr)
        return 2
    finally:
        server.stop()

    errors = 0
    for uri in sorted(diags):
        items = diags[uri]
        if not items:
            continue
        rel = uri.replace("file:///", "").replace("/", os.sep)
        with contextlib.suppress(ValueError):
            rel = os.path.relpath(rel, root)
        for d in items:
            sev = SEVERITY.get(d.get("severity", 1), "?")
            line = d["range"]["start"]["line"] + 1
            col = d["range"]["start"]["character"] + 1
            print(f"{rel}:{line}:{col} [{sev}] {d.get('message')}")
            if sev == "ERROR":
                errors += 1

    total = sum(len(v) for v in diags.values())
    print(
        f"\n--- {len(files)} file(s) opened, {len(diags)} with diagnostics, "
        f"{total} diagnostic(s), {errors} error(s) ---"
    )

    # `status_seen == 0` means the server never ran a single analysis. That is a
    # harness problem and must not be reported as a clean tree — distinguishing
    # those two is the whole reason `analyze` waits on `$/analyzerStatus`.
    if server.status_seen == 0:
        print("the analysis server never reported a status — harness failure", file=sys.stderr)
        print(f"  methods seen: {server.seen_methods}", file=sys.stderr)
        for notice in server.notices[-20:]:
            print(f"  notice: {notice}", file=sys.stderr)
        for line in server.stderr_lines[-20:]:
            print(f"  server: {line}", file=sys.stderr)
        return 2
    for notice in server.notices[-20:]:
        print(f"notice: {notice}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
