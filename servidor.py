#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
servidor.py — Resuelve streams de video (tipo UnlimPlay) para la TV.

La TV no puede scrapear los agregadores (no manda el header Referer ni los
tokens y además bloquean CORS). Este servidor corre en un ordenador de la casa
y hace el trabajo por ella:

    GET /stream.m3u8?tmdb=ID          -> película (HLS listo para AVPlay)
    GET /stream.m3u8?tmdb=ID&s=1&e=2  -> serie, temporada 1 episodio 2
    GET /seg?u=URL                    -> proxy de un segmento o sublista

Uso:  python3 servidor.py            (escucha en el puerto 8765)
"""
import http.server
import socketserver
import urllib.request
import urllib.parse
import re
import json
import ssl
import os

PUERTO = int(os.environ.get("PORT", "8765"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
BASE = "https://videm.xyz/"
CTX = ssl._create_unverified_context()


def descargar(url, referer):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        return r.read(), r.headers.get("Content-Type", "")


def reescribir(texto, base, servidor):
    """Convierte cada URL de segmento/sublista en una ruta /seg del servidor."""
    lineas = []
    for linea in texto.splitlines():
        s = linea.strip()
        if s and not s.startswith("#"):
            a = urllib.parse.urljoin(base, s)
            lineas.append(servidor + "/seg?u=" + urllib.parse.quote(a, safe=""))
        else:
            lineas.append(linea)
    return "\n".join(lineas) + "\n"


def reescribir_directo(texto, base):
    """Deja los segmentos apuntando directo al CDN (la TV los baja sin pasar por aquí)."""
    lineas = []
    for linea in texto.splitlines():
        s = linea.strip()
        if s and not s.startswith("#"):
            lineas.append(urllib.parse.urljoin(base, s))
        else:
            lineas.append(linea)
    return "\n".join(lineas) + "\n"


def resolver(tmdb, temporada, episodio):
    """Devuelve (contenido_m3u8_master, url_base) del stream resuelto."""
    if temporada and episodio:
        embed = BASE + "embed/tv/" + tmdb + "/" + temporada + "/" + episodio
    else:
        embed = BASE + "embed/movie/" + tmdb
    html, _ = descargar(embed, BASE)
    m = re.search(rb'var Q = (\{.*?\});', html)
    if not m:
        raise RuntimeError("no se pudo leer el reproductor")
    q = json.loads(m.group(1))
    token = q["t"]
    ref = q["ssr"]["servers"][0]["ref"]
    play_url = ("https://videm.xyz/api.php?a=play&ref=" +
                urllib.parse.quote(ref) + "&t=" + urllib.parse.quote(token))
    play_raw, _ = descargar(play_url, embed)
    play = json.loads(play_raw)
    stream = play["url"]
    master, _ = descargar("https://videm.xyz" + stream, embed)
    return master.decode("utf-8", "replace"), "https://videm.xyz" + stream


class Handler(http.server.BaseHTTPRequestHandler):
    def servidor(self):
        return "http://" + self.headers.get("Host", "127.0.0.1:" + str(PUERTO))

    def _enviar_texto(self, body, ct="application/vnd.apple.mpegurl"):
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _enviar_binario(self, body, ct):
        self.send_response(200)
        self.send_header("Content-Type", ct or "application/octet-stream")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/stream.m3u8":
                tmdb = (q.get("tmdb") or [""])[0]
                s = (q.get("s") or [""])[0]
                e = (q.get("e") or [""])[0]
                master, base = resolver(tmdb, s, e)
                self._enviar_texto(reescribir(master, base, self.servidor()))
            elif parsed.path == "/seg":
                u = (q.get("u") or [""])[0]
                if not u:
                    self.send_error(400)
                    return
                body, ct = descargar(u, BASE)
                if body.lstrip().startswith(b"#EXTM3U"):
                    self._enviar_texto(reescribir_directo(body.decode("utf-8", "replace"), u))
                else:
                    self._enviar_binario(body, ct)
            else:
                self.send_error(404)
        except Exception:
            try:
                self.send_error(502)
            except Exception:
                pass

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PUERTO), Handler) as httpd:
        print("Servidor de streams en http://0.0.0.0:%d" % PUERTO)
        httpd.serve_forever()
