# kAIm56 — celld (self-hosted Workers / Durable Objects) with esbuild, which celld
# needs to bundle Worker code and the upstream image does not carry.
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM node:22-alpine AS esbuild
RUN npm install --no-save --prefix /es esbuild@0.25.10 \
 && cp /es/node_modules/@esbuild/linux-x64/bin/esbuild /esbuild
FROM ghcr.io/denoland/celld:0.6.0
COPY --from=esbuild /esbuild /usr/local/bin/esbuild
ENV CELLD_ESBUILD=/usr/local/bin/esbuild
