//! DSP puro, sem Python: espelha `akemi/audio/process.py` operação por operação.
//!
//! O objetivo não é "parecido": é o mesmo resultado, amostra por amostra. Por
//! isso as contas seguem o NumPy à risca: média dos canais em `f32`, resample
//! linear em `f64` e arredondamento "metade para o par" (`np.rint`).

/// Erros que viram `ValueError` do lado do Python.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DspError {
    Channels,
    Rate,
    FrameSize,
    OddBytes,
}

impl std::fmt::Display for DspError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            DspError::Channels => "channels must be >= 1",
            DspError::Rate => "sample rates must be positive",
            DspError::FrameSize => "frame_bytes must be positive",
            DspError::OddBytes => "PCM16 buffer size must be a multiple of 2 bytes",
        })
    }
}

impl std::error::Error for DspError {}

/// Bytes PCM16 na ordem da máquina (o mesmo que `np.frombuffer(..., np.int16)`).
pub fn bytes_to_i16(data: &[u8]) -> Result<Vec<i16>, DspError> {
    if !data.len().is_multiple_of(2) {
        return Err(DspError::OddBytes);
    }
    Ok(data.chunks_exact(2).map(|b| i16::from_ne_bytes([b[0], b[1]])).collect())
}

pub fn i16_to_bytes(samples: &[i16]) -> Vec<u8> {
    let mut out = Vec::with_capacity(samples.len() * 2);
    for s in samples {
        out.extend_from_slice(&s.to_ne_bytes());
    }
    out
}

#[inline]
fn to_i16_f32(v: f32) -> i16 {
    v.round_ties_even().clamp(i16::MIN as f32, i16::MAX as f32) as i16
}

#[inline]
fn to_i16_f64(v: f64) -> i16 {
    v.round_ties_even().clamp(i16::MIN as f64, i16::MAX as f64) as i16
}

/// PCM16 intercalado → mono pela média dos canais.
///
/// Como `downmix_to_mono_int16`: amostras que não fecham um quadro inteiro no
/// fim do buffer são descartadas.
pub fn downmix(samples: &[i16], channels: usize) -> Result<Vec<i16>, DspError> {
    if channels < 1 {
        return Err(DspError::Channels);
    }
    if channels == 1 {
        return Ok(samples.to_vec());
    }
    let n = channels as f32;
    Ok(samples
        .chunks_exact(channels)
        .map(|frame| {
            // Até 512 canais de int16 a soma cabe exata num f32 (< 2^24), então
            // somar na ordem não muda nada em relação à soma do NumPy.
            let sum: f32 = frame.iter().map(|&s| s as f32).sum();
            to_i16_f32(sum / n)
        })
        .collect())
}

/// Resample linear de `combined` (que já inclui as sobras da chamada anterior).
///
/// Devolve as amostras novas e quantas amostras de `combined` foram consumidas;
/// o resto vira a sobra da próxima chamada. Mesmas contas de `resample_int16`.
pub fn resample(combined: &[i16], src_rate: u32, dst_rate: u32) -> Result<(Vec<i16>, usize), DspError> {
    if src_rate == 0 || dst_rate == 0 {
        return Err(DspError::Rate);
    }
    if src_rate == dst_rate {
        return Ok((combined.to_vec(), combined.len()));
    }
    if combined.len() < 2 {
        return Ok((Vec::new(), 0));
    }
    let ratio = src_rate as f64 / dst_rate as f64;
    // int() do Python trunca em direção ao zero; aqui o valor é sempre >= 0.
    let n_out = (((combined.len() - 1) as f64) / ratio) as usize;
    if n_out == 0 {
        return Ok((Vec::new(), 0));
    }
    let mut out = Vec::with_capacity(n_out);
    let mut last_pos = 0.0f64;
    for i in 0..n_out {
        let pos = i as f64 * ratio;
        let idx = pos as usize;
        let frac = pos - idx as f64;
        let y0 = combined[idx] as f64;
        let y1 = combined[idx + 1] as f64;
        out.push(to_i16_f64(y0 + frac * (y1 - y0)));
        last_pos = pos;
    }
    let consumed = last_pos.floor() as usize + 1;
    Ok((out, consumed))
}

