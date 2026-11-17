//! `akemi_audio_rs`: o caminho quente do áudio da Akemi em Rust.
//!
//! Mesmo contrato de `akemi/audio/process.py` e `akemi/audio/vad.py`, com
//! resultado idêntico amostra por amostra. A Akemi usa este módulo quando ele
//! está instalado e volta para o Python quando não está (`akemi/audio/native.py`).

pub mod dsp;

#[cfg(feature = "python")]
mod python {
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;

    use crate::dsp;

    impl From<dsp::DspError> for PyErr {
        fn from(err: dsp::DspError) -> PyErr {
            PyValueError::new_err(err.to_string())
        }
    }

    fn frames_to_py<'py>(py: Python<'py>, frames: Vec<Vec<u8>>) -> Vec<Bound<'py, PyBytes>> {
        frames.iter().map(|f| PyBytes::new(py, f)).collect()
    }

    /// PCM16 intercalado → mono (bytes). Igual a `downmix_to_mono_int16`.
    #[pyfunction]
    fn downmix_to_mono_int16<'py>(py: Python<'py>, pcm: &[u8], channels: usize) -> PyResult<Bound<'py, PyBytes>> {
        let mono = dsp::downmix(&dsp::bytes_to_i16(pcm)?, channels)?;
        Ok(PyBytes::new(py, &dsp::i16_to_bytes(&mono)))
    }

    /// Resample linear. Devolve ``(saída, sobra)``, ambos em bytes PCM16.
    #[pyfunction]
    #[pyo3(signature = (samples, src_rate, dst_rate, carry=None))]
    fn resample_int16<'py>(
        py: Python<'py>,
        samples: &[u8],
        src_rate: u32,
        dst_rate: u32,
        carry: Option<&[u8]>,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let mut combined = match carry {
            Some(c) => dsp::bytes_to_i16(c)?,
            None => Vec::new(),
        };
        combined.extend(dsp::bytes_to_i16(samples)?);
        let (out, consumed) = dsp::resample(&combined, src_rate, dst_rate)?;
        Ok((
            PyBytes::new(py, &dsp::i16_to_bytes(&out)),
            PyBytes::new(py, &dsp::i16_to_bytes(&combined[consumed..])),
        ))
    }

    /// Resample com estado entre chamadas.
    #[pyclass(module = "akemi_audio_rs")]
    struct StreamResampler {
        inner: dsp::StreamResampler,
    }

    #[pymethods]
    impl StreamResampler {
        #[new]
        fn new(src_rate: u32, dst_rate: u32) -> PyResult<Self> {
            Ok(Self {
                inner: dsp::StreamResampler::new(src_rate, dst_rate)?,
            })
        }

        /// Bytes PCM16 mono na taxa de origem → bytes PCM16 na taxa de destino.
        fn push<'py>(&mut self, py: Python<'py>, samples: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
            let out = self.inner.push(&dsp::bytes_to_i16(samples)?);
            Ok(PyBytes::new(py, &dsp::i16_to_bytes(&out)))
        }

        fn clear(&mut self) {
            self.inner.clear();
        }

        #[getter]
        fn src_rate(&self) -> u32 {
            self.inner.src_rate
        }

        #[getter]
        fn dst_rate(&self) -> u32 {
            self.inner.dst_rate
        }

        /// Amostras de origem guardadas para a próxima chamada.
        #[getter]
        fn pending(&self) -> usize {
            self.inner.pending()
        }
    }

    /// Fatia um fluxo de bytes em quadros de tamanho fixo.
    #[pyclass(module = "akemi_audio_rs")]
    struct FrameAssembler {
        inner: dsp::FrameAssembler,
    }

    #[pymethods]
    impl FrameAssembler {
        #[new]
        fn new(frame_bytes: usize) -> PyResult<Self> {
            Ok(Self {
                inner: dsp::FrameAssembler::new(frame_bytes)?,
            })
        }

        fn push<'py>(&mut self, py: Python<'py>, data: &[u8]) -> Vec<Bound<'py, PyBytes>> {
            frames_to_py(py, self.inner.push(data))
        }

        fn clear(&mut self) {
            self.inner.clear();
        }

        #[getter]
        fn frame_bytes(&self) -> usize {
            self.inner.frame_bytes
        }

        #[getter]
        fn pending(&self) -> usize {
            self.inner.pending()
        }
    }

    /// Um callback inteiro numa chamada: bytes do dispositivo → quadros de 20 ms.
    #[pyclass(module = "akemi_audio_rs")]
    struct AudioPipeline {
        inner: dsp::Pipeline,
    }

    #[pymethods]
    impl AudioPipeline {
        #[new]
        #[pyo3(signature = (device_rate, channels, output_rate=16000, frame_ms=20))]
        fn new(device_rate: u32, channels: usize, output_rate: u32, frame_ms: u32) -> PyResult<Self> {
            Ok(Self {
                inner: dsp::Pipeline::new(device_rate, channels, output_rate, frame_ms)?,
            })
        }

        fn push<'py>(&mut self, py: Python<'py>, data: &[u8]) -> PyResult<Vec<Bound<'py, PyBytes>>> {
            Ok(frames_to_py(py, self.inner.push(data)?))
        }

        fn clear(&mut self) {
            self.inner.clear();
        }

        #[getter]
        fn device_rate(&self) -> u32 {
            self.inner.device_rate()
        }

        #[getter]
        fn output_rate(&self) -> u32 {
            self.inner.output_rate()
        }

        #[getter]
        fn channels(&self) -> usize {
            self.inner.channels
        }

        #[getter]
        fn frame_bytes(&self) -> usize {
            self.inner.frame_bytes()
        }

        /// Quantos quadros já saíram desde a criação.
        #[getter]
        fn frames_out(&self) -> u64 {
            self.inner.frames_out
        }
    }

    /// A instância C do WebRTC VAD (libfvad) não tem afinidade de thread:
    /// mover entre threads é seguro. Todo método que mexe nela pede `&mut`, e o
    /// PyO3 garante um só `&mut` por vez, então `&` compartilhado não faz nada.
    struct VadCell(webrtc_vad::Vad);
    // SAFETY: ver acima. O ponteiro é dono exclusivo da memória do libfvad.
    unsafe impl Send for VadCell {}
    // SAFETY: `webrtc_vad::Vad` não tem nenhum método `&self`.
    unsafe impl Sync for VadCell {}

    fn vad_rate(rate: u32) -> PyResult<webrtc_vad::SampleRate> {
        match rate {
            8000 => Ok(webrtc_vad::SampleRate::Rate8kHz),
            16000 => Ok(webrtc_vad::SampleRate::Rate16kHz),
            32000 => Ok(webrtc_vad::SampleRate::Rate32kHz),
            48000 => Ok(webrtc_vad::SampleRate::Rate48kHz),
            _ => Err(PyValueError::new_err(
                "Sample rate must be one of (8000, 16000, 32000, 48000)",
            )),
        }
    }

    fn vad_mode(aggressiveness: u8) -> PyResult<webrtc_vad::VadMode> {
        match aggressiveness {
            0 => Ok(webrtc_vad::VadMode::Quality),
            1 => Ok(webrtc_vad::VadMode::LowBitrate),
            2 => Ok(webrtc_vad::VadMode::Aggressive),
            3 => Ok(webrtc_vad::VadMode::VeryAggressive),
            _ => Err(PyValueError::new_err(
                "aggressiveness must be an integer between 0 and 3",
            )),
        }
    }

    /// WebRTC VAD com a mesma interface do ``webrtcvad.Vad``.
    #[pyclass(module = "akemi_audio_rs")]
    struct Vad {
        cell: VadCell,
        rate: u32,
        aggressiveness: u8,
    }

    #[pymethods]
    impl Vad {
        #[new]
        #[pyo3(signature = (aggressiveness=2, sample_rate=16000))]
        fn new(aggressiveness: u8, sample_rate: u32) -> PyResult<Self> {
            let vad = webrtc_vad::Vad::new_with_rate_and_mode(vad_rate(sample_rate)?, vad_mode(aggressiveness)?);
            Ok(Self {
                cell: VadCell(vad),
                rate: sample_rate,
                aggressiveness,
            })
        }

        fn set_mode(&mut self, aggressiveness: u8) -> PyResult<()> {
            self.cell.0.set_mode(vad_mode(aggressiveness)?);
            self.aggressiveness = aggressiveness;
            Ok(())
        }

        /// ``True`` se o quadro (10/20/30 ms de PCM16 mono) tem voz.
        /// Tamanho errado levanta ``ValueError`` (como o ``webrtcvad``).
        #[pyo3(signature = (frame, sample_rate=None))]
        fn is_speech(&mut self, frame: &[u8], sample_rate: Option<u32>) -> PyResult<bool> {
            let rate = sample_rate.unwrap_or(self.rate);
            if rate != self.rate {
                self.cell.0.set_sample_rate(vad_rate(rate)?);
                self.rate = rate;
            }
            let samples = dsp::bytes_to_i16(frame)?;
            let per_ms = (rate / 1000) as usize;
            if samples.is_empty() || ![10, 20, 30].iter().any(|ms| ms * per_ms == samples.len()) {
                return Err(PyValueError::new_err(format!(
                    "frame must be 10, 20 or 30 ms of PCM16 mono at {rate} Hz (got {} bytes)",
                    frame.len()
                )));
            }
            self.cell
                .0
                .is_voice_segment(&samples)
                .map_err(|_| PyValueError::new_err("invalid frame length"))
        }

        #[getter]
        fn sample_rate(&self) -> u32 {
            self.rate
        }

        #[getter]
        fn aggressiveness(&self) -> u8 {
            self.aggressiveness
        }
    }

    #[pymodule]
    fn akemi_audio_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add_function(wrap_pyfunction!(downmix_to_mono_int16, m)?)?;
        m.add_function(wrap_pyfunction!(resample_int16, m)?)?;
        m.add_class::<StreamResampler>()?;
        m.add_class::<FrameAssembler>()?;
        m.add_class::<AudioPipeline>()?;
        m.add_class::<Vad>()?;
        Ok(())
    }
}
