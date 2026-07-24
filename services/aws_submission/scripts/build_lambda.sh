#!/usr/bin/env bash
set -euo pipefail

service_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${service_dir}/build/lambda"
dist_dir="${service_dir}/dist"

rm -rf "${build_dir}"
mkdir -p "${build_dir}" "${dist_dir}"

uv pip install \
  --target "${build_dir}" \
  --python-platform x86_64-manylinux2014 \
  --python-version 3.12 \
  "${service_dir}"

find "${build_dir}" -type d -name __pycache__ -prune -exec rm -rf {} +
find "${build_dir}" -type f -name '*.pyc' -delete
