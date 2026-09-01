import { useState, useRef, useCallback, useEffect } from "react";

/**
 * useVoiceInput — Web Speech API hook for voice-to-text prompting
 * 
 * Usage:
 *   const { listening, transcript, start, stop, supported } = useVoiceInput({ onResult });
 * 
 * Returns final transcript via onResult callback.
 * Shows interim transcript for live feedback.
 */
export function useVoiceInput({ onResult, lang = "en-US" } = {}) {
  const [listening, setListening]   = useState(false);
  const [interim, setInterim]       = useState("");
  const [error, setError]           = useState(null);
  const recognitionRef              = useRef(null);
  const supported = typeof window !== "undefined" &&
    ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);

  useEffect(() => () => { recognitionRef.current?.abort(); }, []);

  const start = useCallback(() => {
    if (!supported) { setError("Speech recognition not supported in this browser."); return; }
    if (listening) return;

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SR();
    recognitionRef.current = recognition;

    recognition.continuous     = false;
    recognition.interimResults = true;
    recognition.lang           = lang;
    recognition.maxAlternatives = 1;

    recognition.onstart  = () => { setListening(true); setInterim(""); setError(null); };
    recognition.onend    = () => { setListening(false); setInterim(""); };
    recognition.onerror  = (e) => { setListening(false); setError(e.error); setInterim(""); };

    recognition.onresult = (e) => {
      let interimText = "", finalText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else interimText += t;
      }
      setInterim(interimText);
      if (finalText.trim()) onResult?.(finalText.trim());
    };

    recognition.start();
  }, [supported, listening, lang, onResult]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  return { listening, interim, error, start, stop, supported };
}

/**
 * VoiceButton — drop-in mic button component
 */
import React from "react";
import { Mic, MicOff } from "lucide-react";

export function VoiceButton({ onResult, className = "" }) {
  const { listening, interim, error, start, stop, supported } = useVoiceInput({ onResult });

  if (!supported) return null;

  return (
    <div className={`voice-btn-wrap ${className}`}>
      <button
        className={`voice-btn ${listening ? "recording" : ""}`}
        onClick={listening ? stop : start}
        title={listening ? "Stop recording" : "Voice input"}
        aria-label={listening ? "Stop recording" : "Start voice input"}
      >
        {listening ? <MicOff size={15} /> : <Mic size={15} />}
      </button>
      {listening && interim && (
        <span className="voice-interim">{interim}</span>
      )}
      {error && <span className="voice-error">{error}</span>}
    </div>
  );
}
