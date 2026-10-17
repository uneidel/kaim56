# kaim56-apps on celld

The browser apps (`../apps/<name>/`) hosted by [celld](https://github.com/denoland/celld),
a self-hosted runtime for Cloudflare Workers / Durable Objects.

- **Hosting:** the manager deploys the Worker (`worker.js`, `do/`) and every app under `apps/`
  (as `public/apps`) to an S3 bucket with `celld deploy` (`mgr/celldeploy.py`) — after a
  publish, rename or delete, and whenever the files changed (checked every 30 s). celld
  adopts each version at its next poll, without a restart.
- **Bucket:** SeaweedFS in the container `kaim56-s3` (S3 on 127.0.0.1:8333 and the docker
  network `kaim56-apps`; data in the volume `kaim56-s3`), keys in `firecracker/celld.env`
  (0600). Garage was tried first: it does not enforce conditional writes, which celld
  needs; `celld diagnose --bucket … --endpoint …` checks that.
- **Access:** only through the manager: `/apps/<name>/` is admin-only there and forwarded to
  celld on loopback (`CELLD_URL` in Settings). celld has no login of its own and is never
  exposed. Without `CELLD_URL`, or when celld does not answer, the manager serves the
  files itself from `apps/`.

Set up (what `install.sh --with-celld` does): `bash setup-bucket.sh <firecracker dir> <this dir>`.
Without `celld.env` the manager deploys nothing; `celld dev` with `apps/` mounted as
`public/apps` still works then.
