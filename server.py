#!/usr/bin/env python3
"""Saimo OTT — servidor de teste (só stdlib).

Serve o front (app.html) + API JSON em cima de saimo.db:

  python3 server.py [--port 8000]
  -> http://localhost:8000/app.html

API:
  GET /api/stats
  GET /api/canais?q=&limit=&offset=
  GET /api/resolve-canal?nome=
  GET /api/filmes?q=&limit=&offset=          (com poster/gêneros da ficha)
  GET /api/resolve-filme?titulo=&versao=dub
  GET /api/series?q=&limit=&offset=
  GET /api/serie?titulo=                     (temporadas + Versões)
  GET /api/episodios?titulo=&temp=&versao=dub
  GET /api/baixar?tipo=filme|serie&titulo=&versao=dub[&temp=&ep=]
      (sonda as fontes, escolhe a maior e redireciona 302 p/ download)
  GET /api/radios?q=&limit=&offset=
"""
import argparse
import json
import sqlite3
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import saimo

LIMITE_MAX = 200


def jdump(o: str) -> bytes:
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


def match_titulos(con, tabela: str, qq: str, lim: int = 500, col: str = "titulo") -> list:
    """LIKE primeiro; se vazio, compara sem acento/maiúsculas (ex: dragao=dragão)."""
    rows = con.execute(f"SELECT DISTINCT {col} FROM {tabela} WHERE {col} LIKE ? LIMIT ?",
                       (f"%{qq}%", lim)).fetchall()
    if rows:
        return [r[0] for r in rows]
    nq = saimo.norm_nome(qq)
    if not nq:
        return []
    return [t for (t,) in con.execute(f"SELECT DISTINCT {col} FROM {tabela}").fetchall()
            if nq in saimo.norm_nome(t)][:lim]


