// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONObject

/**
 * The manager's apps, as the Apps tab of the manager UI shows them:
 * GET /api/apps (the local apps: served by celld/the manager, or moved to
 * Cloudflare) plus GET /api/apps/cloudflare (every Worker on the account,
 * also those created outside the manager). Pure parsing, unit-tested.
 */
object AppsCatalog {
    /** where: "local" (opens in the app, through the manager), "cloud" (moved
     *  to Cloudflare by the manager), "cfonly" (a Worker created elsewhere).
     *  url: the Cloudflare address, empty for local apps. */
    data class Entry(val name: String, val title: String, val icon: String,
                     val where: String, val url: String, val detail: String)

    fun parse(appsJson: String?, cfJson: String?): List<Entry> {
        val out = mutableListOf<Entry>()
        val local = mutableSetOf<String>()
        runCatching { JSONObject(appsJson ?: "") }.getOrNull()?.optJSONArray("apps")?.let { arr ->
            for (i in 0 until arr.length()) {
                val a = arr.optJSONObject(i) ?: continue
                val name = a.optString("name")
                if (name.isBlank()) continue
                local += name
                val cloud = a.optJSONObject("cloud")
                val onCf = a.optString("served_by") == "cloudflare"
                out += Entry(
                    name, a.optString("title").ifBlank { name }, a.optString("icon"),
                    if (onCf) "cloud" else "local",
                    cloud?.optString("url").orEmpty().takeIf { onCf }.orEmpty(),
                    a.optString("description").ifBlank { if (onCf) "on Cloudflare" else "served by ${a.optString("served_by", "manager")}" },
                )
            }
        }
        runCatching { JSONObject(cfJson ?: "") }.getOrNull()?.optJSONArray("workers")?.let { arr ->
            for (i in 0 until arr.length()) {
                val w = arr.optJSONObject(i) ?: continue
                if (!w.isNull("app") && w.optString("app").isNotBlank()) continue   // a local app's worker: listed above
                val name = w.optString("name")
                if (name.isBlank()) continue
                out += Entry(name, name, "", "cfonly", w.optString("url"),
                    "created outside the manager" + if (w.optBoolean("assets")) " · static files" else "")
            }
        }
        return out
    }

    /** The script that makes fetch() POSTs (and every other non-GET) work in
     *  the WebView: shouldInterceptRequest only sees GETs without a body, so
     *  those go through the KaimBridge JavaScript interface instead. */
    const val FETCH_SHIM = """<script>(function(){
const of=window.fetch.bind(window);let n=0;const P={};
window.__kaimDone=function(id,st,ct,b64){const p=P[id];delete P[id];if(!p)return;
 if(st<200){p.rej(new TypeError(b64||'network error'));return}
 const bin=atob(b64||'');const u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
 p.res(new Response(st===204||st===304?null:u,{status:st,headers:{'Content-Type':ct||''}}))};
window.fetch=async function(input,init){const src=input instanceof Request?input:null;
 const u=new URL(src?src.url:String(input),location.href);const req=new Request(src||u.href,init);const m=req.method.toUpperCase();
 if(m==='GET'||m==='HEAD'||u.origin!==location.origin)return of(input,init);
 const b=new Uint8Array(await req.arrayBuffer());let s='';for(let i=0;i<b.length;i++)s+=String.fromCharCode(b[i]);
 const id=++n;return new Promise((res,rej)=>{P[id]={res,rej};
  KaimBridge.request(id,m,u.pathname+u.search,req.headers.get('Content-Type')||'',btoa(s))})};
})();</script>"""

    /** The shim goes first into <head> (or in front of the page without one). */
    fun inject(html: String): String {
        val m = Regex("<head[^>]*>", RegexOption.IGNORE_CASE).find(html)
        return if (m != null) html.substring(0, m.range.last + 1) + FETCH_SHIM + html.substring(m.range.last + 1)
        else FETCH_SHIM + html
    }
}
