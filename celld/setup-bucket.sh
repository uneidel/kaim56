#!/usr/bin/env bash
# kAIm56 — celld in production mode: SeaweedFS as its S3 bucket, both on loopback.
# Usage: setup-bucket.sh FC_DIR CELLD_DIR   (install.sh --with-celld runs it)
#
# kaim56-s3     SeaweedFS (S3 API), reachable only on the docker network
#               kaim56-apps and on 127.0.0.1:8333; data in the volume kaim56-s3.
# kaim56-celld  celld --bucket on 127.0.0.1:9876; it runs what the manager
#               deploys (mgr/celldeploy.py: the Worker plus every app).
# FC_DIR/celld.env (0600) holds the bucket's keys; made once, kept after.
# Garage would be lighter but does not enforce conditional writes, which
# celld needs (two nodes could own one cell); SeaweedFS passes its probe.
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
FC_DIR=$1 CELLD_DIR=$2
S3_IMAGE=chrislusf/seaweedfs:3.97
NET=kaim56-apps
ENV="$FC_DIR/celld.env" S3CFG="$FC_DIR/celld-s3.json"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
if [ ! -f "$ENV" ]; then
  ( umask 077
    AK="GK$(openssl rand -hex 12)" SK="$(openssl rand -hex 32)"
    printf '{"identities":[{"name":"celld","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' "$AK" "$SK" > "$S3CFG"
    printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\nAWS_REGION=us-east-1\nCELLD_BUCKET=s3://celld\nS3_ENDPOINT=http://kaim56-s3:8333\nS3_HOST_ENDPOINT=http://127.0.0.1:8333\nCELLD_DURABILITY=bucket\nCELLD_WATCH=/state\n' "$AK" "$SK" > "$ENV" )
fi

docker rm -f kaim56-s3 >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name kaim56-s3 --network "$NET" -p 127.0.0.1:8333:8333 \
  -v "$S3CFG":/etc/celld-s3.json:ro -v kaim56-s3:/data \
  "$S3_IMAGE" server -dir=/data -s3 -s3.port=8333 -s3.config=/etc/celld-s3.json >/dev/null

# the bucket, through the manager's own code (SigV4, stdlib)
for i in $(seq 30); do
  python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from mgr import celldeploy as c; c.ensure_bucket(c.load_env())" "$FC_DIR" 2>/dev/null && break
  [ "$i" = 30 ] && { echo "  S3 did not come up — docker logs kaim56-s3" >&2; exit 1; }
  sleep 2
done

( cd "$CELLD_DIR" && docker build -q -t kaim56-celld . >/dev/null )
docker rm -f kaim56-celld >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name kaim56-celld --network "$NET" -p 127.0.0.1:9876:9876 \
  --env-file "$ENV" -v celld-state:/state \
  kaim56-celld --listen 0.0.0.0:9876 --internal-listen 127.0.0.1:9877 >/dev/null
echo "  celld (bucket on SeaweedFS) on 127.0.0.1:9876 — the manager deploys the apps to it"
