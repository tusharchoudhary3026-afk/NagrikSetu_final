"""Sarvam Saaras speech-to-text integration and safe local configuration."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
import wave

try:
    from sarvamai import SarvamAI
except Exception:  # Keep the rest of the application usable without voice extras.
    SarvamAI = None


def _api_key() -> str:
    """Read the key from process env, Streamlit secrets, or the ignored local .env."""
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if key:
        return _clean_key(key)

    try:
        import streamlit as st
        key = str(st.secrets.get("SARVAM_API_KEY", "")).strip()
    except Exception:
        # Streamlit secrets are unavailable when this function is used outside
        # a Streamlit runtime or before a secrets file has been created.
        key = ""
    if key:
        return _clean_key(key)

    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            name, separator, value = candidate.partition("=")
            if separator and name.strip() == "SARVAM_API_KEY":
                return _clean_key(value.strip())
    except OSError:
        pass
    return ""


def _clean_key(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    if value.casefold() in {"your-sarvam-api-key", "your-key", "replace-with-fresh-sarvam-key"}:
        return ""
    return value


def sarvam_configured() -> bool:
    """Return whether a non-placeholder Sarvam key is available."""
    return bool(_api_key())


def _provider_error_message(exc: Exception) -> str:
    """Translate provider/transport failures without exposing request details."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

    if status in {401, 403}:
        return "Sarvam rejected the API key. Check that SARVAM_API_KEY is valid and active."
    if status == 429:
        return "Sarvam is rate-limiting requests. Wait a moment, then retry."
    if status in {400, 413, 415, 422}:
        return ("Sarvam could not read this recording. Record a clear clip under 30 seconds "
                "and try again.")
    if isinstance(status, int) and status >= 500:
        return "Sarvam is temporarily unavailable. Please retry in a moment."
    return "Could not reach Sarvam for transcription. Check your internet connection and retry."


def transcribe_audio(
    audio_file_bytes: bytes,
    filename: str = "audio.wav",
    *,
    language_code: str = "unknown",
) -> str:
    """Transcribe a short audio recording to Hindi, English, or Hinglish text."""
    if not isinstance(audio_file_bytes, (bytes, bytearray)) or not audio_file_bytes:
        raise ValueError("The audio recording is empty. Record it again and retry.")
    if len(audio_file_bytes) > 25 * 1024 * 1024:
        raise ValueError("This recording is too large. Record a shorter clip and retry.")
    try:
        with wave.open(io.BytesIO(audio_file_bytes), "rb") as wav_file:
            duration = wav_file.getnframes() / float(wav_file.getframerate())
        if duration > 30:
            raise ValueError("Recordings must be 30 seconds or shorter. Record a shorter clip and retry.")
    except (wave.Error, EOFError, ZeroDivisionError):
        # Let Sarvam return a useful format error for non-WAV callers.
        pass
    if language_code not in {"unknown", "hi-IN", "en-IN"}:
        raise ValueError("Choose Auto-detect, Hindi, English, or Hinglish for the recording.")

    api_key = _api_key()
    if not api_key:
        raise ValueError(
            'Speech-to-text is not configured. Add SARVAM_API_KEY="your-key" to .env '
            "or SARVAM_API_KEY = \"your-key\" to .streamlit/secrets.toml, then refresh the app."
        )
    if SarvamAI is None:
        raise ValueError("Sarvam AI SDK is not installed. Run `pip install -r requirements.txt` and restart Streamlit.")

    # Streamlit currently records WAV. Keep a safe extension if a supported
    # audio file is passed by another caller in the future.
    suffix = Path(str(filename or "audio.wav")).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".webm", ".flac", ".aac", ".amr"}:
        suffix = ".wav"

    temp_path = None
    try:
        client = SarvamAI(api_subscription_key=api_key)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(audio_file_bytes)
            temp_path = temp_file.name

        with open(temp_path, "rb") as audio_file:
            response = client.speech_to_text.transcribe(
                file=audio_file,
                model="saaras:v4",
                language_code=language_code,
                mode="codemix",
            )

        transcript = getattr(response, "transcript", None)
        if transcript is None and isinstance(response, dict):
            transcript = response.get("transcript")
        transcript = str(transcript or "").strip()
        if not transcript:
            raise ValueError("No speech was detected. Try a louder or longer recording.")
        return transcript
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(_provider_error_message(exc)) from exc
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass