// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// kaim56-wake: openWakeWord (github.com/dscripka/openWakeWord, Apache-2.0)
// streaming inference in pure Rust (tract), as a sidecar of the Go voice
// client (which stays cgo-free, like with kaim56-tunnel).
//
//   stdin : 16 kHz mono s16le PCM, any chunking
//   stdout: one score 0..1 per 1280 samples (80 ms), "%.4f\n", flushed
//
//   kaim56-wake [classifier.onnx]   default: the embedded "Hey Bender" model
//
// The pipeline mirrors openwakeword.utils.AudioFeatures for 1280-sample
// chunks: melspectrogram over the last 1280+480 samples, x/10+2, appended to
// the mel buffer (starts as 76 rows of ones); every chunk one embedding of the
// last 76 mel rows; the classifier sees the last 16 embeddings (the buffer
// starts with embeddings of 4 s of noise); the first 5 predictions are 0.
use anyhow::{Context, Result};
use std::io::{Read, Write};
use tract_onnx::prelude::*;

const MEL_ONNX: &[u8] = include_bytes!("../models/melspectrogram.onnx");
const EMB_ONNX: &[u8] = include_bytes!("../models/embedding_model.onnx");
const CLS_ONNX: &[u8] = include_bytes!("../models/hey_bender.onnx");

const CHUNK: usize = 1280; // 80 ms
const MEL_CTX: usize = CHUNK + 160 * 3; // samples per melspectrogram call
const MEL_BINS: usize = 32;
const EMB_WIN: usize = 76; // mel rows per embedding
const EMB_DIM: usize = 96;
const CLS_WIN: usize = 16; // embeddings per prediction

type Plan = SimplePlan<TypedFact, Box<dyn TypedOp>, Graph<TypedFact, Box<dyn TypedOp>>>;

fn load(bytes: &[u8], shape: &[usize]) -> Result<Plan> {
    Ok(tract_onnx::onnx()
        .model_for_read(&mut std::io::Cursor::new(bytes))?
        .with_input_fact(0, f32::fact(shape).into())?
        .into_optimized()?
        .into_runnable()?)
}

struct Wake {
    mel: Plan,
    mel_long: Option<(Plan, usize)>, // for the noise warm-up (4 s at once)
    emb: Plan,
    cls: Plan,
    raw: Vec<f32>,         // the last MEL_CTX samples (int16 values as f32)
    mels: Vec<[f32; MEL_BINS]>,
    feats: Vec<[f32; EMB_DIM]>,
    n_pred: usize,
}

impl Wake {
    fn new(cls: &[u8]) -> Result<Self> {
        let warm = 16000 * 4;
        let mut w = Wake {
            mel: load(MEL_ONNX, &[1, MEL_CTX])?,
            mel_long: Some((load(MEL_ONNX, &[1, warm])?, warm)),
            emb: load(EMB_ONNX, &[1, EMB_WIN, MEL_BINS, 1])?,
            cls: load(cls, &[1, CLS_WIN, EMB_DIM])?,
            raw: vec![0.0; MEL_CTX],
            mels: vec![[1.0; MEL_BINS]; EMB_WIN],
            feats: Vec::new(),
            n_pred: 0,
        };
        // feature buffer starts with embeddings of 4 s of noise in [-1000, 1000)
        let (plan, n) = w.mel_long.take().unwrap();
        let mut seed: u64 = 0x9e3779b97f4a7c15;
        let noise: Vec<f32> = (0..n)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                (seed % 2000) as f32 - 1000.0
            })
            .collect();
        let rows = Self::melspec(&plan, &noise)?;
        let mut i = 0;
        while i + EMB_WIN <= rows.len() {
            let e = w.embed(&rows[i..i + EMB_WIN])?;
            w.feats.push(e);
            i += 8;
        }
        Ok(w)
    }

    fn melspec(plan: &Plan, samples: &[f32]) -> Result<Vec<[f32; MEL_BINS]>> {
        let t = tract_ndarray::Array2::from_shape_vec((1, samples.len()), samples.to_vec())?;
        let out = plan.run(tvec!(t.into_tensor().into()))?;
        let v = out[0].to_array_view::<f32>()?;
        let flat: Vec<f32> = v.iter().copied().collect();
        Ok(flat
            .chunks(MEL_BINS)
            .map(|c| {
                let mut r = [0f32; MEL_BINS];
                for (d, s) in r.iter_mut().zip(c) {
                    *d = s / 10.0 + 2.0;
                }
                r
            })
            .collect())
    }

    fn embed(&self, rows: &[[f32; MEL_BINS]]) -> Result<[f32; EMB_DIM]> {
        let flat: Vec<f32> = rows.iter().flat_map(|r| r.iter().copied()).collect();
        let t = tract_ndarray::Array4::from_shape_vec((1, EMB_WIN, MEL_BINS, 1), flat)?;
        let out = self.emb.run(tvec!(t.into_tensor().into()))?;
        let mut e = [0f32; EMB_DIM];
        for (d, s) in e.iter_mut().zip(out[0].to_array_view::<f32>()?.iter()) {
            *d = *s;
        }
        Ok(e)
    }

    /// One 1280-sample chunk -> the classifier score.
    fn step(&mut self, chunk: &[f32]) -> Result<f32> {
        self.raw.drain(..CHUNK);
        self.raw.extend_from_slice(chunk);
        let rows = Self::melspec(&self.mel, &self.raw)?;
        self.mels.extend(rows);
        if self.mels.len() > 970 {
            let cut = self.mels.len() - 970;
            self.mels.drain(..cut);
        }
        let e = self.embed(&self.mels[self.mels.len() - EMB_WIN..])?;
        self.feats.push(e);
        if self.feats.len() > 120 {
            let cut = self.feats.len() - 120;
            self.feats.drain(..cut);
        }
        let win: Vec<f32> = self.feats[self.feats.len() - CLS_WIN..]
            .iter()
            .flat_map(|r| r.iter().copied())
            .collect();
        let t = tract_ndarray::Array3::from_shape_vec((1, CLS_WIN, EMB_DIM), win)?;
        let out = self.cls.run(tvec!(t.into_tensor().into()))?;
        let score = *out[0].to_array_view::<f32>()?.iter().next().context("empty output")?;
        self.n_pred += 1;
        Ok(if self.n_pred <= 5 { 0.0 } else { score })
    }
}

fn main() -> Result<()> {
    let cls = match std::env::args().nth(1) {
        Some(p) if p == "--version" => {
            println!("kaim56-wake {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Some(p) => std::fs::read(&p).with_context(|| format!("reading {p}"))?,
        None => CLS_ONNX.to_vec(),
    };
    let mut w = Wake::new(&cls)?;
    let mut stdin = std::io::stdin().lock();
    let mut stdout = std::io::stdout().lock();
    let mut buf = vec![0u8; CHUNK * 2];
    loop {
        if let Err(e) = stdin.read_exact(&mut buf) {
            if e.kind() == std::io::ErrorKind::UnexpectedEof {
                return Ok(());
            }
            return Err(e.into());
        }
        let chunk: Vec<f32> = buf
            .chunks_exact(2)
            .map(|b| i16::from_le_bytes([b[0], b[1]]) as f32)
            .collect();
        let s = w.step(&chunk)?;
        writeln!(stdout, "{s:.4}")?;
        stdout.flush()?;
    }
}