def resolve_titulo(con, tabela: str, cols: str, nome: str):
    row = con.execute(f"SELECT {cols} FROM {tabela} WHERE titulo=?", (nome,)).fetchone()
    if row:
        return row
    m = match_titulos(con, tabela, nome, 1)
    if m:
        return con.execute(f"SELECT {cols} FROM {tabela} WHERE titulo=?", (m[0],)).fetchone()
    return None


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, status=200):
        body = jdump(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/api/stats":
            return self.api_stats()
        if u.path == "/api/canais":
            return self.api_canais(q)
        if u.path == "/api/resolve-canal":
            return self.api_resolve_canal(q)
        if u.path == "/api/filmes":
            return self.api_filmes(q)
        if u.path == "/api/resolve-filme":
            return self.api_resolve_filme(q)
        if u.path == "/api/series":
            return self.api_series(q)
        if u.path == "/api/serie":
            return self.api_serie(q)
        if u.path == "/api/episodios":
            return self.api_episodios(q)
        if u.path == "/api/baixar":
            return self.api_baixar(q)
        if u.path == "/api/radios":
            return self.api_radios(q)
        if u.path == "/":
            self.path = "/app.html"
        return super().do_GET()

    def pag(self, q):
        try:
            lim = min(int(q.get("limit", 60)), LIMITE_MAX)
        except ValueError:
            lim = 60
        try:
            off = max(int(q.get("offset", 0)), 0)
        except ValueError:
            off = 0
        return lim, off

    def api_stats(self):
        con = saimo.connect()
        out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("canais", "filmes", "series", "episodios", "radios")}
        con.close()
        self.send_json(out)

    def api_canais(self, q):
        con = saimo.connect()
        lim, off = self.pag(q)
        if q.get("q"):
            nomes = match_titulos(con, "canais", q["q"], col="nome")
            page = nomes[off:off + lim]
            ordem = {n: i for i, n in enumerate(page)}
            rows = con.execute(
                f"SELECT nome, logo, categoria FROM canais WHERE nome IN "
                f"({','.join('?' * len(page))})" if page else
                "SELECT nome, logo, categoria FROM canais WHERE 0", page).fetchall()
            rows.sort(key=lambda r: ordem.get(r[0], 0))
        else:
            rows = con.execute(
                "SELECT nome, logo, categoria FROM canais ORDER BY nome LIMIT ? OFFSET ?",
                (lim, off)).fetchall()
        con.close()
        self.send_json([{"nome": n, "logo": l, "categoria": c} for n, l, c in rows])

    def api_resolve_canal(self, q):
        nome = q.get("nome", "")
        con = saimo.connect()
        row = con.execute("SELECT id, nome, logo, categoria FROM canais WHERE nome=?",
                          (nome,)).fetchone()
        if not row:
            m = match_titulos(con, "canais", nome, 1, col="nome")
            if m:
                row = con.execute("SELECT id, nome, logo, categoria FROM canais WHERE nome=?",
                                  (m[0],)).fetchone()
        if not row:
            sug = con.execute("SELECT nome FROM canais WHERE nome LIKE ? LIMIT 8",
                              (f"%{nome}%",)).fetchall()
            con.close()
            return self.send_json({"erro": "não encontrado",
                                   "sugestoes": [r[0] for r in sug]}, 404)
        cid, nome, logo, cat = row
        off = saimo.off_set(con)
        fontes = con.execute(
            "SELECT url, nome, qualidade, referer, user_agent, key_id, key "
            "FROM canal_fontes WHERE canal_id=? ORDER BY ordem", (cid,)).fetchall()
        con.close()
        out, vistas = [], set()
        for url, fn, qual, ref, ua, kid, key in fontes:
            if not url or url in vistas or saimo.host_of(url) in off:
                continue
            vistas.add(url)
            f = saimo.fonte_player(url, ref, ua, kid, key)
            f["fonte_nome"], f["qualidade"] = fn, qual
            out.append(f)
        self.send_json({"canal": nome, "logo": logo, "categoria": cat, "fontes": out})

    def api_filmes(self, q):
        con = saimo.connect()
        lim, off = self.pag(q)
        if q.get("q"):
            titulos = match_titulos(con, "filmes", q["q"])
            page = titulos[off:off + lim]
            ordem = {t: i for i, t in enumerate(page)}
            rows = con.execute(
                "SELECT m.titulo, f.poster, f.generos, f.tmdb_id FROM filmes m "
                "LEFT JOIN fichas f ON f.tipo='f' AND f.titulo=m.titulo "
                f"WHERE m.titulo IN ({','.join('?' * len(page))})" if page else
                "SELECT m.titulo, f.poster, f.generos, f.tmdb_id FROM filmes m "
                "LEFT JOIN fichas f ON f.tipo='f' AND f.titulo=m.titulo WHERE 0",
                page).fetchall()
            rows.sort(key=lambda r: ordem.get(r[0], 0))
        else:
            rows = con.execute(
                "SELECT m.titulo, f.poster, f.generos, f.tmdb_id FROM filmes m "
                "LEFT JOIN fichas f ON f.tipo='f' AND f.titulo=m.titulo "
                "ORDER BY m.titulo LIMIT ? OFFSET ?", (lim, off)).fetchall()
        con.close()
        self.send_json([{"titulo": t, "poster": p, "generos": g, "tmdb_id": i}
                        for t, p, g, i in rows])

    def api_resolve_filme(self, q):
        titulo, versao = q.get("titulo", ""), q.get("versao", "dub")
        con = saimo.connect()
        real, por = saimo.filme_fontes(con, titulo)
        con.close()
        if real is None:
            return self.send_json({"erro": "não encontrado"}, 404)
        self.send_json({"titulo": real, "versao_pedida": versao,
                        "versoes": sorted(por),
                        "fontes": por.get(versao) or por.get("dub") or [],
                        **(saimo.ficha_de("f", real) or {})})

    def api_series(self, q):
        con = saimo.connect()
        lim, off = self.pag(q)
        if q.get("q"):
            titulos = match_titulos(con, "series", q["q"])
            page = titulos[off:off + lim]
            ordem = {t: i for i, t in enumerate(page)}
            rows = con.execute(
                "SELECT m.titulo, m.ano, f.poster, f.generos FROM series m "
                "LEFT JOIN fichas f ON f.tipo='s' AND f.titulo=m.titulo "
                f"WHERE m.titulo IN ({','.join('?' * len(page))})" if page else
                "SELECT m.titulo, m.ano, f.poster, f.generos FROM series m "
                "LEFT JOIN fichas f ON f.tipo='s' AND f.titulo=m.titulo WHERE 0",
                page).fetchall()
            rows.sort(key=lambda r: ordem.get(r[0], 0))
        else:
            rows = con.execute(
                "SELECT m.titulo, m.ano, f.poster, f.generos FROM series m "
                "LEFT JOIN fichas f ON f.tipo='s' AND f.titulo=m.titulo "
                "ORDER BY m.titulo LIMIT ? OFFSET ?", (lim, off)).fetchall()
        con.close()
        self.send_json([{"titulo": t, "ano": a, "poster": p, "generos": g}
                        for t, a, p, g in rows])

    def api_serie(self, q):
        titulo = q.get("titulo", "")
        con = saimo.connect()
        row = resolve_titulo(con, "series", "id, titulo, ano", titulo)
        if not row:
            con.close()
            return self.send_json({"erro": "não encontrada"}, 404)
        sid, real, ano = row
        temps = con.execute("SELECT DISTINCT temporada FROM episodios WHERE serie_id=? "
                            "ORDER BY temporada", (sid,)).fetchall()
        vers = con.execute("SELECT DISTINCT versao FROM episodios WHERE serie_id=?",
                           (sid,)).fetchall()
        con.close()
        self.send_json({"titulo": real, "ano": ano,
                        "temporadas": [t[0] for t in temps],
                        "versoes": [v[0] for v in vers],
                        **(saimo.ficha_de("s", real) or {})})

    def api_episodios(self, q):
        titulo, versao = q.get("titulo", ""), q.get("versao", "dub")
        try:
            temp = int(q.get("temp", 1))
        except ValueError:
            return self.send_json({"erro": "temp inválida"}, 400)
        con = saimo.connect()
        off = saimo.off_set(con)
        row = resolve_titulo(con, "series", "id", titulo)
        if not row:
            con.close()
            return self.send_json({"erro": "não encontrada"}, 404)
        sid = row[0]
        rows = con.execute("SELECT numero, versao, url FROM episodios WHERE serie_id=? "
                           "AND temporada=? ORDER BY numero, ordem", (sid, temp)).fetchall()
        con.close()
        eps: dict[int, dict] = {}
        for num, ver, url in rows:
            if saimo.host_of(url) in off:
                continue
            eps.setdefault(num, {}).setdefault(ver, []).append(saimo.fonte_player(url))
        out = [{"numero": n, "versoes": sorted(v),
                "fontes": v.get(versao) or v.get("dub") or next(iter(v.values()))}
               for n, v in sorted(eps.items())]
        self.send_json({"titulo": titulo, "temporada": temp,
                        "versao_pedida": versao, "episodios": out})

    def api_baixar(self, q):
        """Escolhe a maior fonte (Content-Length) e redireciona para download."""
        tipo, titulo, versao = q.get("tipo", "filme"), q.get("titulo", ""), q.get("versao", "dub")
        con = saimo.connect()
        if tipo == "serie":
            try:
                temp, ep = int(q.get("temp", 1)), int(q.get("ep", 1))
            except ValueError:
                con.close()
                return self.send_json({"erro": "temp/ep inválidos"}, 400)
            real, fontes = saimo.episodio_fontes(con, titulo, temp, ep, versao)
            rot = f"{real} S{temp:02d}E{ep:02d} [{versao}]" if real else titulo
        else:
            real, por = saimo.filme_fontes(con, titulo)
            fontes = por.get(versao) or por.get("dub") or []
            rot = f"{real} [{versao}]" if real else titulo
        con.close()
        if not fontes:
            return self.send_json({"erro": "sem fonte ativa"}, 404)
        _f, dest, tam = saimo.escolher_melhor(fontes)
        if not dest:
            return self.send_json({"erro": "sem URL final"}, 404)
        self.send_response(302)
        self.send_header("Location", dest)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{saimo.nome_arquivo(rot)}.mp4"')
        self.end_headers()

    def api_radios(self, q):
        con = saimo.connect()
        lim, off = self.pag(q)
        if q.get("q"):
            rows = con.execute("SELECT nome, url, logo FROM radios WHERE nome LIKE ? "
                               "ORDER BY nome LIMIT ? OFFSET ?",
                               (f"%{q['q']}%", lim, off)).fetchall()
        else:
            rows = con.execute("SELECT nome, url, logo FROM radios ORDER BY nome "
                               "LIMIT ? OFFSET ?", (lim, off)).fetchall()
        con.close()
        self.send_json([{"nome": n, "url": u, "logo": l} for n, u, l in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"Saimo Scraper em http://localhost:{a.port}/app.html")
    srv.serve_forever()


if __name__ == "__main__":
    main()