/// Resample com estado: guarda as amostras que sobraram entre chamadas.
#[derive(Debug, Clone)]
pub struct StreamResampler {
    pub src_rate: u32,
    pub dst_rate: u32,
    carry: Vec<i16>,
}

impl StreamResampler {
    pub fn new(src_rate: u32, dst_rate: u32) -> Result<Self, DspError> {
        if src_rate == 0 || dst_rate == 0 {
            return Err(DspError::Rate);
        }
        Ok(Self {
            src_rate,
            dst_rate,
            carry: Vec::new(),
        })
    }

    pub fn push(&mut self, samples: &[i16]) -> Vec<i16> {
        self.carry.extend_from_slice(samples);
        // As taxas já foram validadas no `new`, então não há erro aqui.
        let (out, consumed) = resample(&self.carry, self.src_rate, self.dst_rate).unwrap_or_default();
        self.carry.drain(..consumed);
        out
    }

    pub fn pending(&self) -> usize {
        self.carry.len()
    }

    pub fn clear(&mut self) {
        self.carry.clear();
    }
}

/// Fatia um fluxo de bytes em quadros de tamanho fixo (ex.: 640 B = 20 ms a 16 kHz).
#[derive(Debug, Clone)]
pub struct FrameAssembler {
    pub frame_bytes: usize,
    buf: Vec<u8>,
}

impl FrameAssembler {
    pub fn new(frame_bytes: usize) -> Result<Self, DspError> {
        if frame_bytes == 0 {
            return Err(DspError::FrameSize);
        }
        Ok(Self {
            frame_bytes,
            buf: Vec::new(),
        })
    }

    pub fn push(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        if data.is_empty() {
            return Vec::new();
        }
        self.buf.extend_from_slice(data);
        let whole = self.buf.len() / self.frame_bytes * self.frame_bytes;
        let frames = self.buf[..whole]
            .chunks_exact(self.frame_bytes)
            .map(<[u8]>::to_vec)
            .collect();
        self.buf.drain(..whole);
        frames
    }

    pub fn pending(&self) -> usize {
        self.buf.len()
    }

    pub fn clear(&mut self) {
        self.buf.clear();
    }
}

/// O caminho inteiro de um callback do PortAudio numa chamada só:
/// bytes do dispositivo (int16 intercalado) → mono → taxa de saída → quadros.
#[derive(Debug, Clone)]
pub struct Pipeline {
    pub channels: usize,
    pub frame_ms: u32,
    resampler: StreamResampler,
    frames: FrameAssembler,
    pub frames_out: u64,
}

impl Pipeline {
    pub fn new(device_rate: u32, channels: usize, output_rate: u32, frame_ms: u32) -> Result<Self, DspError> {
        if channels < 1 {
            return Err(DspError::Channels);
        }
        let frame_bytes = output_rate as usize * frame_ms as usize / 1000 * 2;
        Ok(Self {
            channels,
            frame_ms,
            resampler: StreamResampler::new(device_rate, output_rate)?,
            frames: FrameAssembler::new(frame_bytes)?,
            frames_out: 0,
        })
    }

    pub fn device_rate(&self) -> u32 {
        self.resampler.src_rate
    }

    pub fn output_rate(&self) -> u32 {
        self.resampler.dst_rate
    }

    pub fn frame_bytes(&self) -> usize {
        self.frames.frame_bytes
    }

    pub fn push(&mut self, data: &[u8]) -> Result<Vec<Vec<u8>>, DspError> {
        let samples = bytes_to_i16(data)?;
        let mono = downmix(&samples, self.channels)?;
        let out = self.resampler.push(&mono);
        let frames = self.frames.push(&i16_to_bytes(&out));
        self.frames_out += frames.len() as u64;
        Ok(frames)
    }

