"""Step Functions task handlers for the Cropaganda pipeline.

One deployment package, three Lambda functions:
  prepare  -> validates the upload, locks the job (AWAITING_UPLOAD -> PROCESSING), returns the work list
  render   -> one social-network format per invocation (run in parallel by a Map state)
  package  -> zips every rendered file and marks the job DONE
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

import imaging

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

BUCKET = os.environ["MEDIA_BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")
jobs = ddb.Table(JOBS_TABLE)
events = ddb.Table(EVENTS_TABLE)


def _formats() -> dict[str, dict]:
    here = os.path.dirname(__file__)
    for path in (os.path.join(here, "formats.json"), os.path.join(here, "..", "..", "shared", "formats.json")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return {f["id"]: f for f in json.load(fh)["formats"]}
    raise RuntimeError("formats.json not found")


FORMATS = _formats()


class InvalidUpload(Exception):
    """The uploaded object is not a usable photo (not retried by Step Functions)."""


class DuplicateUpload(Exception):
    """The job already left AWAITING_UPLOAD: another execution owns it, leave it alone."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "photos"


def _decimal_to_float(value) -> float:
    return float(value) if value is not None else 0.5


# --------------------------------------------------------------------------- prepare
def prepare(event, _context):
    job_id = event["jobId"]
    key = event["key"]
    log.info("prepare job=%s key=%s", job_id, key)

    job = jobs.get_item(Key={"jobId": job_id}).get("Item")
    if not job:
        raise InvalidUpload(f"no job {job_id} for object {key}")
    if key != job["originalKey"]:
        raise InvalidUpload(f"unexpected object key {key}")

    head = s3.head_object(Bucket=BUCKET, Key=key)
    if head["ContentLength"] > MAX_UPLOAD_BYTES:
        raise InvalidUpload("photo is larger than the upload limit")

    # Lock the job: a second upload with the same presigned form must not start a second run.
    try:
        jobs.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :processing, startedAt = :now",
            ConditionExpression="#s = :waiting",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":processing": "PROCESSING", ":waiting": "AWAITING_UPLOAD", ":now": int(time.time())},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise DuplicateUpload(f"job {job_id} is not waiting for an upload") from exc
        raise

    # Decode once here so a corrupt file fails fast, before fanning out.
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    try:
        photo = imaging.load_photo(body)
    except Exception as exc:  # Pillow raises many types for bad input
        raise InvalidUpload(f"cannot read the photo: {exc}") from exc

    ev = events.get_item(Key={"eventId": job["eventId"]}).get("Item") if job.get("eventId") else None
    formats = [f for f in job.get("formats", []) if f in FORMATS] or list(FORMATS)

    return {
        "jobId": job_id,
        "key": key,
        "width": photo.width,
        "height": photo.height,
        "focusX": _decimal_to_float(job.get("focusX")),
        "focusY": _decimal_to_float(job.get("focusY")),
        "event": _event_for_render(ev),
        "zipName": f"cropaganda-{_slug(ev['name'] if ev else 'photos')}-{job_id[:8]}.zip",
        "formats": formats,
    }


def _event_for_render(ev: dict | None) -> dict | None:
    if not ev:
        return None
    keep = ("name", "dateLabel", "location", "hashtag", "primaryColor", "accentColor", "textColor", "position", "logoKey", "bannerKey", "frameKeys")
    return {k: ev[k] for k in keep if ev.get(k)}


# --------------------------------------------------------------------------- render
def _load_image(key: str | None):
    """Event logo or custom banner as RGBA, or None. A broken file must not cost the user their photos:
    a missing logo is simply left out, a missing banner falls back to the generated one."""
    if not key:
        return None
    try:
        return imaging.load_logo(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        log.warning("could not load event image %s", key, exc_info=True)
        return None


def render(event, _context):
    job = event["job"]
    fmt = FORMATS[event["format"]]
    log.info("render job=%s format=%s", job["jobId"], fmt["id"])

    body = s3.get_object(Bucket=BUCKET, Key=job["key"])["Body"].read()
    photo = imaging.load_photo(body)

    overlay = None
    ev = job.get("event")
    if ev:
        frame_key = imaging.frame_key_for(fmt, ev.get("frameKeys") or {})
        overlay = imaging.Overlay.from_event(
            ev, _load_image(ev.get("logoKey")), _load_image(ev.get("bannerKey")), _load_image(frame_key)
        )

    data = imaging.render(photo, fmt, overlay, job["focusX"], job["focusY"])
    out_key = f"outputs/{job['jobId']}/{fmt['id']}.jpg"
    s3.put_object(
        Bucket=BUCKET,
        Key=out_key,
        Body=data,
        ContentType="image/jpeg",
        ContentDisposition=f'attachment; filename="{fmt["id"]}-{fmt["width"]}x{fmt["height"]}.jpg"',
    )
    return {"format": fmt["id"], "key": out_key, "width": fmt["width"], "height": fmt["height"], "bytes": len(data)}


# --------------------------------------------------------------------------- package
def package(event, _context):
    job = event["job"]
    outputs = event["outputs"]
    job_id = job["jobId"]
    log.info("package job=%s files=%d", job_id, len(outputs))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # JPEGs do not compress further
        for out in outputs:
            data = s3.get_object(Bucket=BUCKET, Key=out["key"])["Body"].read()
            zf.writestr(f"{out['format']}-{out['width']}x{out['height']}.jpg", data)
    zip_key = f"outputs/{job_id}/{job['zipName']}"
    s3.put_object(
        Bucket=BUCKET,
        Key=zip_key,
        Body=buf.getvalue(),
        ContentType="application/zip",
        ContentDisposition=f'attachment; filename="{job["zipName"]}"',
    )

    jobs.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #s = :done, outputs = :outputs, zipKey = :zip, completedAt = :now, sourceWidth = :w, sourceHeight = :h",
        ConditionExpression="#s = :processing",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":done": "DONE",
            ":processing": "PROCESSING",
            ":outputs": [{k: out[k] for k in ("format", "key", "width", "height", "bytes")} for out in outputs],
            ":zip": zip_key,
            ":now": int(time.time()),
            ":w": job["width"],
            ":h": job["height"],
        },
    )
    return {"jobId": job_id, "status": "DONE", "zipKey": zip_key}
