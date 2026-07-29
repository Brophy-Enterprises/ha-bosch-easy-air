#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT_DIR}/build/firefox/easyair-auth-helper"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

rsync -a \
  --exclude "build/" \
  --exclude "build-chrome.sh" \
  --exclude "build-firefox.sh" \
  --exclude "manifest.chrome.json" \
  --exclude "manifest.firefox.json" \
  "${ROOT_DIR}/" \
  "${BUILD_DIR}/"

cp "${ROOT_DIR}/manifest.firefox.json" "${BUILD_DIR}/manifest.json"

echo "Built ${BUILD_DIR}"
echo "Load this directory from about:debugging#/runtime/this-firefox:"
echo "  ${BUILD_DIR}"
