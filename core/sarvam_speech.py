"""Sarvam AI Speech-to-Text integration using official SDK."""
import os
import tempfile
from sarvamai import SarvamAI

def transcribe_audio(audio_file_bytes: bytes, filename="audio.wav") -> str:
    """Send recorded audio bytes to Sarvam AI STT API using official SDK."""
    api_key = os.environ.get("SARVAM_API_KEY", "sk_oof9rn6g_9b1WoNX6oJkGGHdM3nZpmDhU").strip()
    if not api_key:
        raise ValueError("Sarvam API key is missing.")

    temp_file_path = None
    try:
        # Initialize Sarvam client
        client = SarvamAI(api_subscription_key=api_key)
        
        # Write audio bytes to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(audio_file_bytes)
            temp_file_path = temp_file.name

        try:
            with open(temp_file_path, "rb") as f:
                # Using official SDK transcribe method with supported model
                response = client.speech_to_text.transcribe(
                    file=f,
                    model="saaras:v3",
                    language_code="hi-IN",
                    mode="transcribe"
                )
            
            # Extract transcript safely
            if hasattr(response, "transcript"):
                return response.transcript
            elif isinstance(response, dict):
                return response.get("transcript", "")
            return str(response)
            
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass

    except Exception as exc:
        raise ValueError(f"Sarvam AI SDK Error: {exc}")