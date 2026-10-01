// kAIm56 — the apps' Worker on celld. Serves the static apps (public/apps/<name>/);
// a path ending in "/" gets its index.html. celld 0.6 dev does not resolve a
// directory index below the root itself (/t/ -> 404 while /t/index.html
// redirects there), so html_handling is "none" and the mapping happens here.
// SPDX-License-Identifier: AGPL-3.0-or-later
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/")) url.pathname += "index.html";
    return env.ASSETS.fetch(new Request(url, request));
  },
};
