"""Cropaganda HTTP API (API Gateway HTTP API, payload v2, Cognito JWT authorizer).

Routes
  GET    /formats
  GET    /events                  POST /events
  PUT    /events/{eventId}        DELETE /events/{eventId}
  POST   /events/{eventId}/logo   -> presigned POST for the event logo, or for its custom banner
                                     (body {"kind": "banner"}): a user-made strip that replaces the generated banner,
                                     or for a full-frame overlay (body {"kind": "frame", "shape": "story"})
  GET    /jobs                    POST /jobs  -> creates a job + presigned POST for the photo
  GET    /jobs/{jobId}            DELETE /jobs/{jobId}

Events are shared by every signed-in producer; jobs are private to their owner.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

REGION = os.environ.get("AWS_REGION", "eu-north-1")
BUCKET = os.environ["MEDIA_BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
JOBS_BY_OWNER_INDEX = os.environ.get("JOBS_BY_OWNER_INDEX", "byOwner")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_LOGO_BYTES = int(os.environ.get("MAX_LOGO_BYTES", str(2 * 1024 * 1024)))
MAX_BANNER_BYTES = int(os.environ.get("MAX_BANNER_BYTES", str(5 * 1024 * 1024)))
MAX_FRAME_BYTES = int(os.environ.get("MAX_FRAME_BYTES", str(8 * 1024 * 1024)))
FRAME_SHAPES = ("square", "portrait", "story", "landscape")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
URL_TTL = 3600

s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
ddb = boto3.resource("dynamodb")
jobs = ddb.Table(JOBS_TABLE)
events = ddb.Table(EVENTS_TABLE)


def _load_formats() -> list[dict]:
    here = os.path.dirname(__file__)
    for path in (os.path.join(here, "formats.json"), os.path.join(here, "..", "..", "shared", "formats.json")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)["formats"]
    raise RuntimeError("formats.json not found")


FORMATS = _load_formats()
FORMAT_IDS = {f["id"] for f in FORMATS}
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
HASHTAG = re.compile(r"^#?[\w]{1,40}$", re.UNICODE)
UUID = re.compile(r"^[0-9a-f]{32}$")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- helpers
def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value))


def respond(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=_json_default),
    }


def claims(event) -> dict:
    return event["requestContext"]["authorizer"]["jwt"]["claims"]


def body_of(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HttpError(400, "body must be JSON") from exc
    if not isinstance(data, dict):
        raise HttpError(400, "body must be a JSON object")
    return data


def text(data: dict, field: str, max_len: int, required: bool = False) -> str:
    value = data.get(field, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise HttpError(400, f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise HttpError(400, f"{field} is required")
    if len(value) > max_len:
        raise HttpError(400, f"{field} is longer than {max_len} characters")
    return value


def presign_get(key: str) -> str:
    return s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=URL_TTL)


def check_id(value: str) -> str:
    if not UUID.match(value or ""):
        raise HttpError(404, "not found")
    return value


# --------------------------------------------------------------------------- events
def validate_event(data: dict, event_id: str) -> dict:
    item = {
        "name": text(data, "name", 80, required=True),
        "dateLabel": text(data, "dateLabel", 40),
        "location": text(data, "location", 60),
        "hashtag": text(data, "hashtag", 41),
        "position": text(data, "position", 6) or "bottom",
    }
    if item["hashtag"] and not HASHTAG.match(item["hashtag"]):
        raise HttpError(400, "hashtag may only contain letters, digits and _")
    if item["position"] not in ("top", "bottom"):
        raise HttpError(400, "position must be top or bottom")
    for field, default in (("primaryColor", "#111827"), ("accentColor", "#F59E0B"), ("textColor", "#FFFFFF")):
        value = text(data, field, 7) or default
        if not HEX.match(value):
            raise HttpError(400, f"{field} must look like #RRGGBB")
        item[field] = value.upper()
    # Image keys: omitted on update = keep the current file, "" = remove it.
    for field, prefix in (("logoKey", f"logos/{event_id}/"), ("bannerKey", f"logos/{event_id}/banner-")):
        if field in data:
            key = text(data, field, 200)
            if key and not key.startswith(prefix):
                raise HttpError(400, f"{field} does not belong to this event")
            item[field] = key
    # Full-frame overlays, one optional image per shape. Omitted = keep, {} = remove them all.
    if "frameKeys" in data:
        frames = data["frameKeys"] or {}
        if not isinstance(frames, dict) or not set(frames) <= set(FRAME_SHAPES):
            raise HttpError(400, f"frameKeys must map {', '.join(FRAME_SHAPES)} to uploaded files")
        item["frameKeys"] = {}
        for shape, key in frames.items():
            if not isinstance(key, str) or not key.startswith(f"logos/{event_id}/frame-{shape}-") or len(key) > 200:
                raise HttpError(400, f"frameKeys.{shape} does not belong to this event")
            item["frameKeys"][shape] = key
    return item


def public_event(item: dict) -> dict:
    out = dict(item)
    for kind in ("logo", "banner"):
        key = item.get(f"{kind}Key", "")
        out[f"{kind}Key"] = key
        out[f"{kind}Url"] = presign_get(key) if key else None
    out["frameKeys"] = dict(item.get("frameKeys") or {})
    out["frameUrls"] = {shape: presign_get(key) for shape, key in out["frameKeys"].items()}
    return out


def list_events(_event, _params):
    items, kwargs = [], {}
    while True:
        page = events.scan(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda i: i.get("createdAt", 0), reverse=True)
    return 200, {"events": [public_event(i) for i in items]}


def create_event(event, _params):
    event_id = uuid.uuid4().hex
    item = {"logoKey": "", "bannerKey": "", **validate_event(body_of(event), event_id)}
    item.update(
        eventId=event_id,
        createdAt=int(time.time()),
        createdBy=claims(event).get("email", claims(event)["sub"]),
    )
    events.put_item(Item=item)
    return 201, public_event(item)


def update_event(event, params):
    event_id = check_id(params["eventId"])
    current = events.get_item(Key={"eventId": event_id}).get("Item")
    if not current:
        raise HttpError(404, "event not found")
    item = {**current, **validate_event(body_of(event), event_id), "updatedAt": int(time.time())}
    events.put_item(Item=item)
    for field in ("logoKey", "bannerKey"):
        old = current.get(field)
        if old and old != item.get(field):
            s3.delete_object(Bucket=BUCKET, Key=old)
    kept = set((item.get("frameKeys") or {}).values())
    for old in (current.get("frameKeys") or {}).values():
        if old not in kept:
            s3.delete_object(Bucket=BUCKET, Key=old)
    return 200, public_event(item)


def delete_event(_event, params):
    event_id = check_id(params["eventId"])
    current = events.get_item(Key={"eventId": event_id}).get("Item")
    if not current:
        raise HttpError(404, "event not found")
    _delete_prefix(f"logos/{event_id}/")
    events.delete_item(Key={"eventId": event_id})
    return 204, None


def logo_upload(event, params):
    event_id = check_id(params["eventId"])
    if not events.get_item(Key={"eventId": event_id}).get("Item"):
        raise HttpError(404, "event not found")
    data = body_of(event)
    kind = text(data, "kind", 10) or "logo"
    if kind not in ("logo", "banner", "frame"):
        raise HttpError(400, "kind must be logo, banner or frame")
    content_type = text(data, "contentType", 40)
    if content_type not in LOGO_TYPES:
        raise HttpError(400, f"{kind} must be PNG, JPEG or WebP")
    prefix, max_bytes = "", MAX_LOGO_BYTES
    if kind == "banner":
        prefix, max_bytes = "banner-", MAX_BANNER_BYTES
    elif kind == "frame":
        shape = text(data, "shape", 10)
        if shape not in FRAME_SHAPES:
            raise HttpError(400, f"shape must be one of {', '.join(FRAME_SHAPES)}")
        prefix, max_bytes = f"frame-{shape}-", MAX_FRAME_BYTES
    key = f"logos/{event_id}/{prefix}{uuid.uuid4().hex}.{LOGO_TYPES[content_type]}"
    post = s3.generate_presigned_post(
        BUCKET,
        key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
        ExpiresIn=300,
    )
    return 200, {"key": key, "upload": post}


# --------------------------------------------------------------------------- jobs
def public_job(item: dict, with_urls: bool) -> dict:
    out = {k: item.get(k) for k in ("jobId", "eventId", "eventName", "status", "formats", "originalName", "createdAt", "completedAt", "error")}
    out["focusX"], out["focusY"] = item.get("focusX"), item.get("focusY")
    outputs = item.get("outputs") or []
    if with_urls and item.get("status") == "DONE":
        out["outputs"] = [{**o, "url": presign_get(o["key"])} for o in outputs]
        out["zipUrl"] = presign_get(item["zipKey"])
    elif item.get("status") == "DONE" and outputs:
        out["thumbnailUrl"] = presign_get(outputs[0]["key"])
    return out


def create_job(event, _params):
    data = body_of(event)
    owner = claims(event)["sub"]

    content_type = text(data, "contentType", 40)
    if content_type not in PHOTO_TYPES:
        raise HttpError(400, "photo must be JPEG, PNG or WebP")
    size = data.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_UPLOAD_BYTES:
        raise HttpError(400, f"photo must be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    formats = data.get("formats") or sorted(FORMAT_IDS)
    if not isinstance(formats, list) or not formats or not set(formats) <= FORMAT_IDS:
        raise HttpError(400, "unknown format in formats")

    focus = []
    for field in ("focusX", "focusY"):
        value = data.get(field, 0.5)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise HttpError(400, f"{field} must be between 0 and 1")
        focus.append(Decimal(str(round(float(value), 4))))

    event_id = text(data, "eventId", 32)
    event_name = ""
    if event_id:
        ev = events.get_item(Key={"eventId": check_id(event_id)}).get("Item")
        if not ev:
            raise HttpError(400, "event not found")
        event_name = ev["name"]

    job_id = uuid.uuid4().hex
    key = f"uploads/{job_id}/original"
    now = int(time.time())
    item = {
        "jobId": job_id,
        "owner": owner,
        "status": "AWAITING_UPLOAD",
        "eventId": event_id,
        "eventName": event_name,
        "formats": [f["id"] for f in FORMATS if f["id"] in formats],
        "focusX": focus[0],
        "focusY": focus[1],
        "originalKey": key,
        "originalName": text(data, "fileName", 200),
        "createdAt": now,
        "expiresAt": now + RETENTION_DAYS * 86400,
    }
    jobs.put_item(Item=item)
    post = s3.generate_presigned_post(
        BUCKET,
        key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_UPLOAD_BYTES]],
        ExpiresIn=300,
    )
    return 201, {**public_job(item, False), "upload": post}


def _own_job(event, params) -> dict:
    job_id = check_id(params["jobId"])
    item = jobs.get_item(Key={"jobId": job_id}).get("Item")
    if not item or item["owner"] != claims(event)["sub"]:
        raise HttpError(404, "job not found")
    return item


def get_job(event, params):
    return 200, public_job(_own_job(event, params), True)


def list_jobs(event, _params):
    resp = jobs.query(
        IndexName=JOBS_BY_OWNER_INDEX,
        KeyConditionExpression=Key("owner").eq(claims(event)["sub"]),
        ScanIndexForward=False,
        Limit=30,
    )
    return 200, {"jobs": [public_job(i, False) for i in resp["Items"] if i["status"] != "AWAITING_UPLOAD" or i["createdAt"] > time.time() - 900]}


def delete_job(event, params):
    item = _own_job(event, params)
    if item["status"] == "PROCESSING":
        raise HttpError(409, "job is still processing")
    _delete_prefix(f"uploads/{item['jobId']}/")
    _delete_prefix(f"outputs/{item['jobId']}/")
    jobs.delete_item(Key={"jobId": item["jobId"]})
    return 204, None


def _delete_prefix(prefix: str) -> None:
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys, "Quiet": True})


# --------------------------------------------------------------------------- router
ROUTES = {
    "GET /formats": lambda e, p: (200, {"formats": FORMATS, "maxUploadBytes": MAX_UPLOAD_BYTES, "retentionDays": RETENTION_DAYS}),
    "GET /events": list_events,
    "POST /events": create_event,
    "PUT /events/{eventId}": update_event,
    "DELETE /events/{eventId}": delete_event,
    "POST /events/{eventId}/logo": logo_upload,
    "GET /jobs": list_jobs,
    "POST /jobs": create_job,
    "GET /jobs/{jobId}": get_job,
    "DELETE /jobs/{jobId}": delete_job,
}


def handler(event, _context):
    route = event.get("routeKey", "")
    fn = ROUTES.get(route)
    if fn is None:
        return respond(404, {"message": "not found"})
    try:
        status, body = fn(event, event.get("pathParameters") or {})
    except HttpError as exc:
        return respond(exc.status, {"message": exc.message})
    except Exception:
        log.exception("unhandled error on %s", route)
        return respond(500, {"message": "internal error"})
    if status == 204:
        return {"statusCode": 204, "headers": {"cache-control": "no-store"}}
    return respond(status, body)