    pub fn clear(&mut self) {
        self.resampler.clear();
        self.frames.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downmix_stereo_averages_channels() {
        assert_eq!(downmix(&[1000, 3000, 2000, 4000], 2).unwrap(), vec![2000, 3000]);
    }

    #[test]
    fn downmix_rounds_half_to_even_like_numpy() {
        // (1 + 2) / 2 = 1.5 → 2;  (2 + 3) / 2 = 2.5 → 2;  (-1 + -2) / 2 = -1.5 → -2
        assert_eq!(downmix(&[1, 2, 2, 3, -1, -2], 2).unwrap(), vec![2, 2, -2]);
    }

    #[test]
    fn downmix_drops_incomplete_frame_and_rejects_zero_channels() {
        assert_eq!(downmix(&[10, 20, 30], 2).unwrap(), vec![15]);
        assert_eq!(downmix(&[1], 2).unwrap(), Vec::<i16>::new());
        assert_eq!(downmix(&[1], 0), Err(DspError::Channels));
    }

    #[test]
    fn downmix_extremes_stay_in_range() {
        assert_eq!(
            downmix(&[i16::MIN, i16::MIN, i16::MAX, i16::MAX], 2).unwrap(),
            vec![i16::MIN, i16::MAX]
        );
    }

    #[test]
    fn resample_48k_to_16k_is_exact_third() {
        let src: Vec<i16> = (0..480).collect();
        let (out, consumed) = resample(&src, 48000, 16000).unwrap();
        assert_eq!(out.len(), 159); // (480 - 1) / 3
        assert_eq!(out[1], 3);
        assert_eq!(consumed, 475); // floor(158 * 3) + 1
    }

    #[test]
    fn resample_same_rate_passthrough() {
        let (out, consumed) = resample(&[1, 2, 3, 4], 16000, 16000).unwrap();
        assert_eq!(out, vec![1, 2, 3, 4]);
        assert_eq!(consumed, 4);
    }

    #[test]
    fn resample_too_short_keeps_everything() {
        assert_eq!(resample(&[7], 48000, 16000).unwrap(), (vec![], 0));
        assert_eq!(resample(&[1, 2], 16000, 0), Err(DspError::Rate));
    }

    #[test]
    fn stream_resampler_carries_leftover_between_pushes() {
        let mut r = StreamResampler::new(48000, 16000).unwrap();
        let src: Vec<i16> = (0..480).collect();
        assert_eq!(r.push(&src).len(), 159);
        assert_eq!(r.pending(), 5); // 480 - 475 consumidas
                                    // A próxima chamada começa pelas sobras: [475, 476, 477, 478, 479, 0, 1, ...]
                                    // (mesmo resultado do StreamResampler em Python)
        assert_eq!(r.push(&[0, 1, 2, 3]), vec![475, 478]);
        assert_eq!(r.pending(), 5);
        r.clear();
        assert_eq!(r.pending(), 0);
    }

    #[test]
    fn frame_assembler_splits_and_keeps_remainder() {
        let mut fa = FrameAssembler::new(4).unwrap();
        assert_eq!(fa.push(b"abcdefg"), vec![b"abcd".to_vec()]);
        assert_eq!(fa.pending(), 3);
        assert_eq!(fa.push(b"hij"), vec![b"efgh".to_vec()]);
        assert!(FrameAssembler::new(0).is_err());
    }

    #[test]
    fn pipeline_48k_stereo_to_20ms_frames() {
        let mut p = Pipeline::new(48000, 2, 16000, 20).unwrap();
        assert_eq!(p.frame_bytes(), 640);
        // 1 s de áudio estéreo a 48 kHz → quase 50 quadros de 20 ms.
        let raw = i16_to_bytes(&vec![100i16; 48000 * 2]);
        let frames = p.push(&raw).unwrap();
        assert_eq!(frames.len(), 49);
        assert!(frames.iter().all(|f| f.len() == 640));
        assert_eq!(p.frames_out, 49);
        assert_eq!(p.push(b"\x00"), Err(DspError::OddBytes));
    }
}
