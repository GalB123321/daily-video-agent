#!/usr/bin/env python3
"""Render worker for the Meta Video admin tile.

Polls the Supabase video_jobs table for queued render jobs created by the
shelly admin tile, renders each one with the daily video pipeline, and stores
the finished portrait video back on the R2 bucket. The admin tile then streams
the result from meta-video/out/<jobId>.mp4.

One loop iteration does the following:
  1. Find the oldest video_jobs row with status queued.
  2. Claim it by patching status to rendering, but only while it is still
     queued, so two workers never grab the same job.
  3. Make temp input and out directories.
  4. Download every clip named in clip_keys from R2 into the input directory.
  5. Write a temp config.yaml from job.settings, pointing watch_folder at the
     input directory and output_folder at the out directory, with
     archive_processed off so source clips are left alone.
  6. Run the pipeline as a subprocess, run.py, with DVA_CONFIG set to that temp
     config so the pipeline reads it instead of the repo config.yaml.
  7. On success upload the produced mp4 to meta-video/out/<jobId>.mp4 and patch
     the job to done with the output key and a tail of the log.
  8. On any failure patch the job to error with the error text and log tail.

The loop never crashes on a single bad job, it logs the failure, marks the job
error, and moves on. Supabase is reached over plain urllib so the worker needs
no extra HTTP dependency. R2 is reached with boto3 pointed at the Cloudflare S3
endpoint.

No dash characters are used as prose punctuation in this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig


ROOT = Path(__file__).resolve().parent
RUN_PY = ROOT / "run.py"

POLL_SECONDS = 8.0
LOG_TAIL_CHARS = 8000


# A minimal .env loader so the worker can read credentials from a .env file
# without adding a dependency. Lines like KEY=value, comments and blanks are
# ignored. Existing environment variables always win.
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"missing required env var {name}", file=sys.stderr)
        sys.exit(1)
    return value


# Supabase REST helpers over urllib.

def _supabase_request(method: str, path_and_query: str, body=None, prefer=None):
    """Call the Supabase REST API. Returns parsed JSON, or None for empty body."""
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    url = f"{base}/rest/v1/{path_and_query}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url=url, data=data, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def fetch_next_queued_job():
    """Return the oldest queued job row, or None when the queue is empty."""
    query = (
        "video_jobs?status=eq.queued&order=created_at.asc&limit=1"
        "&select=id,settings,clip_keys,status,created_at"
    )
    rows = _supabase_request("GET", query)
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def claim_job(job_id: str) -> bool:
    """Patch the job to rendering only while still queued. True if we won it."""
    query = f"video_jobs?id=eq.{urllib.parse.quote(job_id)}&status=eq.queued"
    rows = _supabase_request(
        "PATCH",
        query,
        body={"status": "rendering"},
        prefer="return=representation",
    )
    return isinstance(rows, list) and len(rows) == 1


def patch_job(job_id: str, patch: dict) -> None:
    query = f"video_jobs?id=eq.{urllib.parse.quote(job_id)}"
    _supabase_request("PATCH", query, body=patch, prefer="return=minimal")


# R2 helpers via boto3 S3 client.

def make_r2_client():
    account = os.environ["R2_ACCOUNT_ID"]
    endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=BotoConfig(signature_version="s3v4"),
    )


def download_clip(s3, bucket: str, key: str, dest_dir: Path) -> Path:
    """Download one R2 object into dest_dir, keeping a sensible file name."""
    name = Path(key).name or "clip.mp4"
    dest = dest_dir / name
    # Guard against two clip keys ending in the same base name.
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{counter}_{name}"
        counter += 1
    s3.download_file(bucket, key, str(dest))
    return dest


def upload_output(s3, bucket: str, key: str, path: Path) -> None:
    s3.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={"ContentType": "video/mp4"},
    )


# Job rendering.

def write_job_config(settings: dict, input_dir: Path, out_dir: Path) -> Path:
    """Write a temp config.yaml from job settings for one render."""
    import yaml  # lazy, PyYAML is a pipeline dependency already

    config = dict(settings or {})
    config["watch_folder"] = str(input_dir)
    config["output_folder"] = str(out_dir)
    config["archive_processed"] = False

    fd, tmp_path = tempfile.mkstemp(prefix="dva_config_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, allow_unicode=True)
    return Path(tmp_path)


def find_output_mp4(out_dir: Path) -> Path | None:
    """Return the produced mp4 in the out dir, newest first, or None."""
    mp4s = sorted(
        out_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return mp4s[0] if mp4s else None


def run_pipeline(config_path: Path) -> subprocess.CompletedProcess:
    """Run run.py as a subprocess with DVA_CONFIG pointing at config_path."""
    env = dict(os.environ)
    env["DVA_CONFIG"] = str(config_path)
    return subprocess.run(
        [sys.executable, str(RUN_PY)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def render_job(s3, bucket: str, job: dict) -> None:
    """Render one claimed job end to end, patching its final status."""
    job_id = str(job["id"])
    settings = job.get("settings") or {}
    clip_keys = job.get("clip_keys") or []
    if isinstance(clip_keys, str):
        # clip_keys arrives as jsonb, but tolerate a string just in case.
        try:
            clip_keys = json.loads(clip_keys)
        except json.JSONDecodeError:
            clip_keys = []

    workdir = Path(tempfile.mkdtemp(prefix=f"dva_job_{job_id}_"))
    config_path: Path | None = None
    log_parts: list[str] = []
    try:
        input_dir = workdir / "input"
        out_dir = workdir / "out"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        if not clip_keys:
            raise RuntimeError("job has no clip_keys to render")

        for key in clip_keys:
            log_parts.append(f"download {key}")
            download_clip(s3, bucket, key, input_dir)

        config_path = write_job_config(settings, input_dir, out_dir)
        log_parts.append(f"config {config_path}")

        proc = run_pipeline(config_path)
        if proc.stdout:
            log_parts.append(proc.stdout)
        if proc.stderr:
            log_parts.append(proc.stderr)

        if proc.returncode != 0:
            raise RuntimeError(f"pipeline exited with code {proc.returncode}")

        produced = find_output_mp4(out_dir)
        if produced is None:
            raise RuntimeError("pipeline produced no mp4 in the out folder")

        output_key = f"meta-video/out/{job_id}.mp4"
        log_parts.append(f"upload {output_key}")
        upload_output(s3, bucket, output_key, produced)

        patch_job(job_id, {
            "status": "done",
            "output_key": output_key,
            "log": _tail("\n".join(log_parts)),
            "error": "",
        })
        print(f"job {job_id} done, output {output_key}")
    except Exception as exc:  # noqa: BLE001
        log_parts.append(traceback.format_exc())
        patch_job(job_id, {
            "status": "error",
            "error": str(exc),
            "log": _tail("\n".join(log_parts)),
        })
        print(f"job {job_id} error: {exc}", file=sys.stderr)
    finally:
        if config_path is not None:
            try:
                config_path.unlink()
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


def _tail(text: str) -> str:
    if len(text) <= LOG_TAIL_CHARS:
        return text
    return text[-LOG_TAIL_CHARS:]


# Main loop.

def main() -> int:
    load_dotenv(ROOT / ".env")
    for name in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
    ):
        require_env(name)

    bucket = os.environ["R2_BUCKET"]
    s3 = make_r2_client()

    print("render worker started, polling for queued jobs")
    while True:
        try:
            job = fetch_next_queued_job()
            if job is None:
                time.sleep(POLL_SECONDS)
                continue
            if not claim_job(str(job["id"])):
                # Another worker won this one, try again shortly.
                continue
            render_job(s3, bucket, job)
        except KeyboardInterrupt:
            print("render worker stopping")
            return 0
        except Exception as exc:  # noqa: BLE001
            # Never let one bad iteration kill the loop.
            print(f"loop error: {exc}", file=sys.stderr)
            traceback.print_exc()
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
