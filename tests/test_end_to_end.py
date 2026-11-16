"""Runs the API and the pipeline handlers against moto (no AWS account needed).

    pip install -r requirements-dev.txt && pytest
"""

import importlib
import io
import json
import os
import sys
import zipfile

import boto3
import pytest
from moto import mock_aws
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend", "functions", "pipeline"))
sys.path.insert(0, os.path.join(ROOT, "backend", "functions", "api"))

REGION = "eu-north-1"
BUCKET = "cropaganda-test-media"


@pytest.fixture()
def aws(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "MEDIA_BUCKET": BUCKET,
        "JOBS_TABLE": "jobs",
        "EVENTS_TABLE": "events",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName="events",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "eventId", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "eventId", "KeyType": "HASH"}],
        )
        ddb.create_table(
            TableName="jobs",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "jobId", "AttributeType": "S"},
                {"AttributeName": "owner", "AttributeType": "S"},
                {"AttributeName": "createdAt", "AttributeType": "N"},
            ],
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "byOwner",
                    "KeySchema": [
                        {"AttributeName": "owner", "KeyType": "HASH"},
                        {"AttributeName": "createdAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        import app
        import handlers

        yield s3, importlib.reload(app), importlib.reload(handlers)


def call(app, route, body=None, params=None, sub="user-1"):
    resp = app.handler(
        {
            "routeKey": route,
            "pathParameters": params or {},
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub, "email": f"{sub}@example.com"}}}},
        },
        None,
    )
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


def photo_bytes(size=(3000, 2000)) -> bytes:
    img = Image.new("RGB", size, (40, 80, 160))
    ImageDraw.Draw(img).ellipse((1800, 500, 2400, 1100), fill=(250, 210, 180))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_full_flow(aws):
    s3, app, handlers = aws

    status, ev = call(app, "POST /events", {"name": "AWS Community Day", "dateLabel": "14 Nov 2026", "hashtag": "acd", "accentColor": "#ff9900"})
    assert status == 201 and ev["accentColor"] == "#FF9900"

    status, logo = call(app, "POST /events/{eventId}/logo", {"contentType": "image/png"}, {"eventId": ev["eventId"]})
    assert status == 200 and logo["key"].startswith(f"logos/{ev['eventId']}/")
    buf = io.BytesIO()
    Image.new("RGBA", (200, 100), (255, 153, 0, 255)).save(buf, "PNG")
    s3.put_object(Bucket=BUCKET, Key=logo["key"], Body=buf.getvalue())
    status, ev = call(app, "PUT /events/{eventId}", {**ev, "logoKey": logo["key"]}, {"eventId": ev["eventId"]})
    assert status == 200 and ev["logoUrl"]

    status, job = call(app, "POST /jobs", {"eventId": ev["eventId"], "contentType": "image/jpeg", "size": 1000, "formats": ["story", "linkedin"], "focusX": 0.7, "focusY": 0.3, "fileName": "a.jpg"})
    assert status == 201 and job["status"] == "AWAITING_UPLOAD"
    assert job["upload"]["fields"]["key"] == f"uploads/{job['jobId']}/original"

    key = job["upload"]["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=photo_bytes())

    prepared = handlers.prepare({"jobId": job["jobId"], "key": key, "bucket": BUCKET}, None)
    assert prepared["formats"] == ["story", "linkedin"] and prepared["focusX"] == 0.7
    with pytest.raises(handlers.DuplicateUpload):
        handlers.prepare({"jobId": job["jobId"], "key": key}, None)

    outputs = [handlers.render({"job": prepared, "format": f}, None) for f in prepared["formats"]]
    for out in outputs:
        img = Image.open(io.BytesIO(s3.get_object(Bucket=BUCKET, Key=out["key"])["Body"].read()))
        assert img.size == (out["width"], out["height"])

    result = handlers.package({"job": prepared, "outputs": outputs}, None)
    zf = zipfile.ZipFile(io.BytesIO(s3.get_object(Bucket=BUCKET, Key=result["zipKey"])["Body"].read()))
    assert sorted(zf.namelist()) == ["linkedin-1200x627.jpg", "story-1080x1920.jpg"]

    status, done = call(app, "GET /jobs/{jobId}", params={"jobId": job["jobId"]})
    assert status == 200 and done["status"] == "DONE" and len(done["outputs"]) == 2 and done["zipUrl"]

    status, _ = call(app, "GET /jobs/{jobId}", params={"jobId": job["jobId"]}, sub="someone-else")
    assert status == 404

    status, listing = call(app, "GET /jobs")
    assert status == 200 and listing["jobs"][0]["thumbnailUrl"]

    assert call(app, "DELETE /jobs/{jobId}", params={"jobId": job["jobId"]})[0] == 204
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"outputs/{job['jobId']}/")


