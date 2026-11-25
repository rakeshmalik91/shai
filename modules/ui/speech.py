"""Speech-to-Text helpers for the Shai Desktop GUI.

Prefers high-accuracy audio capture via ``sounddevice`` + configured cloud
transcription (OpenAI Whisper or Gemini) when available, and falls back to
Windows' built-in ``System.Speech.Recognition.SpeechRecognitionEngine`` via
PowerShell when no cloud speech key or ``sounddevice`` is available.
"""
from __future__ import annotations

import array
import base64
from collections import deque
import http.client
import io
import json
import math
import os
import queue
import subprocess
import sys
import threading
from typing import Callable
import urllib.error
import urllib.request
import uuid
import wave

SAMPLE_RATE = 16000
BLOCK_DURATION_S = 0.1
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION_S)
PRE_ROLL_BLOCKS = 3
SILENCE_END_BLOCKS = 6   # ~0.6s pause ends an utterance
MIN_SPEECH_BLOCKS = 3    # ignore <0.3s transient clicks
MAX_UTTERANCE_BLOCKS = 250  # ~25s max per chunk
MIN_RMS_THRESHOLD = 250.0
MAX_RMS_THRESHOLD = 1200.0


def build_powershell_speech_script(continuous: bool, timeout_seconds: int = 20) -> str:
    """Return a PowerShell script that emits JSON-line speech recognition events."""
    header = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "    Add-Type -AssemblyName System.Speech\n"
        "    $recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()\n"
        "    if (-not $recognizers -or $recognizers.Count -eq 0) {\n"
        "        @{ type = 'error'; error = 'No Windows speech recognizer is installed.' } | ConvertTo-Json -Compress\n"
        "        exit 1\n"
        "    }\n"
        "    $rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine\n"
        "    $rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))\n"
        "    $rec.SetInputToDefaultAudioDevice()\n"
    )
    if continuous:
        body = (
            "    $sourceId = 'ShaiSpeechRecognized'\n"
            "    $null = Register-ObjectEvent -InputObject $rec -EventName SpeechRecognized -SourceIdentifier $sourceId\n"
            "    $rec.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)\n"
            "    while ($true) {\n"
            "        $ev = Wait-Event -SourceIdentifier $sourceId -Timeout 1\n"
            "        if ($null -ne $ev) {\n"
            "            $text = $ev.SourceEventArgs.Result.Text\n"
            "            Remove-Event -EventIdentifier $ev.EventIdentifier\n"
            "            if ($text) {\n"
            "                @{ type = 'text'; text = $text } | ConvertTo-Json -Compress\n"
            "                [Console]::Out.Flush()\n"
            "            }\n"
            "        }\n"
            "    }\n"
        )
    else:
        secs = max(1, int(timeout_seconds))
        body = (
            f"    $result = $rec.Recognize([TimeSpan]::FromSeconds({secs}))\n"
            "    if ($result -and $result.Text) {\n"
            "        @{ type = 'text'; text = $result.Text } | ConvertTo-Json -Compress\n"
            "        [Console]::Out.Flush()\n"
            "    }\n"
            "    @{ type = 'done' } | ConvertTo-Json -Compress\n"
            "    [Console]::Out.Flush()\n"
        )
    footer = (
        "} catch {\n"
        "    @{ type = 'error'; error = $_.Exception.Message } | ConvertTo-Json -Compress\n"
        "    [Console]::Out.Flush()\n"
        "    exit 1\n"
        "}\n"
    )
    return header + body + footer


