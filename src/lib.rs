use preflate_rs::{
    ExitCode, PreflateConfig, PreflateError as RustPreflateError, preflate_whole_deflate_stream,
    recreate_whole_deflate_stream,
};
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

const PREFLATE_VERSION: &str = "0.7.6";

create_exception!(_preflate, PreflateError, PyException);

fn to_python_error(error: RustPreflateError) -> PyErr {
    PreflateError::new_err((
        error.exit_code().as_integer_error_code(),
        error.message().to_owned(),
    ))
}

fn encode_inner(
    raw_deflate: &[u8],
    verify: bool,
    plain_text_limit: Option<usize>,
) -> Result<(Vec<u8>, Vec<u8>), RustPreflateError> {
    let mut config = PreflateConfig {
        verify_compression: verify,
        ..PreflateConfig::default()
    };
    if let Some(limit) = plain_text_limit {
        config.plain_text_limit = limit;
    }

    let (result, plaintext) = preflate_whole_deflate_stream(raw_deflate, &config)?;
    if result.compressed_size != raw_deflate.len() {
        return Err(RustPreflateError::new(
            ExitCode::InvalidDeflate,
            "raw DEFLATE stream contains trailing or unconsumed bytes",
        ));
    }

    let plaintext = plaintext.text().to_vec();
    if verify {
        let recreated = recreate_whole_deflate_stream(&plaintext, &result.corrections)?;
        if recreated != raw_deflate {
            return Err(RustPreflateError::new(
                ExitCode::RoundtripMismatch,
                "recreated raw DEFLATE bytes differ from the input",
            ));
        }
    }

    Ok((plaintext, result.corrections))
}

#[pyfunction]
#[pyo3(signature = (raw_deflate, *, verify=true, plain_text_limit=None))]
fn preflate_encode(
    py: Python<'_>,
    raw_deflate: &[u8],
    verify: bool,
    plain_text_limit: Option<usize>,
) -> PyResult<(Vec<u8>, Vec<u8>)> {
    py.detach(|| encode_inner(raw_deflate, verify, plain_text_limit))
        .map_err(to_python_error)
}

#[pyfunction]
fn preflate_decode(
    py: Python<'_>,
    plaintext: &[u8],
    reconstruction_data: &[u8],
) -> PyResult<Vec<u8>> {
    py.detach(|| recreate_whole_deflate_stream(plaintext, reconstruction_data))
        .map_err(to_python_error)
}

#[pyfunction]
fn preflate_version() -> &'static str {
    PREFLATE_VERSION
}

#[pymodule]
fn _preflate(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("PreflateError", module.py().get_type::<PreflateError>())?;
    module.add_function(wrap_pyfunction!(preflate_encode, module)?)?;
    module.add_function(wrap_pyfunction!(preflate_decode, module)?)?;
    module.add_function(wrap_pyfunction!(preflate_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_raw_deflate_roundtrip() {
        let raw = [0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x57, 0xc8, 0x40, 0x90, 0x00];
        let (plaintext, corrections) = encode_inner(&raw, true, None).unwrap();
        assert_eq!(plaintext, b"hello hello hello");
        assert_eq!(
            recreate_whole_deflate_stream(&plaintext, &corrections).unwrap(),
            raw
        );
    }

    #[test]
    fn plaintext_limit_is_enforced() {
        let raw = [0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x57, 0xc8, 0x40, 0x90, 0x00];
        let error = encode_inner(&raw, true, Some(4)).unwrap_err();
        assert_eq!(error.exit_code(), ExitCode::PlainTextLimit);
    }
}
