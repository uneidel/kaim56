# katfs/0 — P2P-Dateizugriff (iroh)

## Rollen
- HOST-Knoten (nativ, Rust, auf dem Server): iroh-Endpoint, **akzeptiert** ALPN "katfs/0".
  Er ist der Datei-KONSUMENT: sendet Requests, empfängt Responses.
- BROWSER (WASM): **verbindet sich** zum HOST (node-id/ticket). Datei-PROVIDER:
  empfängt Requests, antwortet aus dem per File System Access API gewählten Verzeichnis.

## Transport
Eine iroh bidirektionale Verbindung. Nachrichten je: 4-Byte big-endian Länge + JSON.
Bei read/write folgt ein zweiter Frame: 4-Byte Länge + Rohbytes (Dateiinhalt).

## Requests (HOST -> BROWSER)
- {"id":N,"op":"hello","path":"."}            -> {"id":N,"ok":true,"share":"<id>","name":"<ordner>","device":"<plattform>","readonly":bool}
- {"id":N,"op":"list","path":"rel/pfad"}      -> {"id":N,"ok":true,"entries":[{"name","dir","size"}]}
- {"id":N,"op":"stat","path":"..."}           -> {"id":N,"ok":true,"exists","dir","size"}
- {"id":N,"op":"read","path":"..."}           -> {"id":N,"ok":true,"size":M} + [M Bytes]
- {"id":N,"op":"write","path":"...","size":M} + [M Bytes] -> {"id":N,"ok":true}
- {"id":N,"op":"delete","path":"...","recursive":bool} -> {"id":N,"ok":true}
Fehler: {"id":N,"ok":false,"error":"..."}

## Mehrere Freigaben gleichzeitig
Der HOST haelt **beliebig viele** Browser-Verbindungen. Jede meldet sich beim
Verbinden per `hello` mit einer **share-id**, die der Browser im `localStorage`
haelt — ein Reload verbindet damit *dieselbe* Freigabe neu (der alte Eintrag
wird ersetzt, nicht verdoppelt). Antwortet ein Browser `ok:false` auf `hello`
(alte Seite), faellt der Host auf den `list "."`-Warmup zurueck und vergibt die
id selbst (`s<epoch>`).

Die id kommt aus dem Browser und ist **kein Zugangsschutz**: wer sie kennt und
den Knoten erreicht, kann eine Freigabe mit derselben id ersetzen. Das war schon
vorher so (jede Verbindung verdraengte die einzige aktive) — nur jetzt gezielt.

## Host lokale HTTP-API (für die Agent-Tools; 127.0.0.1 / Gateway)
- GET  /status                 -> {"connected":bool,"count":N,"share":"<name|N shares>"}
- GET  /shares                 -> {"shares":[{"id","name","device","readonly","since"}]}
- GET  /ls?path=...[&share=ID]    -> {"entries":[...]}
- GET  /read?path=...[&share=ID]  -> Rohbytes (404 wenn fehlt)
- POST /write?path=...[&share=ID] (Body=Bytes) -> {"ok":true}
- POST /delete?path=...[&recursive=1][&share=ID] -> {"ok":true}
- GET  /                       -> Browser-Seite (web/), mit eingebetteter node-id
- GET  /nodeid                 -> {"node_id":"..."}

Ohne `share` bedient der Knoten die Anfrage nur, solange **genau eine** Freigabe
aktiv ist; bei mehreren antwortet er mit der Liste der ids statt zu raten.

`delete` ist unwiderruflich (kein Papierkorb). Drei Sperren: die Wurzel der
Freigabe laesst sich nicht loeschen (leerer Pfad -> 400), `..` wird wie ueberall
abgewiesen, und ein **nicht-leeres Verzeichnis** scheitert ohne `recursive=1`.
Eine als read-only gemeldete Freigabe lehnt `delete` genauso ab wie `write`.

## Pfade
Immer relativ zum freigegebenen Wurzelverzeichnis. ".." wird abgelehnt (kein Ausbruch).

## Stream-Rollen (ergänzt nach Implementierung)
Der HOST öffnet den bidirektionalen Stream (`open_bi`) und sendet die erste Request;
der BROWSER nimmt ihn an (`accept_bi`). Grund: In QUIC wird ein Stream beim Peer erst
durch die ersten Bytes sichtbar — der HOST (Konsument) sendet zuerst.
Verbindung per blanker node-id setzt n0-DNS-Discovery voraus (N0-Preset publiziert die node-id).