def encode_powershell_command(script: str) -> str:
    """Encode a PowerShell script as UTF-16LE base64 for ``-EncodedCommand``."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


_SPEECH_CANCEL_PHRASES = frozenset({
    "stop",
    "cancel",
    "abort",
    "halt",
    "stop it",
    "stop now",
    "cancel it",
    "cancel that",
    "never mind",
    "nevermind",
    "shai stop",
    "shai cancel",
})


def is_speech_cancel_command(text: str) -> bool:
    """Return True if transcribed speech is a short stop/cancel command."""
    normalized = " ".join((text or "").strip().lower().split()).strip(".,!?;:\"'")
    return normalized in _SPEECH_CANCEL_PHRASES


_SPEECH_STOP_LISTENING_PHRASES = frozenset({
    "stop listening",
    "mute",
    "mute mic",
    "stop mic",
    "turn off mic",
    "mic off",
    "pause listening",
    "go to sleep",
})


def is_speech_stop_listening_command(text: str) -> bool:
    """Return True if transcribed speech asks ShAI to stop listening."""
    normalized = " ".join((text or "").strip().lower().split()).strip(".,!?;:\"'")
    return normalized in _SPEECH_STOP_LISTENING_PHRASES


def build_powershell_wake_word_script(
    wake_words: tuple[str, ...] = ("Shai", "Hey Shai", "Okay Shai"),
    min_confidence: float = 0.6,
) -> str:
    """Return a PowerShell script that listens locally for wake words."""
    escaped = ", ".join("'" + w.replace("'", "''") + "'" for w in wake_words)
    return (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "    Add-Type -AssemblyName System.Speech\n"
        "    $recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()\n"
        "    if (-not $recognizers -or $recognizers.Count -eq 0) { exit 1 }\n"
        "    $rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine\n"
        f"    $choices = New-Object System.Speech.Recognition.Choices @({escaped})\n"
        "    $gb = New-Object System.Speech.Recognition.GrammarBuilder $choices\n"
        "    $rec.LoadGrammar((New-Object System.Speech.Recognition.Grammar $gb))\n"
        "    $rec.SetInputToDefaultAudioDevice()\n"
        "    $sourceId = 'ShaiWakeWord'\n"
        "    $null = Register-ObjectEvent -InputObject $rec -EventName SpeechRecognized -SourceIdentifier $sourceId\n"
        "    $rec.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)\n"
        "    while ($true) {\n"
        "        $ev = Wait-Event -SourceIdentifier $sourceId -Timeout 1\n"
        "        if ($null -ne $ev) {\n"
        "            $res = $ev.SourceEventArgs.Result\n"
        "            Remove-Event -EventIdentifier $ev.EventIdentifier\n"
        f"            if ($res -and $res.Text -and $res.Confidence -ge {min_confidence:.2f}) {{\n"
        "                @{ type = 'wake'; text = $res.Text } | ConvertTo-Json -Compress\n"
        "                [Console]::Out.Flush()\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "} catch {\n"
        "    exit 1\n"
        "}\n"
    )


class WakeWordSession:
    """Lightweight local wake-word listener using Windows System.Speech."""

    def __init__(
        self,
        wake_words: tuple[str, ...] = ("Shai", "Hey Shai", "Okay Shai"),
        on_wake: Callable[[str], None] | None = None,
    ):
        self.wake_words = wake_words
        self._on_wake = on_wake
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stopped = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return not self._stopped and self._proc is not None and self._proc.poll() is None

    def start(self):
        if sys.platform != "win32":
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None and not self._stopped:
                return
            self._stopped = False
        script = build_powershell_wake_word_script(wake_words=self.wake_words)
        encoded = encode_powershell_command(script)
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except OSError:
            return
        with self._lock:
            self._proc = proc
        threading.Thread(target=self._read_loop, args=(proc,), daemon=True).start()

    def _read_loop(self, proc: subprocess.Popen):
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stopped:
                    break
                event = parse_speech_event(line)
                if event and event.get("type") == "wake" and self._on_wake:
                    self._on_wake(str(event.get("text") or "").strip())
        except Exception:
            pass
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def stop(self):
        with self._lock:
            self._stopped = True
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def parse_speech_event(line: str) -> dict | None:
    """Parse a single JSON-line event emitted by the speech worker."""
    raw = (line or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def pcm_rms(pcm_bytes: bytes) -> float:
    """Compute root-mean-square amplitude for little-endian 16-bit signed PCM."""
    if not pcm_bytes:
        return 0.0
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm_bytes[:usable])
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / len(samples)
    return math.sqrt(mean_sq)


def pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a RIFF/WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


_CUE_TONES: dict[str, tuple[tuple[int, int], ...]] = {
    "start": ((587, 70), (880, 90)),
    "stop": ((659, 70), (440, 95)),
    "send": ((880, 55), (1175, 75)),
}
_CUE_WAV_CACHE: dict[str, bytes] = {}


def build_chime_wav_bytes(
    tones: tuple[tuple[int, int], ...],
    sample_rate: int = SAMPLE_RATE,
    volume: float = 0.30,
) -> bytes:
    """Synthesize a soft multi-tone sine chime as RIFF/WAV bytes."""
    samples = array.array("h")
    amp = max(0.0, min(1.0, volume)) * 32767.0
    for freq, duration_ms in tones:
        n_samples = max(1, int(sample_rate * duration_ms / 1000.0))
        fade = max(1, min(n_samples // 4, int(sample_rate * 0.012)))
        for i in range(n_samples):
            env = 1.0
            if i < fade:
                env = i / float(fade)
            elif i >= n_samples - fade:
                env = (n_samples - 1 - i) / float(fade)
            val = int(amp * env * math.sin(2.0 * math.pi * freq * i / sample_rate))
            samples.append(max(-32768, min(32767, val)))
    return pcm_to_wav_bytes(samples.tobytes(), sample_rate=sample_rate)


def play_speech_cue(cue: str) -> None:
    """Play a non-blocking audio cue ('start', 'stop', or 'send') on Windows."""
    tones = _CUE_TONES.get(cue)
    if not tones or sys.platform != "win32":
        return
    try:
        import winsound

        wav_bytes = _CUE_WAV_CACHE.get(cue)
        if wav_bytes is None:
            wav_bytes = build_chime_wav_bytes(tones)
            _CUE_WAV_CACHE[cue] = wav_bytes

        def _play():
            try:
                winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True).start()
    except Exception:
        pass


def select_speech_backend() -> str:
    """Select the best available speech-to-text backend."""
    if _sounddevice_available() and (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    ):
        return "cloud"
    if sys.platform == "win32":
        return "windows_sapi"
    return "unsupported"


DEFAULT_OPENAI_STT_MODELS = ("whisper-1",)
DEFAULT_GEMINI_STT_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
)
_GEMINI_CONN_LOCK = threading.Lock()
_GEMINI_CONN: http.client.HTTPSConnection | None = None


def _post_gemini_json(path: str, body: bytes, timeout: float = 8.0) -> tuple[int, bytes]:
    """POST JSON to Gemini over a reusable keep-alive HTTPS connection."""
    global _GEMINI_CONN
    with _GEMINI_CONN_LOCK:
        for attempt in range(2):
            if _GEMINI_CONN is None:
                _GEMINI_CONN = http.client.HTTPSConnection(
                    "generativelanguage.googleapis.com", timeout=timeout
                )
            try:
                _GEMINI_CONN.request(
                    "POST",
                    path,
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                resp = _GEMINI_CONN.getresponse()
                return resp.status, resp.read()
            except (http.client.HTTPException, OSError, TimeoutError):
                try:
                    _GEMINI_CONN.close()
                except Exception:
                    pass
                _GEMINI_CONN = None
                if attempt == 1:
                    raise
    raise RuntimeError("Unreachable")


def load_stt_models(project_root=None) -> dict[str, list[str]]:
    """Load configured Speech-to-Text models from model_routing.json."""
    from modules import model_routing
    from modules.engine import agent_runtime as engine

    root = project_root or getattr(engine, "ROOT", None) or "."
    cfg = model_routing.load_routing_config(root).get("speech_to_text", {})
    result = {
        "openai": list(DEFAULT_OPENAI_STT_MODELS),
        "gemini": list(DEFAULT_GEMINI_STT_MODELS),
    }
    if isinstance(cfg, dict):
        for provider in ("openai", "gemini"):
            items = cfg.get(provider)
            if isinstance(items, list):
                cleaned = [str(m).strip() for m in items if isinstance(m, str) and m.strip()]
                if cleaned:
                    result[provider] = cleaned
    return result


def _transcribe_openai(wav_bytes: bytes, api_key: str, models: list[str] | tuple[str, ...] | None = None) -> str:
    last_err: Exception | None = None
    for model_name in (models or DEFAULT_OPENAI_STT_MODELS):
        boundary = f"----ShaiSpeech{uuid.uuid4().hex}"
        parts = [
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"{model_name}\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode("utf-8"),
            wav_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        body = b"".join(parts)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return str(data.get("text") or "").strip()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 429, 500, 502, 503, 504):
                last_err = exc
                continue
            raise
        except (TimeoutError, OSError) as exc:
            last_err = exc
            continue
    assert last_err is not None
    raise last_err


def _transcribe_gemini(wav_bytes: bytes, api_key: str, models: list[str] | tuple[str, ...] | None = None) -> str:
    b64_audio = base64.b64encode(wav_bytes).decode("ascii")
    last_err: Exception | None = None
    data: dict | None = None
    for model_name in (models or DEFAULT_GEMINI_STT_MODELS):
        gen_cfg: dict = {"temperature": 0.0}
        if "lite" not in model_name.lower():
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Transcribe the spoken audio verbatim. "
                                "Output only the exact spoken words with normal punctuation. "
                                "If there is no clear speech or only background noise, output an empty string."
                            )
                        },
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_audio,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": gen_cfg,
        }
        encoded_payload = json.dumps(payload).encode("utf-8")
        path = f"/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            status, raw_body = _post_gemini_json(path, encoded_payload, timeout=8.0)
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            last_err = exc
            continue
        if status in (400, 404, 429, 500, 502, 503, 504):
            last_err = RuntimeError(f"HTTP {status}: {raw_body.decode('utf-8', errors='replace')[:200]}")
            continue
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {raw_body.decode('utf-8', errors='replace')[:200]}")
        data = json.loads(raw_body.decode("utf-8"))
        break
    else:
        assert last_err is not None
        raise last_err

    assert data is not None
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
    return text


def transcribe_wav_bytes(wav_bytes: bytes, project_root=None) -> str:
    """Transcribe WAV audio using OpenAI or Gemini models from model_routing.json."""
    stt_models = load_stt_models(project_root)
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        return _transcribe_openai(wav_bytes, openai_key, models=stt_models["openai"])
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        return _transcribe_gemini(wav_bytes, gemini_key, models=stt_models["gemini"])
    raise RuntimeError("No OPENAI_API_KEY or GEMINI_API_KEY configured for speech transcription.")


class SpeechToTextSession:
    """Manages a background single-use or continuous speech recognition session."""

    def __init__(
        self,
        continuous: bool = False,
        on_text: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ):
        self.continuous = bool(continuous)
        self._on_text = on_text
        self._on_error = on_error
        self._on_done = on_done
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._stopped = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            if self._stopped:
                return False
            if self._proc is not None:
                return self._proc.poll() is None
            return self._running

    def start(self):
        backend = select_speech_backend()
        if backend == "cloud":
            self._start_cloud_session()
            return
        if backend == "windows_sapi":
            self._start_windows_sapi_session()
            return
        if self._on_error:
            self._on_error("Speech-to-text requires sounddevice + an API key, or Windows.")
        if self._on_done:
            self._on_done()

    def _start_cloud_session(self):
        with self._lock:
            self._stopped = False
            self._running = True
            self._stop_event.clear()
        threading.Thread(target=self._cloud_capture_loop, daemon=True).start()

    def _cloud_capture_loop(self):
        import sounddevice as sd

        audio_q: queue.Queue[bytes] = queue.Queue()
        transcribe_q: queue.Queue[bytes | None] = queue.Queue()

        def _callback(indata, _frames, _time_info, status):
            if status:
                return
            audio_q.put(bytes(indata))

        def _transcribe_worker():
            while True:
                wav_bytes = transcribe_q.get()
                if wav_bytes is None:
                    break
                if self._stop_event.is_set():
                    continue
                try:
                    text = transcribe_wav_bytes(wav_bytes)
                    if text and not self._stop_event.is_set() and self._on_text:
                        self._on_text(text)
                        if not self.continuous:
                            self._stop_event.set()
                            break
                except urllib.error.HTTPError as exc:
                    detail = ""
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")[:200]
                    except Exception:
                        pass
                    if not self._stop_event.is_set() and self._on_error:
                        self._on_error(f"Speech API HTTP {exc.code}: {detail or exc.reason}")
                except Exception as exc:
                    if not self._stop_event.is_set() and self._on_error:
                        self._on_error(f"Speech transcription error: {exc}")

        worker = threading.Thread(target=_transcribe_worker, daemon=True)
        worker.start()

        pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_BLOCKS)
        speech_chunks: list[bytes] = []
        speaking = False
        active_blocks = 0
        silent_blocks = 0
        noise_samples: list[float] = []
        threshold = MIN_RMS_THRESHOLD

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                channels=1,
                dtype="int16",
                callback=_callback,
            ):
                while not self._stop_event.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    rms = pcm_rms(chunk)
                    if not speaking and len(noise_samples) < 3:
                        noise_samples.append(rms)
                        avg_noise = sum(noise_samples) / len(noise_samples)
                        threshold = min(MAX_RMS_THRESHOLD, max(MIN_RMS_THRESHOLD, avg_noise * 2.8))
                        pre_roll.append(chunk)
                        continue

                    if rms >= threshold:
                        if not speaking:
                            speaking = True
                            speech_chunks = list(pre_roll)
                            active_blocks = 0
                            silent_blocks = 0
                        speech_chunks.append(chunk)
                        active_blocks += 1
                        silent_blocks = 0
                    elif speaking:
                        speech_chunks.append(chunk)
                        silent_blocks += 1
                        if (
                            silent_blocks >= SILENCE_END_BLOCKS
                            or len(speech_chunks) >= MAX_UTTERANCE_BLOCKS
                        ):
                            if active_blocks >= MIN_SPEECH_BLOCKS:
                                transcribe_q.put(pcm_to_wav_bytes(b"".join(speech_chunks)))
                            speaking = False
                            speech_chunks = []
                            active_blocks = 0
                            silent_blocks = 0
                    else:
                        pre_roll.append(chunk)
        except Exception as exc:
            if not self._stop_event.is_set() and self._on_error:
                self._on_error(f"Microphone capture failed: {exc}")
        finally:
            if speaking and active_blocks >= MIN_SPEECH_BLOCKS and not self._stop_event.is_set():
                transcribe_q.put(pcm_to_wav_bytes(b"".join(speech_chunks)))
            transcribe_q.put(None)
            worker.join(timeout=5)
            with self._lock:
                self._running = False
            if not self._stopped and self._on_done:
                self._on_done()

    def _start_windows_sapi_session(self):
        script = build_powershell_speech_script(continuous=self.continuous)
        encoded = encode_powershell_command(script)
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except OSError as exc:
            if self._on_error:
                self._on_error(f"Could not start speech recognizer: {exc}")
            if self._on_done:
                self._on_done()
            return

        with self._lock:
            self._proc = proc
            self._stopped = False
            self._running = True

        threading.Thread(target=self._read_loop, args=(proc,), daemon=True).start()

    def _read_loop(self, proc: subprocess.Popen):
        saw_error = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stopped:
                    break
                event = parse_speech_event(line)
                if not event:
                    continue
                ev_type = event.get("type")
                if ev_type == "text":
                    text = str(event.get("text") or "").strip()
                    if text and self._on_text:
                        self._on_text(text)
                elif ev_type == "error":
                    saw_error = True
                    msg = str(event.get("error") or "Speech recognition failed.").strip()
                    if self._on_error:
                        self._on_error(msg)
                elif ev_type == "done":
                    break
        except Exception as exc:
            if not self._stopped and self._on_error:
                saw_error = True
                self._on_error(f"Speech recognition error: {exc}")
        finally:
            try:
                rc = proc.wait(timeout=1)
            except Exception:
                rc = None
            if not self._stopped and not saw_error and rc not in (0, None):
                stderr_text = ""
                try:
                    if proc.stderr is not None:
                        stderr_text = proc.stderr.read().strip()
                except Exception:
                    pass
                if self._on_error:
                    self._on_error(stderr_text or f"Speech recognizer exited with code {rc}.")
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                self._running = False
            if not self._stopped and self._on_done:
                self._on_done()

    def stop(self):
        with self._lock:
            self._stopped = True
            self._running = False
            self._stop_event.set()
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
