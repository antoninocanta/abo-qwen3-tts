#!/usr/bin/env python3
"""Faux moteur Qwen3-TTS, pour eprouver le pool sans carte graphique.

Il imite ce qui compte du binaire reel et rien d'autre : le mode `--serve` qui
ouvre un port et reste vivant, le mode invocation unique qui ecrit un fichier
puis se termine, et une trace de chaque lancement pour que les tests puissent
compter les chargements de poids evites.
"""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WAV = b"RIFF" + b"\x00" * 40

# Chaque lancement est trace : c'est la mesure du test. Un pool qui marche
# lance un processus par voix, pas un par segment.
log = os.getenv("FAKE_QWEN_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\n")

args = sys.argv[1:]

# Un demarrage rate se demande explicitement, pour eprouver le repli. La panne
# ne vise que le mode serveur : c'est exactement le cas ou l'invocation unique
# doit encore sauver la synthese.
if os.getenv("FAKE_QWEN_FAIL_SERVE") == "1" and "--serve" in args:
    sys.exit(3)


def value_of(flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


serve_port = value_of("--serve")

if serve_port:
    # Le moteur reel ouvre son port une fois les poids en place : le delai
    # simule ce chargement, et c'est lui que le pool amortit.
    time.sleep(float(os.getenv("FAKE_QWEN_LOAD_SECONDS", "0.2")))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - impose par BaseHTTPRequestHandler
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(WAV)))
            self.end_headers()
            self.wfile.write(WAV)

        def log_message(self, *_: object) -> None:
            pass

    ThreadingHTTPServer(("127.0.0.1", int(serve_port)), Handler).serve_forever()
    sys.exit(0)

# Invocation unique : produire le fichier attendu, puis rendre la main.
for flag, payload in (("-o", WAV), ("--save-voice", b"qvoice" + b"\x00" * 32)):
    path = value_of(flag)
    if path:
        with open(path, "wb") as handle:
            handle.write(payload)