def test_validation(aws):
    _, app, handlers = aws
    assert call(app, "POST /events", {"name": ""})[0] == 400
    assert call(app, "POST /events", {"name": "x", "primaryColor": "red"})[0] == 400
    assert call(app, "POST /events", {"name": "x", "hashtag": "no spaces"})[0] == 400
    assert call(app, "POST /jobs", {"contentType": "image/gif", "size": 10})[0] == 400
    assert call(app, "POST /jobs", {"contentType": "image/jpeg", "size": 10**9})[0] == 400
    assert call(app, "POST /jobs", {"contentType": "image/jpeg", "size": 10, "formats": ["nope"]})[0] == 400
    assert call(app, "POST /jobs", {"contentType": "image/jpeg", "size": 10, "focusX": 2})[0] == 400
    assert call(app, "GET /jobs/{jobId}", params={"jobId": "../etc"})[0] == 404


def test_corrupt_upload_is_rejected(aws):
    s3, app, handlers = aws
    _, job = call(app, "POST /jobs", {"contentType": "image/jpeg", "size": 10})
    key = job["upload"]["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"not an image")
    with pytest.raises(handlers.InvalidUpload):
        handlers.prepare({"jobId": job["jobId"], "key": key}, None)


def test_overlay_without_event_renders_plain_crop(aws):
    _, _, handlers = aws
    import imaging

    photo = imaging.load_photo(photo_bytes((800, 600)))
    for fmt in handlers.FORMATS.values():
        data = imaging.render(photo, fmt, None)
        assert Image.open(io.BytesIO(data)).size == (fmt["width"], fmt["height"])


def test_custom_banner_replaces_generated_banner(aws):
    s3, app, handlers = aws
    import imaging

    _, ev = call(app, "POST /events", {"name": "Own banner event", "position": "bottom"})
    assert ev["bannerKey"] == "" and ev["bannerUrl"] is None

    assert call(app, "POST /events/{eventId}/logo", {"contentType": "image/png", "kind": "frame"}, {"eventId": ev["eventId"]})[0] == 400
    status, up = call(app, "POST /events/{eventId}/logo", {"contentType": "image/png", "kind": "banner"}, {"eventId": ev["eventId"]})
    assert status == 200 and up["key"].startswith(f"logos/{ev['eventId']}/banner-")

    # A logo key cannot be passed off as a banner, and keys of other events are refused.
    assert call(app, "PUT /events/{eventId}", {**ev, "bannerKey": f"logos/{ev['eventId']}/x.png"}, {"eventId": ev["eventId"]})[0] == 400
    assert call(app, "PUT /events/{eventId}", {**ev, "bannerKey": "logos/" + "0" * 32 + "/banner-x.png"}, {"eventId": ev["eventId"]})[0] == 400

    banner = Image.new("RGBA", (2000, 250), (255, 0, 0, 255))
    buf = io.BytesIO()
    banner.save(buf, "PNG")
    s3.put_object(Bucket=BUCKET, Key=up["key"], Body=buf.getvalue())
    status, ev = call(app, "PUT /events/{eventId}", {**ev, "bannerKey": up["key"]}, {"eventId": ev["eventId"]})
    assert status == 200 and ev["bannerUrl"]

    _, job = call(app, "POST /jobs", {"eventId": ev["eventId"], "contentType": "image/jpeg", "size": 1000, "formats": ["instagram-square", "story"]})
    key = job["upload"]["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=photo_bytes())
    prepared = handlers.prepare({"jobId": job["jobId"], "key": key}, None)
    assert prepared["event"]["bannerKey"] == up["key"]

    for fmt_id in prepared["formats"]:
        out = handlers.render({"job": prepared, "format": fmt_id}, None)
        img = Image.open(io.BytesIO(s3.get_object(Bucket=BUCKET, Key=out["key"])["Body"].read())).convert("RGB")
        fmt = handlers.FORMATS[fmt_id]
        x, y, tw, th = imaging.banner_box(fmt["width"], fmt["height"], 2000, 250, "bottom", fmt["safeTop"], fmt["safeBottom"])
        assert tw == fmt["width"] and th == round(250 * fmt["width"] / 2000)
        r, g, b = img.getpixel((fmt["width"] // 2, y + th // 2))
        assert r > 200 and g < 60 and b < 60  # the red banner, not the generated dark panel
        r, g, b = img.getpixel((fmt["width"] // 2, max(y - 20, 0)))
        assert not (r > 200 and g < 60)  # photo above the banner

    # Removing the banner deletes the file and brings back the generated banner.
    status, ev = call(app, "PUT /events/{eventId}", {**ev, "bannerKey": ""}, {"eventId": ev["eventId"]})
    assert status == 200 and ev["bannerUrl"] is None
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"logos/{ev['eventId']}/banner-")


def test_banner_box_geometry():
    import imaging

    # Wide strip: full width, against the bottom edge, or below the story's top UI zone.
    assert imaging.banner_box(1080, 1080, 2000, 250, "bottom") == (0, 945, 1080, 135)
    assert imaging.banner_box(1080, 1920, 2000, 250, "top", 0.13, 0.18) == (0, 250, 1080, 135)
    assert imaging.banner_box(1080, 1920, 2000, 250, "bottom", 0.13, 0.18) == (0, 1574 - 135, 1080, 135)
    # Too tall for the output: capped at 35 % of the height and centred.
    x, y, tw, th = imaging.banner_box(1200, 627, 1000, 1000, "bottom")
    assert th == round(627 * imaging.BANNER_MAX_HEIGHT) and tw == th and x == (1200 - tw) // 2 and y == 627 - th


def _frame_png(size, colour) -> bytes:
    """Opaque border in `colour`, transparent window in the middle."""
    w, h = size
    img = Image.new("RGBA", size, colour + (255,))
    ImageDraw.Draw(img).rectangle((w // 10, h // 10, w - w // 10, h - h // 10), fill=(0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_full_frame_overlay(aws):
    s3, app, handlers = aws

    _, ev = call(app, "POST /events", {"name": "re:Invent"})
    assert ev["frameKeys"] == {} and ev["frameUrls"] == {}
    params = {"eventId": ev["eventId"]}
    assert call(app, "POST /events/{eventId}/logo", {"contentType": "image/png", "kind": "frame", "shape": "huge"}, params)[0] == 400

    keys = {}
    for shape, size, colour in (("portrait", (1086, 1448), (200, 0, 200)), ("landscape", (1672, 941), (0, 200, 0))):
        status, up = call(app, "POST /events/{eventId}/logo", {"contentType": "image/png", "kind": "frame", "shape": shape}, params)
        assert status == 200 and up["key"].startswith(f"logos/{ev['eventId']}/frame-{shape}-")
        s3.put_object(Bucket=BUCKET, Key=up["key"], Body=_frame_png(size, colour))
        keys[shape] = up["key"]

    # A portrait file cannot be filed under another shape.
    assert call(app, "PUT /events/{eventId}", {**ev, "frameKeys": {"story": keys["portrait"]}}, params)[0] == 400
    status, ev = call(app, "PUT /events/{eventId}", {**ev, "frameKeys": keys}, params)
    assert status == 200 and set(ev["frameUrls"]) == {"portrait", "landscape"}

    _, job = call(app, "POST /jobs", {"eventId": ev["eventId"], "contentType": "image/jpeg", "size": 1000})
    key = job["upload"]["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=photo_bytes())
    prepared = handlers.prepare({"jobId": job["jobId"], "key": key}, None)

    # Square and story have no frame of their own: they borrow portrait (nearest ratio).
    expected = {"instagram-square": (200, 0, 200), "instagram-portrait": (200, 0, 200), "story": (200, 0, 200)}
    for fmt_id in prepared["formats"]:
        out = handlers.render({"job": prepared, "format": fmt_id}, None)
        img = Image.open(io.BytesIO(s3.get_object(Bucket=BUCKET, Key=out["key"])["Body"].read())).convert("RGB")
        w, h = img.size
        border = img.getpixel((w // 40, h // 2))
        want = expected.get(fmt_id, (0, 200, 0))
        assert all(abs(a - b) < 40 for a, b in zip(border, want)), (fmt_id, border)
        centre = img.getpixel((w // 2, h // 2))
        assert sum(abs(a - b) for a, b in zip(centre, want)) > 150, (fmt_id, centre)  # the photo shows through

    # Dropping one frame deletes its file and keeps the other.
    status, ev = call(app, "PUT /events/{eventId}", {**ev, "frameKeys": {"landscape": keys["landscape"]}}, params)
    assert status == 200 and list(ev["frameKeys"]) == ["landscape"]
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=keys["portrait"])


def test_frame_choice():
    import imaging

    fmt = lambda w, h, slot: {"width": w, "height": h, "frame": slot}
    frames = {"portrait": "P", "landscape": "L"}
    assert imaging.frame_key_for(fmt(1080, 1350, "portrait"), frames) == "P"
    assert imaging.frame_key_for(fmt(1080, 1080, "square"), frames) == "P"  # 1.0 is closer to 0.8 than to 1.78
    assert imaging.frame_key_for(fmt(1080, 1920, "story"), frames) == "P"
    assert imaging.frame_key_for(fmt(1200, 627, "landscape"), frames) == "L"
    assert imaging.frame_key_for(fmt(1200, 627, "landscape"), {}) is None
