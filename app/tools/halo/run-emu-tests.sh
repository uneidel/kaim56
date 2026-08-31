#!/usr/bin/env bash
# The device side against the vendor's Halo emulator. Python with lupa is not
# on the host, hence the container. The image is built on the first run (cached
# afterwards).
set -e
cd "$(dirname "$0")/../.."
docker build -q -t katagent-halo-emu - <<'DOCKER'
FROM python:3.12-slim
RUN pip install --no-cache-dir halo-emulator
ENV SDL_VIDEODRIVER=dummy
DOCKER
docker run --rm -v "$PWD:/w" -w /w katagent-halo-emu python tools/halo/emu_test.py
