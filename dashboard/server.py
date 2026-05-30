#!/usr/bin/env python3
"""Local control panel for the daily video editor.

A tiny dependency free web server (Python standard library only) that exposes
the existing editing engine through a browser UI. It reads and writes
config.yaml, lists and accepts clips, runs the pipeline, streams the log, and
serves finished videos for preview and download.

This is the local demo. The same API shape (config, clips, run, logs, outputs)
maps cleanly onto a shelly-admin tile later, where the worker runs the render.

Run it:
    cd ~/daily-video-agent
    source .venv/bin/activate        # so auto-editor and faster-whisper are available
    python3 dashboard/server.py
Then open http://localhost:8765
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

# Make the repo importable so we can reuse the engine settings and helpers.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

try:
    import yaml  # PyYAML, present in the project venv
except ImportError:
    sys.stderr.write(
        "\nPyYAML is not available. Run the dashboard with the project venv:\n"
        "    cd " + str(ROOT) + "\n"
        "    source .venv/bin/activate\n"
        "    python3 dashboard/server.py\n"
        "or install it with: pip install pyyaml\n\n"
    )
    sys.exit(1)

from pipeline import presets, util  # noqa: E402

PORT = int(os.environ.get("DVA_DASHBOARD_PORT", "8765"))
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
EDIT_SECTIONS = [
    "target", "cutting", "motion", "transitions", "captions",
    "color", "audio", "broll", "creative_llm", "intro", "notify",
]

# Run state shared across request threads.
_run_lock = threading.Lock()
RUN = {"proc": None, "lines": [], "started_at": None, "done": True, "returncode": None}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def resolve(path_str: str) -> Path:
    """Resolve a config path that may be relative to the repo root."""
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def read_config_raw() -> dict:
    cfg = ROOT / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        return yaml.safe_load(cfg.read_text()) or {}
    except Exception:
        return {}


def baseline_for(preset: str) -> dict:
    bundle = presets.PRESET_BUNDLES.get(preset, {})
    return presets.deep_merge(presets.DEFAULTS, bundle)


def nested_diff(base: dict, sub: dict) -> dict:
    """Keep only the leaves of sub that differ from base."""
    out: dict = {}
    for k, v in sub.items():
        b = base.get(k)
        if isinstance(v, dict) and isinstance(b, dict):
            d = nested_diff(b, v)
            if d:
                out[k] = d
        elif v != b:
            out[k] = v
    return out


def write_config(payload: dict) -> dict:
    preset = payload.get("preset", "punchy")
    if preset not in presets.PRESET_BUNDLES:
        preset = "punchy"
    base = baseline_for(preset)
    submitted = payload.get("settings", {}) or {}
    overrides = {}
    for sec in EDIT_SECTIONS:
        if sec in submitted and isinstance(submitted[sec], dict):
            d = nested_diff(base.get(sec, {}), submitted[sec])
            if d:
                overrides[sec] = d

    out = {
        "preset": preset,
        "watch_folder": payload.get("watch_folder", base.get("watch_folder", "./input")),
        "output_folder": payload.get("output_folder", base.get("output_folder", "./output")),
        "archive_processed": bool(payload.get("archive_processed", base.get("archive_processed", True))),
    }
    out.update(overrides)

    header = (
        "# Daily video editor config. Written by the control panel.\n"
        "# preset sets the overall look. Anything below it overrides the preset.\n\n"
    )
    body = yaml.safe_dump(out, sort_keys=False, allow_unicode=True, default_flow_style=False)
    (ROOT / "config.yaml").write_text(header + body)
    return out


def list_media(folder: Path) -> list:
    items = []
    if folder.exists():
        for f in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                st = f.stat()
                items.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return items


IDEAS_PROMPT = (
    "You are a short form video director for Shelly, a restaurant in Caulfield, Melbourne "
    "(Instagram @shellyrestaurant_). Produce a shoot plan a team member can film TODAY on a "
    "phone, held in portrait, for one punchy 20 to 35 second social video. "
    "Respond with ONLY valid JSON, no preamble and no code fence, in exactly this shape: "
    '{"theme":"the angle of today\'s video in a few words",'
    '"hook":"the opening line or on screen text that stops the scroll",'
    '"shots":[{"action":"a concrete physical thing to film","why":"why it lands"}],'
    '"caption":"the post caption with a few hashtags",'
    '"audio":"a music or sound idea"}. '
    "Give 6 to 8 shots. Make the actions concrete and fun to film, for example pour the coffee "
    "in slow motion, steam rising off a fresh plate, a plate smash for the hook, hands plating a "
    "dish, the espresso pull, a candid laugh. Keep it specific to a restaurant and cafe. "
    "Do not use any dash characters in the text."
)


def _extract_json(text: str):
    a = text.find("{")
    b = text.rfind("}")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        return json.loads(text[a:b + 1])
    except Exception:
        return None


def gen_ideas(notes: str) -> dict:
    prompt = IDEAS_PROMPT
    if notes:
        prompt += "\n\nNotes from the team about today: " + notes
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            cwd=str(ROOT), capture_output=True, text=True, timeout=200,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "The Claude CLI was not found on PATH. Install Claude Code to enable shoot ideas."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out waiting for ideas. Try again."}
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return {"ok": False, "error": ((proc.stderr or "No response from the model.").strip())[:400]}
    data = _extract_json(out)
    if data:
        return {"ok": True, "ideas": data}
    return {"ok": True, "raw": out}


def start_run() -> dict:
    with _run_lock:
        if RUN["proc"] is not None and RUN["proc"].poll() is None:
            return {"started": False, "reason": "already running"}
        RUN["lines"] = []
        RUN["done"] = False
        RUN["returncode"] = None
        RUN["started_at"] = int(time.time())
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "run.py")],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        RUN["proc"] = proc

    def pump():
        for line in iter(proc.stdout.readline, ""):
            with _run_lock:
                RUN["lines"].append(line.rstrip("\n"))
        proc.stdout.close()
        rc = proc.wait()
        with _run_lock:
            RUN["done"] = True
            RUN["returncode"] = rc
            RUN["lines"].append(f"__RUN_FINISHED__ exit code {rc}")

    threading.Thread(target=pump, daemon=True).start()
    return {"started": True}


# ----------------------------------------------------------------------------
# http handler
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DVADashboard/1.0"

    def log_message(self, *args):  # quiet console
        pass

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, ctype: str, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def _serve_file_range(self, path: Path):
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start_s, _, end_s = rng[6:].partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            start = min(start, end)
            with open(path, "rb") as fh:
                fh.seek(start)
                chunk = fh.read(end - start + 1)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            self._send_bytes(data, ctype, extra={"Accept-Ranges": "bytes"})

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path

        if p == "/" or p == "/index.html":
            html = (HERE / "index.html").read_bytes()
            self._send_bytes(html, "text/html; charset=utf-8")
            return

        if p == "/api/config":
            settings = util.load_config()
            self._send_json({
                "defaults": presets.DEFAULTS,
                "bundles": presets.PRESET_BUNDLES,
                "presets": list(presets.PRESET_BUNDLES.keys()),
                "config": read_config_raw(),
                "merged": settings,
            })
            return

        if p == "/api/clips":
            settings = util.load_config()
            folder = resolve(settings.get("watch_folder", "./input"))
            self._send_json({"folder": str(folder), "clips": list_media(folder)})
            return

        if p == "/api/outputs":
            settings = util.load_config()
            folder = resolve(settings.get("output_folder", "./output"))
            self._send_json({"folder": str(folder), "outputs": list_media(folder)})
            return

        if p == "/api/run":
            with _run_lock:
                self._send_json({
                    "running": not RUN["done"],
                    "returncode": RUN["returncode"],
                    "started_at": RUN["started_at"],
                })
            return

        if p == "/api/logs":
            offset = int((parse_qs(u.query).get("offset", ["0"])[0]) or "0")
            with _run_lock:
                lines = RUN["lines"][offset:]
                total = len(RUN["lines"])
                done = RUN["done"]
            self._send_json({"lines": lines, "next_offset": total, "done": done})
            return

        if p.startswith("/media/output/"):
            settings = util.load_config()
            folder = resolve(settings.get("output_folder", "./output"))
            self._serve_file_range(folder / unquote(p[len("/media/output/"):]))
            return

        if p.startswith("/media/clip/"):
            settings = util.load_config()
            folder = resolve(settings.get("watch_folder", "./input"))
            self._serve_file_range(folder / unquote(p[len("/media/clip/"):]))
            return

        self._send_json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path

        if p == "/api/config":
            try:
                payload = json.loads(self._read_body() or b"{}")
                saved = write_config(payload)
                self._send_json({"ok": True, "config": saved, "merged": util.load_config()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            return

        if p == "/api/clips/upload":
            settings = util.load_config()
            folder = resolve(settings.get("watch_folder", "./input"))
            folder.mkdir(parents=True, exist_ok=True)
            name = unquote(self.headers.get("X-Filename", "clip.mp4"))
            name = os.path.basename(name) or "clip.mp4"
            data = self._read_body()
            (folder / name).write_bytes(data)
            self._send_json({"ok": True, "name": name, "size": len(data)})
            return

        if p == "/api/clips/delete":
            try:
                payload = json.loads(self._read_body() or b"{}")
                settings = util.load_config()
                folder = resolve(settings.get("watch_folder", "./input"))
                target = folder / os.path.basename(payload.get("name", ""))
                if target.exists():
                    target.unlink()
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            return

        if p == "/api/run":
            self._send_json(start_run())
            return

        if p == "/api/ideas":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception:
                payload = {}
            self._send_json(gen_ideas((payload.get("notes") or "").strip()))
            return

        self._send_json({"error": "not found"}, 404)


def main():
    util.ensure_dirs()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  Shelly video editor control panel running at {url}")
    print("  Press Ctrl C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
