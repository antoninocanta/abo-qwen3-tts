#!/usr/bin/env python3
"""Faux moteur, pour eprouver l'agent sans carte graphique ni poids a charger.

Il imite du contrat reel ce qui compte pour l'agent, et rien d'autre : `/health`
et `/synthesize` qui rend un WAV valide en base64. Une seconde d'onde, pas du
silence — une mesure de bout en bout doit porter une verification de contenu,
et un fichier vide passerait tous les controles de format.
"""
import base64
import io
import json
import math
import os
import struct
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv("FAKE_ENGINE_PORT", "18100"))
# Un echec a la demande, pour eprouver le chemin de reprise du backend.
FAIL = os.getenv("FAKE_ENGINE_FAIL") == "1"


def tone(seconds: float = 1.0, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(index * 0.05)))
                for index in range(int(rate * seconds))
            )
        )
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - impose par http.server
        if self.path == "/health":
            self._send(200, {"status": "ok", "engine": True})
        else:
            self._send(404, {"error": "inconnu"})

    def do_POST(self) -> None:  # noqa: N802 - impose par http.server
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")

        if self.path != "/synthesize":
            self._send(404, {"error": "inconnu"})
            return
        if FAIL:
            self._send(502, {"error": "moteur en panne, volontairement"})
            return
        if not request.get("text"):
            self._send(422, {"error": "texte vide"})
            return

        audio = tone()
        self._send(
            200,
            {
                "audio_b64": base64.b64encode(audio).decode(),
                "format": "wav",
                "size_bytes": len(audio),
                "engine": "fake",
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        print("fake-engine " + (fmt % args), flush=True)


if __name__ == "__main__":
    print(f"fake-engine sur :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
