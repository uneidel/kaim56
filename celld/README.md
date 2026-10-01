# kaim56-apps on celld

The browser apps (`../apps/<name>/`) hosted by [celld](https://github.com/denoland/celld),
a self-hosted runtime for Cloudflare Workers / Durable Objects.

- **Hosting:** `apps/` is mounted as `public/apps` — `/apps/<name>/` serves the app's files.
- **Access:** only through the manager: `/apps/<name>/` is admin-only there and forwarded to
  celld on loopback (`CELLD_URL` in Settings). celld has no login of its own and is never
  exposed. Without `CELLD_URL`, or when celld does not answer, the manager serves the
  files itself.
- **Mode:** `celld dev` (local storage in the `celld-dev` volume, no bucket). A production
  setup needs a bucket (S3-compatible, GCS or Azure) and `celld` instead of `celld dev`.

Run (what `install.sh --with-celld` does):

    docker run -d --restart unless-stopped --name kaim56-celld -p 127.0.0.1:9876:9876 \
      -v "$PWD":/project -v "$PWD/../apps":/project/public/apps:ro -v celld-dev:/project/.celld \
      -w /project ghcr.io/denoland/celld dev /project --host 0.0.0.0
