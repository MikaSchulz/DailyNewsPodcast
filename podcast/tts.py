"""Synthesizes the podcast script to an MP3 via Google Cloud Text-to-Speech."""

import logging
import re

from google.cloud import texttospeech

logger = logging.getLogger(__name__)

# Google TTS caps requests at 5000 bytes of input; stay comfortably under that
# so multi-byte German umlauts don't push a chunk over the limit.
MAX_CHUNK_BYTES = 4500


def synthesize(script: str, config: dict, output_path: str) -> str:
    chunks = _chunk_text(script, MAX_CHUNK_BYTES)
    logger.info("Synthesizing %d chunk(s) of speech.", len(chunks))

    try:
        client = texttospeech.TextToSpeechClient()
    except Exception as exc:
        raise RuntimeError(
            f"Could not create Google Cloud TTS client. Check GOOGLE_APPLICATION_CREDENTIALS. Details: {exc}"
        ) from exc

    tts_cfg = config["tts"]
    voice = texttospeech.VoiceSelectionParams(
        language_code=tts_cfg["language_code"],
        name=tts_cfg["voice_name"],
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=tts_cfg.get("speaking_rate", 1.0),
    )

    audio_bytes = bytearray()
    for i, chunk in enumerate(chunks, start=1):
        try:
            response = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=chunk),
                voice=voice,
                audio_config=audio_config,
            )
        except Exception as exc:
            raise RuntimeError(f"Google TTS call failed on chunk {i}/{len(chunks)}: {exc}") from exc
        audio_bytes.extend(response.audio_content)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    logger.info("Wrote audio to %s (%d bytes).", output_path, len(audio_bytes))
    return output_path


def _chunk_text(text: str, max_bytes: int) -> list[str]:
    """Splits text into chunks under max_bytes, breaking on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate.encode("utf-8")) > max_bytes and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
