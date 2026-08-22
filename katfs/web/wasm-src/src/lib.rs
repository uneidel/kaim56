//! katfs/0 browser transport — thin wasm-bindgen wrapper over iroh.
//!
//! The browser is a relay-only iroh client: it CONNECTS out to the native host
//! node (browsers cannot receive inbound / hole-punch — see
//! https://docs.iroh.computer/languages/wasm-browser). Application-protocol
//! wise the browser is the katfs PROVIDER (server), but at the QUIC level it is
//! the dialer. The host opens the bidirectional stream; the browser accepts it.
//!
//! This crate deliberately exposes only low-level stream primitives; all katfs
//! framing + File System Access logic lives in JS (katfs-provider.js).

use std::cell::RefCell;
use std::rc::Rc;

use iroh::endpoint::{Connection, RecvStream, SendStream};
use wasm_bindgen::prelude::*;

fn jserr<E: std::fmt::Display>(e: E) -> JsError {
    JsError::new(&e.to_string())
}

#[wasm_bindgen(start)]
pub fn start() {
    console_error_panic_hook::set_once();
}

#[wasm_bindgen]
pub struct KatfsEndpoint {
    ep: iroh::Endpoint,
}

#[wasm_bindgen]
impl KatfsEndpoint {
    /// Create a relay-only endpoint using the N0 preset (n0 relays + discovery).
    pub async fn spawn() -> Result<KatfsEndpoint, JsError> {
        let ep = iroh::Endpoint::builder(iroh::endpoint::presets::N0)
            .bind()
            .await
            .map_err(jserr)?;
        Ok(KatfsEndpoint { ep })
    }

    /// Our own endpoint id (mostly for logging/debug).
    #[wasm_bindgen(js_name = endpointId)]
    pub fn endpoint_id(&self) -> String {
        self.ep.id().to_string()
    }

    /// Connect to `endpoint_id` (hex) speaking `alpn`. Discovery (N0 DNS) is
    /// used to resolve the id to a relay address.
    pub async fn connect(&self, endpoint_id: String, alpn: Vec<u8>) -> Result<KatfsConn, JsError> {
        let id: iroh::EndpointId = endpoint_id.parse().map_err(jserr)?;
        let conn = self.ep.connect(id, &alpn).await.map_err(jserr)?;
        Ok(KatfsConn { conn })
    }
}

#[wasm_bindgen]
pub struct KatfsConn {
    conn: Connection,
}

#[wasm_bindgen]
impl KatfsConn {
    /// Accept the bidirectional stream opened by the host (katfs convention:
    /// host opens + sends the first request).
    #[wasm_bindgen(js_name = acceptBi)]
    pub async fn accept_bi(&self) -> Result<KatfsStream, JsError> {
        let (send, recv) = self.conn.accept_bi().await.map_err(jserr)?;
        Ok(KatfsStream::new(send, recv))
    }

    /// Alternative: open the bidirectional stream ourselves (if the host is
    /// built to accept_bi instead). Provided for flexibility.
    #[wasm_bindgen(js_name = openBi)]
    pub async fn open_bi(&self) -> Result<KatfsStream, JsError> {
        let (send, recv) = self.conn.open_bi().await.map_err(jserr)?;
        Ok(KatfsStream::new(send, recv))
    }
}

#[wasm_bindgen]
pub struct KatfsStream {
    send: Rc<RefCell<SendStream>>,
    recv: Rc<RefCell<RecvStream>>,
}

impl KatfsStream {
    fn new(send: SendStream, recv: RecvStream) -> Self {
        KatfsStream {
            send: Rc::new(RefCell::new(send)),
            recv: Rc::new(RefCell::new(recv)),
        }
    }
}

#[wasm_bindgen]
impl KatfsStream {
    /// Read up to `max` bytes. Returns an empty array on clean EOF.
    pub async fn read(&self, max: usize) -> Result<Vec<u8>, JsError> {
        let recv = self.recv.clone();
        let mut recv = recv.borrow_mut();
        let mut buf = vec![0u8; max];
        match recv.read(&mut buf).await.map_err(jserr)? {
            Some(n) => {
                buf.truncate(n);
                Ok(buf)
            }
            None => Ok(Vec::new()),
        }
    }

    /// Write all `data` bytes.
    pub async fn write(&self, data: Vec<u8>) -> Result<(), JsError> {
        let send = self.send.clone();
        let mut send = send.borrow_mut();
        send.write_all(&data).await.map_err(jserr)?;
        Ok(())
    }

    /// Finish (half-close) the send side.
    pub async fn finish(&self) -> Result<(), JsError> {
        let send = self.send.clone();
        let mut send = send.borrow_mut();
        send.finish().map_err(jserr)?;
        Ok(())
    }
}
