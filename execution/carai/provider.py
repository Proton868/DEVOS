"""Provider-neutral VoiceProvider contract. Carai is the DevOS name for this runtime."""
from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TranscriptChunk:
    text: str
    is_final: bool = True
    confidence: Optional[float] = None
    language: Optional[str] = None


@dataclass
class SynthesisResult:
    audio: bytes = b""
    content_type: str = "audio/mpeg"
    provider: str = "none"
    error: Optional[str] = None


class VoiceProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm", language: str = "en") -> TranscriptChunk:
        ...

    @abc.abstractmethod
    def synthesize(self, text: str, *, voice: Optional[str] = None) -> SynthesisResult:
        ...

    def health(self) -> dict:
        return {"provider": self.name, "configured": False, "stt": False, "tts": False}


class NullVoiceProvider(VoiceProvider):
    """Fail-closed when no provider credentials are configured."""
    name = "null"

    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm", language: str = "en") -> TranscriptChunk:
        raise RuntimeError("VOICE_PROVIDER_NOT_CONFIGURED")

    def synthesize(self, text: str, *, voice: Optional[str] = None) -> SynthesisResult:
        return SynthesisResult(error="VOICE_PROVIDER_NOT_CONFIGURED", provider=self.name)

    def health(self) -> dict:
        return {"provider": self.name, "configured": False, "stt": False, "tts": False, "telephony": False}


class BrowserDelegatedProvider(VoiceProvider):
    """
    Server acknowledges browser Web Speech / MediaRecorder pipeline.
    STT/TTS happen client-side; server stores transcripts only.
    """
    name = "browser_delegated"

    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm", language: str = "en") -> TranscriptChunk:
        # Server does not decode browser audio without a cloud STT key.
        raise RuntimeError("BROWSER_STT_CLIENT_SIDE")

    def synthesize(self, text: str, *, voice: Optional[str] = None) -> SynthesisResult:
        return SynthesisResult(
            audio=b"",
            content_type="text/plain",
            provider=self.name,
            error="BROWSER_TTS_CLIENT_SIDE",
        )

    def health(self) -> dict:
        return {
            "provider": self.name,
            "configured": True,
            "stt": "client",
            "tts": "client",
            "telephony": False,
            "note": "Browser Web Speech / speechSynthesis; not telephony",
        }


def get_voice_provider() -> VoiceProvider:
    kind = (os.environ.get("DEVOS_VOICE_PROVIDER") or "browser_delegated").strip().lower()
    if kind in ("null", "none", "off"):
        return NullVoiceProvider()
    if kind in ("browser", "browser_delegated", "carai"):
        return BrowserDelegatedProvider()
    # Unknown provider names fail closed rather than inventing APIs
    return NullVoiceProvider()
