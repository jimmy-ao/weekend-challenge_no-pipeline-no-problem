#!/usr/bin/env bash
# Builds the two Lambda packages into build/ (Terraform zips them).
# Uses uv: the "pipeline" dependency group (pyproject.toml, pinned in uv.lock) is installed as
# prebuilt manylinux wheels for Lambda's arm64 / Python 3.13, so this works from macOS or Linux
# without Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"

rm -rf "$BUILD"
mkdir -p "$BUILD/api" "$BUILD/pipeline"

# API: boto3 only (already in the Lambda runtime)
cp "$ROOT/backend/functions/api/"*.py "$BUILD/api/"
cp "$ROOT/backend/shared/formats.json" "$BUILD/api/"

# Pipeline: handlers + imaging + fonts + Pillow for linux/arm64
cp "$ROOT/backend/functions/pipeline/"*.py "$BUILD/pipeline/"
cp -R "$ROOT/backend/functions/pipeline/fonts" "$BUILD/pipeline/fonts"
cp "$ROOT/backend/shared/formats.json" "$BUILD/pipeline/"
uv export --project "$ROOT" --frozen --only-group pipeline --no-hashes --no-header --no-annotate \
  --format requirements-txt --output-file "$BUILD/pipeline-requirements.txt" --quiet
uv pip install \
  --quiet \
  --requirements "$BUILD/pipeline-requirements.txt" \
  --target "$BUILD/pipeline" \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --only-binary :all:
rm "$BUILD/pipeline-requirements.txt"

# Strip what Lambda does not need, and make the zip reproducible (stable hash = no redeploy).
find "$BUILD" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$BUILD" -type d -name "*.dist-info" -prune -exec rm -rf {} +
find "$BUILD" -exec touch -t 202601010000 {} +

echo "built: $(du -sh "$BUILD/api" | cut -f1) api, $(du -sh "$BUILD/pipeline" | cut -f1) pipeline"
