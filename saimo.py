#!/usr/bin/env python3
"""Saimo TV — scraper + resolver + SQLite.

Uso:
  python3 saimo.py sync [--sheets]        baixa tudo e monta saimo.db
  python3 saimo.py stats                  resumo do banco
  python3 saimo.py canais [--busca X]     lista canais
  python3 saimo.py resolve-canal "Globo"  fontes diretas + proxy (JSON)
  python3 saimo.py resolve-filme "A Teia" [--versao dub|leg]
  python3 saimo.py resolve-serie "A Casa do Dragão" --temp 1 --ep 1 [--versao dub|leg]
  python3 saimo.py m3u --saida saimo.m3u   playlist p/ player personalizado
  python3 saimo.py radio --busca X         lista rádios

Só stdlib (urllib + sqlite3). Sem dependências.
"""
import argparse
import json
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "saimo.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

CANAIS_BASE = "https://canais-saimo.pages.dev/"
FILMES_BASE = "https://filmes-saimo.pages.dev/"
PROXY_BASE = "https://saimo-tv.pages.dev"
DESATIVADAS_URL = "https://saimo-tv.pages.dev/fontes-desativadas.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/126.0"
TIMEOUT = 30

HARD_OFF = {
    "123flmsfree.com", "vod-limit-media", "froststream.cloutteam.com",
    "cloutteam.com", "praia13.com", "pub-bda823ef358c448ea310cdf9539f9628.r2.dev",
    "cdn.hermesnewone.com", "speed2.megafilmeshd9.com",
    "cdn-sv01.maximaimg.online", "strm15.uqload.vc", "megaembed.com",
    "clickhost.xyz", "playercdnx.workers.dev", "146.0.32.89",
    "corruptx.org", "governosptx.org", "desativado.invalid",
}

LETRAS = ["#"] + [chr(c) for c in range(65, 91)]


def fetch_text(url: str, headers: dict | None = None) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            raw = r.read()
            for enc in ("utf-8", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! {url}: {e}", file=sys.stderr)
        return None


def norm_nome(s: str) -> str:
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"^[A-Z]{2}\s*[-|]\s*", "", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    toks = re.sub(r"[^a-z0-9]+", " ", s).strip().split()
    while len(toks) > 1 and toks[-1] in ("hd", "sd", "fhd", "uhd", "4k", "br"):
        toks.pop()
    return " ".join(toks)


def parse_catalogo(text: str) -> list:
    """Formato chave: valor (canal/logo/categoria/fonte/nome/qualidade/referer/agente/chave)."""
    canais, cur = [], None

    def push():
        if cur and cur["sources"]:
            canais.append(cur)

    for line in text.split("\n"):
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        i = t.find(":")
        if i <= 0:
            continue
        k, v = t[:i].strip().lower(), t[i + 1:].strip()
        if not v:
            continue
        if k == "canal":
            push()
            cur = {"name": v, "logo": None, "group": None, "sources": []}
        elif cur is None:
            continue
        elif k == "logo":
            cur["logo"] = v
        elif k == "categoria":
            cur["group"] = v
        elif k == "fonte":
            cur["sources"].append({"url": v})
        elif k in ("referer", "nome", "qualidade", "agente"):
            if cur["sources"]:
                cur["sources"][-1][{"referer": "referer", "nome": "name",
                                    "qualidade": "quality", "agente": "userAgent"}[k]] = v
        elif k == "chave":
            parts = v.split(":")
            if len(parts) == 2 and all(p.strip() for p in parts):
                if cur["sources"]:
                    cur["sources"][-1]["keyId"] = parts[0].strip()
                    cur["sources"][-1]["key"] = parts[1].strip()
            else:
                if cur["sources"]:
                    cur["sources"].pop()
    push()
    return canais


def parse_m3u(text: str) -> list:
    out, name = [], None
    logo = group = None
    for line in text.split("\n"):
        t = line.strip()
        if not t or t == "#EXTM3U":
            continue
        if t.startswith("#EXTINF:"):
            name = (re.search(r'tvg-id="([^"]+)"', t) or [None, None])[1]
            logo = (re.search(r'tvg-logo="([^"]+)"', t) or [None, None])[1]
            group = (re.search(r'group-title="([^"]+)"', t) or [None, None])[1]
            if not name:
                quoted, pos = False, -1
                for j, ch in enumerate(t):
                    if ch == '"':
                        quoted = not quoted
                    elif ch == "," and not quoted:
                        pos = j
                name = t[pos + 1:].strip() if pos >= 0 else t.strip()
                name = re.sub(r"\s*\([^)]+\)$", "", name).strip()
        elif not t.startswith("#") and name:
            out.append({"name": name, "logo": logo, "group": group,
                        "sources": [{"url": t}]})
            name = None
    return out


def merge_listas(base: list, extra: list) -> list:
    """canais.txt soma fontes ao catalogo.txt (igual ao site: ym)."""
    idx = {norm_nome(c["name"]): c for c in extra}
    seen, out = set(), []
    for c in base:
        k = norm_nome(c["name"])
        e = idx.get(k)
        if not e:
            out.append(c)
            continue
        seen.add(k)
        urls = {s["url"] for s in c["sources"]}
        novas = [s for s in e["sources"] if s["url"] not in urls]
        out.append({**c, "group": c["group"] or e["group"],
                    "logo": c["logo"] or e["logo"],
                    "sources": c["sources"] + novas if novas else c["sources"]})
    return out + [c for c in extra if norm_nome(c["name"]) not in seen]


def expand_base(ref: str, bases: dict) -> str:
    ref = ref.strip()
    if ref.startswith("http"):
        return ref
    i = ref.find(":")
    if i <= 0:
        return ""
    try:
        n = int(ref[:i])
    except ValueError:
        return ""
    pre = bases.get(n)
    if not pre:
        return ""
    rest = ref[i + 1:]
    url = pre + rest if "." in rest else pre + rest + ".mp4"
    return url


def host_of(url: str) -> str | None:
    try:
        return urllib.parse.urlparse(url).hostname.lower()
    except Exception:
        return None


def tipo_midia(url: str) -> str:
    u = url.lower().split("?")[0]
    if ".mpd" in u:
        return "dash"
    if ".m3u8" in u or u.endswith(".txt") or "dadinhodetapioca.fun" in u:
        return "hls"
    if re.search(r"\.ts(\?|$)", u):
        return "mpegts"
    return "mp4"


def precisa_proxy(url: str) -> bool:
    if "s23-cloudfront-net.lat" in url or "s25-cloudfront-net.lat" in url:
        return False
    low = url.lower()
    return url.startswith("http://") or low.split("?")[0].endswith(".txt") \
        or "dadinhodetapioca.fun" in low


def proxy_url(url: str, referer: str | None = None, ua: str | None = None) -> str:
    q = {"url": url}
    if referer:
        q["referer"] = referer
    if ua:
        q["ua"] = ua
    return f"{PROXY_BASE}/api/proxy?{urllib.parse.urlencode(q)}"


def final_url(url: str) -> str:
    """Resolve /api/stream (302) até a mídia final sem baixar o corpo."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            dest = r.geturl()
            if dest != url:
                return dest
            if "api/stream" in url:
                raise ValueError("retry-get")
            return dest
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.geturl()
    except Exception as e:
        print(f"  ! resolve {url}: {e}", file=sys.stderr)
        return url


def tamanho_url(url: str, headers: dict | None = None) -> int:
    """Tamanho em bytes via HEAD ou Range 0-0; 0 se desconhecido."""
    hdr = {"User-Agent": UA, **(headers or {})}
    for method, extra in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        try:
            req = urllib.request.Request(url, headers={**hdr, **extra}, method=method)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if method == "HEAD":
                    n = r.headers.get("Content-Length")
                    if n and n.isdigit():
                        return int(n)
                else:
                    cr = r.headers.get("Content-Range") or ""
                    if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        return int(cr.rsplit("/", 1)[1])
                    if r.status == 200:
                        n = r.headers.get("Content-Length")
                        if n and n.isdigit():
                            return int(n)
        except Exception:
            pass
    return 0


def escolher_melhor(fontes: list) -> tuple:
    """(fonte, url_final, tamanho) — maior Content-Length; api/stream resolvida antes."""
    melhor, url_melhor, tam_melhor = None, None, -1
    for f in fontes:
        dest = final_url(f["url"]) if "api/stream" in f["url"] else f["url"]
        t = tamanho_url(dest, f.get("headers")) if dest else 0
        if melhor is None or t > tam_melhor:
            melhor, url_melhor, tam_melhor = f, dest, t
    return melhor, url_melhor, max(tam_melhor, 0)


def variante_top(master_url: str, headers: dict | None = None) -> str | None:
    """Maior variante (RESOLUTION, desempate BANDWIDTH) de um master HLS."""
    txt = fetch_text(master_url, headers)
    if not txt or "#EXT-X-STREAM-INF" not in txt:
        return None
    linhas, var = txt.splitlines(), []
    for i, ln in enumerate(linhas):
        if ln.startswith("#EXT-X-STREAM-INF") and i + 1 < len(linhas):
            at = dict(re.findall(r'([A-Z-]+)=("[^"]*"|[^,]*)', ln))
            bw = int(at.get("BANDWIDTH", 0) or 0)
            w = h = 0
            if "x" in at.get("RESOLUTION", ""):
                try:
                    w, h = map(int, at["RESOLUTION"].split("x"))
                except ValueError:
                    pass
            var.append((w * h, bw, linhas[i + 1].strip()))
    if not var:
        return None
    var.sort(reverse=True)
    return urllib.parse.urljoin(master_url, var[0][2])


def baixar_url(url: str, path: Path, headers: dict | None = None, max_bytes: int = 0):
    hdr = {"User-Agent": UA, **(headers or {})}
    tmp = path.with_suffix(path.suffix + ".part")
    ja = tmp.stat().st_size if tmp.exists() else 0
    if ja:
        hdr["Range"] = f"bytes={ja}-"
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(tmp, "ab") as f:
        cr = r.headers.get("Content-Range") or ""
        total = int(cr.rsplit("/", 1)[1]) if "/" in cr and cr.rsplit("/", 1)[1].isdigit() else 0
        feito = ja
        while True:
            bl = r.read(1 << 20)
            if not bl:
                break
            f.write(bl)
            feito += len(bl)
            if total:
                print(f"\r  {feito / 1e6:.1f}/{total / 1e6:.0f} MB ({100 * feito / total:.0f}%)",
                      end="", flush=True)
            else:
                print(f"\r  {feito / 1e6:.1f} MB", end="", flush=True)
            if max_bytes and feito - ja >= max_bytes:
                break
    print()
    if max_bytes and tmp.stat().st_size - ja >= max_bytes:
        print(f"amostra ({max_bytes / 1e6:.0f} MB) -> {tmp} (sem renomear)")
        return tmp
    tmp.rename(path)
    print(f"OK -> {path}")
    return path


def baixar_hls(url: str, path: Path, headers: dict | None = None, max_bytes: int = 0):
    """Baixa HLS .ts (concatena segmentos); sem suporte a EXT-X-KEY."""
    hdr = dict(headers or {})
    master = fetch_text(url, {"User-Agent": UA, **hdr}) or ""
    if "#EXT-X-STREAM-INF" in master:
        top = variante_top(url, {"User-Agent": UA, **hdr})
        if top:
            print(f"  variante: {top}")
            url = top
    txt = fetch_text(url, {"User-Agent": UA, **hdr}) or ""
    if "#EXT-X-KEY" in txt:
        print("  ! playlist criptografada (EXT-X-KEY), sem suporte")
        return None
    segs = [urllib.parse.urljoin(url, ln.strip()) for ln in txt.splitlines()
            if ln.strip() and not ln.startswith("#")]
    if not segs:
        print("  ! nenhum segmento na playlist")
        return None
    print(f"  {len(segs)} segmentos")
    tmp = path.with_suffix(".ts.part")
    feito = 0
    with open(tmp, "wb") as f:
        for i, s in enumerate(segs, 1):
            req = urllib.request.Request(s, headers={"User-Agent": UA, **hdr})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                while True:
                    bl = r.read(1 << 20)
                    if not bl:
                        break
                    f.write(bl)
                    feito += len(bl)
                    if max_bytes and feito >= max_bytes:
                        break
            print(f"\r  seg {i}/{len(segs)} · {feito / 1e6:.0f} MB", end="", flush=True)
            if max_bytes and feito >= max_bytes:
                break
    print()
    tmp.rename(path.with_suffix(".ts"))
    print(f"OK -> {path.with_suffix('.ts')}")
    return path.with_suffix(".ts")


def nome_arquivo(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip()[:150]


def _acha_titulo(con, tabela: str, titulo: str):
    row = con.execute(f"SELECT id, titulo FROM {tabela} WHERE titulo=?",
                      (titulo,)).fetchone()
    if row:
        return row
    row = con.execute(f"SELECT id, titulo FROM {tabela} WHERE titulo LIKE ?",
                      (f"%{titulo}%",)).fetchone()
    if row:
        return row
    nq = norm_nome(titulo)
    if nq:
        for sid, t in con.execute(f"SELECT id, titulo FROM {tabela}").fetchall():
            if nq in norm_nome(t):
                return sid, t
    return None


def filme_fontes(con, titulo: str):
    """(titulo_real, {versao: [fonte_player]}) com desativados filtrados."""
    off = off_set(con)
    achado = _acha_titulo(con, "filmes", titulo)
    if not achado:
        return None, {}
    fid, real = achado
    fontes = con.execute("SELECT versao, url FROM filme_fontes WHERE filme_id=? "
                         "ORDER BY versao, ordem", (fid,)).fetchall()
    por: dict = {}
    for ver, url in fontes:
        if host_of(url) in off:
            continue
        por.setdefault(ver, []).append(fonte_player(url))
    return real, por


def episodio_fontes(con, titulo: str, temp: int, ep: int, versao: str):
    off = off_set(con)
    achado = _acha_titulo(con, "series", titulo)
    if not achado:
        return None, []
    sid, real = achado
    rows = con.execute("SELECT url FROM episodios WHERE serie_id=? AND temporada=? "
                       "AND numero=? AND versao=? ORDER BY ordem",
                       (sid, temp, ep, versao)).fetchall()
    return real, [fonte_player(u) for (u,) in rows if host_of(u) not in off]


def cmd_download(titulo_real: str, fontes: list, rotulo: str, saida: str, max_mb: float):
    if not fontes:
        print("sem fonte ativa")
        return
    print(f"sondando {len(fontes)} fonte(s)...")
    f, dest, tam = escolher_melhor(fontes)
    if not dest:
        print("sem URL final")
        return
    print(f"maior: {tam / 1e6:.0f} MB · {dest}")
    out = Path(saida) / nome_arquivo(rotulo)
    out.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max_mb * 1e6)
    if f["tipo"] == "hls":
        baixar_hls(dest, out, f.get("headers"), max_bytes)
    else:
        baixar_url(dest, out.with_suffix(".mp4"), f.get("headers"), max_bytes)


def connect() -> sqlite3.Connection:
    db_new = not DB_PATH.exists()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON")
    if db_new or SCHEMA_PATH.exists():
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return con


def enc_letra(l: str) -> str:
    return "%23" if l == "#" else l


def sync():
    con = connect()
    cur = con.cursor()
    for t in ("canal_fontes", "canais", "radios", "filme_fontes", "filmes",
              "episodios", "series", "fichas", "bases", "desativados", "meta"):
        cur.execute(f"DELETE FROM {t}")
    con.commit()

    print("baixando desativados...")
    off = set(HARD_OFF)
    try:
        raw = fetch_text(DESATIVADAS_URL)
        for h in (json.loads(raw or "{}").get("desativados") or []):
            h = str(h).strip().lower()
            if h:
                off.add(h)
    except Exception as e:
        print(f"  ! desativadas: {e}")
    cur.executemany("INSERT INTO desativados(host) VALUES (?)", [(h,) for h in sorted(off)])

    print("baixando canais...")
    cat = fetch_text(CANAIS_BASE + "catalogo.txt") or ""
    cus = fetch_text(CANAIS_BASE + "canais.txt") or ""
    res = fetch_text(CANAIS_BASE + "restritos.txt") or ""
    canais = merge_listas(parse_catalogo(cat), parse_m3u(cus))
    restritos = parse_catalogo(res)
    for c in restritos:
        c["_restrito"] = 1
    todos = canais + restritos
    for c in todos:
        cur.execute("INSERT INTO canais(nome, logo, categoria, restrito) VALUES (?,?,?,?)",
                    (c["name"], c.get("logo"), c.get("group"),
                     1 if c.get("_restrito") else 0))
        cid = cur.lastrowid
        for i, s in enumerate(c["sources"]):
            cur.execute("""INSERT INTO canal_fontes
                (canal_id, ordem, url, nome, qualidade, referer, user_agent, key_id, key)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (cid, i, s.get("url"), s.get("name"), s.get("quality"),
                 s.get("referer"), s.get("userAgent"), s.get("keyId"), s.get("key")))
    print(f"  {len(todos)} canais, {sum(len(c['sources']) for c in todos)} fontes")

    print("baixando rádios...")
    rad = fetch_text(CANAIS_BASE + "radios.txt") or ""
    nrad = 0
    seen = set()
    for line in rad.split("\n"):
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 2 and p[0] and p[1].startswith("http") and p[0] not in seen:
            seen.add(p[0])
            logo = p[2] if len(p) > 2 and p[2].startswith("http") else None
            cur.execute("INSERT OR IGNORE INTO radios(nome, url, logo) VALUES (?,?,?)",
                        (p[0], p[1], logo))
            nrad += 1
    print(f"  {nrad} rádios")

    print("baixando índice VOD...")
    indice = fetch_text(FILMES_BASE + "indice.txt") or ""
    bases, letras = {}, []
    for line in indice.split("\n"):
        if line.startswith("base:"):
            _, _, rest = line.partition(":")
            p = rest.strip().split(None, 1)
            if len(p) == 2:
                bases[int(p[0])] = p[1]
        elif line.strip():
            p = line.split("\t")
            if len(p) >= 3:
                letras.append(p[0])
    cur.executemany("INSERT INTO bases(id, prefixo) VALUES (?,?)", sorted(bases.items()))
    if not letras:
        letras = LETRAS
    print(f"  {len(bases)} bases, letras: {''.join(letras)}")

    def fontes_dict(celula: str) -> dict:
        out = {}
        for item in celula.split(","):
            item = item.strip()
            if item:
                out.setdefault(expand_base(item, bases), None)
        return [u for u in out if u]

    nf, ne = 0, 0
    for letra in letras:
        e = enc_letra(letra)
        for arq, reservado in ((f"filmes-{e}.txt", 0), (f"reservado-{e}.txt", 1)):
            txt = fetch_text(FILMES_BASE + arq)
            if not txt:
                continue
            for line in txt.split("\n"):
                p = line.split("\t")
                if len(p) < 2 or not p[0].strip():
                    continue
                cur.execute("INSERT OR IGNORE INTO filmes(titulo, letra, reservado) VALUES (?,?,?)",
                            (p[0].strip(), letra, reservado))
                fid = cur.execute("SELECT id FROM filmes WHERE titulo=? AND reservado=?",
                                  (p[0].strip(), reservado)).fetchone()[0]
                for cel in p[1:]:
                    if "=" not in cel:
                        continue
                    ver, _, refs = cel.partition("=")
                    for i, u in enumerate(fontes_dict(refs)):
                        cur.execute("""INSERT INTO filme_fontes(filme_id, versao, ordem, url)
                                       VALUES (?,?,?,?)""", (fid, ver.strip(), i, u))
                        nf += 1
            con.commit()
        stxt = fetch_text(FILMES_BASE + f"series-{e}.txt")
        if not stxt:
            continue
        series, pedacos = [], set()
        for line in stxt.split("\n"):
            p = line.split("\t")
            if len(p) < 4 or not p[0].strip():
                continue
            try:
                ped, tot = int(p[2] or 0), int(p[3] or 0)
            except ValueError:
                continue
            series.append((p[0].strip(), p[1].strip(), ped, tot))
            pedacos.add(ped)
        for titulo, ano, ped, tot in series:
            cur.execute("""INSERT OR IGNORE INTO series(titulo, ano, letra, pedaco, episodios)
                           VALUES (?,?,?,?,?)""", (titulo, ano, letra, ped, tot))
        ped_txt = {}
        for ped in sorted(pedacos):
            t = fetch_text(FILMES_BASE + f"series-{e}-{ped}.txt") or ""
            ped_txt[ped] = t
            con.commit()
        for titulo, ano, ped, _ in series:
            sid = cur.execute("SELECT id FROM series WHERE titulo=? AND ano=?",
                              (titulo, ano)).fetchone()[0]
            txt = ped_txt.get(ped, "")
            dentro = False
            for line in txt.split("\n"):
                if line.startswith("@"):
                    hdr = line[1:].split("\t")
                    dentro = (hdr[0] == titulo and (len(hdr) < 2 or hdr[1] == ano))
                    continue
                if not dentro:
                    continue
                p = line.split("\t")
                if len(p) < 4:
                    continue
                try:
                    temp, num = int(p[0] or 0), int(p[1] or 0)
                except ValueError:
                    continue
                for i, u in enumerate(fontes_dict(p[3])):
                    cur.execute("""INSERT INTO episodios(serie_id, temporada, numero, versao, ordem, url)
                                   VALUES (?,?,?,?,?,?)""", (sid, temp, num, p[2].strip(), i, u))
                    ne += 1
        con.commit()
        print(f"  letra {letra}: ok ({nf} fontes filme, {ne} eps até aqui)")

    print("baixando fichas/destaques...")
    fichas = fetch_text(FILMES_BASE + "fichas.txt") or ""
    capa = ""
    nfi = 0
    for line in fichas.split("\n"):
        if line.startswith("capa:"):
            capa = line[5:].strip()
            continue
        if line.startswith("#") or not line.strip():
            continue
        p = line.split("\t")
        if len(p) < 5:
            continue
        try:
            tid = int(p[2]) if p[2].strip().isdigit() else None
        except ValueError:
            tid = None
        cur.execute("""INSERT OR REPLACE INTO fichas(tipo, titulo, tmdb_id, poster, generos)
                       VALUES (?,?,?,?,?)""",
                    (p[0].strip(), p[1].strip(), tid,
                     (capa + p[3].strip()) if p[3].strip() else None, p[4].strip()))
        nfi += 1
    cur.execute("INSERT OR REPLACE INTO meta(chave, valor) VALUES ('destaques', ?)",
                (fetch_text(FILMES_BASE + "destaques.txt") or "",))
    cur.execute("INSERT OR REPLACE INTO meta(chave, valor) VALUES ('sync_ok', '1')")
    con.commit()
    print(f"  {nfi} fichas")
    print(f"OK: {len(todos)} canais, {nrad} rádios, {nf} fontes de filme, {ne} episódios.")
    con.close()


def off_set(con) -> set:
    return {r[0] for r in con.execute("SELECT host FROM desativados")}


def filtrar(urls: list, off: set) -> list:
    return [u for u in urls if (h := host_of(u)) and h not in off]


def fonte_player(url: str, referer=None, ua=None, key_id=None, key=None) -> dict:
    d = {"url": url, "tipo": tipo_midia(url),
         "headers": {k: v for k, v in
                     (("Referer", referer), ("User-Agent", ua)) if v},
         "proxy": proxy_url(url, referer, ua)}
    if key_id and key:
        d["clearkey"] = {"keyId": key_id, "key": key}
    return d


def cmd_stats():
    con = connect()
    for t in ("canais", "canal_fontes", "radios", "filmes", "filme_fontes",
              "series", "episodios", "fichas"):
        print(f"{t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    con.close()


def cmd_canais(busca: str | None):
    con = connect()
    q = "SELECT nome, categoria FROM canais ORDER BY nome"
    rows = con.execute(q).fetchall()
    if busca:
        b = norm_nome(busca)
        rows = [r for r in rows if b in norm_nome(r[0])]
    for nome, cat in rows:
        print(f"{nome}\t{cat or ''}")
    con.close()


def cmd_resolve_canal(nome: str, resolver_redirect=False):
    con = connect()
    row = con.execute("SELECT id, nome, logo, categoria FROM canais WHERE nome=?",
                      (nome,)).fetchone()
    if not row:
        like = con.execute("SELECT nome FROM canais WHERE nome LIKE ? LIMIT 10",
                           (f"%{nome}%",)).fetchall()
        print(json.dumps({"erro": "canal não encontrado",
                          "sugestoes": [r[0] for r in like]}, ensure_ascii=False, indent=2))
        return
    cid, nome, logo, cat = row
    off = off_set(con)
    fontes = con.execute("""SELECT url, nome, qualidade, referer, user_agent, key_id, key
                            FROM canal_fontes WHERE canal_id=? ORDER BY ordem""", (cid,)).fetchall()
    con.close()
    saidas, vistas = [], set()
    for url, fnome, qual, ref, ua, kid, key in fontes:
        if not url or host_of(url) in off or url in vistas:
            continue
        vistas.add(url)
        f = fonte_player(url, ref, ua, kid, key)
        f["fonte_nome"] = fnome
        f["qualidade"] = qual
        saidas.append(f)
    print(json.dumps({"canal": nome, "logo": logo, "categoria": cat,
                      "fontes": saidas}, ensure_ascii=False, indent=2))


def cmd_resolve_filme(titulo: str, versao: str, final: bool):
    con = connect()
    real, por = filme_fontes(con, titulo)
    con.close()
    if real is None:
        print(json.dumps({"erro": "filme não encontrado"}, ensure_ascii=False))
        return
    urls = por.get(versao) or por.get("dub") or next(iter(por.values()), [])
    out = []
    for f in urls:
        f["final"] = final_url(f["url"]) if (final and "api/stream" in f["url"]) else f["url"]
        out.append(f)
    ficha = ficha_de("f", real)
    print(json.dumps({"titulo": real, "versao": versao, "fontes": out,
                      **(ficha or {})}, ensure_ascii=False, indent=2))


def cmd_resolve_serie(titulo: str, temp: int, ep: int, versao: str, final: bool):
    con = connect()
    off = off_set(con)
    row = con.execute("SELECT id, titulo, ano FROM series WHERE titulo=?", (titulo,)).fetchone()
    if not row:
        row = con.execute("SELECT id, titulo, ano FROM series WHERE titulo LIKE ?",
                          (f"%{titulo}%",)).fetchone()
    if not row:
        print(json.dumps({"erro": "série não encontrada"}, ensure_ascii=False))
        return
    sid, real, ano = row
    rows = con.execute("""SELECT url FROM episodios
                          WHERE serie_id=? AND temporada=? AND numero=? AND versao=?
                          ORDER BY ordem""", (sid, temp, ep, versao)).fetchall()
    urls = filtrar([r[0] for r in rows], off)
    con.close()
    out = [{"url": u, "tipo": tipo_midia(u),
            "final": final_url(u) if (final and "api/stream" in u) else u} for u in urls]
    print(json.dumps({"titulo": real, "ano": ano, "temporada": temp,
                      "episodio": ep, "versao": versao, "fontes": out},
                     ensure_ascii=False, indent=2))


def ficha_de(tipo: str, titulo: str) -> dict | None:
    try:
        con = connect()
        r = con.execute("SELECT tmdb_id, poster, generos FROM fichas WHERE tipo=? AND titulo=?",
                        (tipo, titulo)).fetchone()
        con.close()
        if r:
            return {"tmdb_id": r[0], "poster": r[1], "generos": r[2]}
    except Exception:
        pass
    return None


def cmd_m3u(saida: str):
    con = connect()
    off = off_set(con)
    canais = con.execute("SELECT id, nome, logo, categoria FROM canais ORDER BY nome").fetchall()
    n = 0
    with open(saida, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cid, nome, logo, cat in canais:
            fontes = con.execute("SELECT url, referer, user_agent FROM canal_fontes "
                                 "WHERE canal_id=? ORDER BY ordem", (cid,)).fetchall()
            urls = filtrar([r[0] for r in fontes], off)
            if not urls:
                continue
            u = urls[0]
            ref = next((r[1] for r in fontes if r[0] == u and r[1]), None)
            ua = next((r[2] for r in fontes if r[0] == u and r[2]), None)
            if ref or ua:
                u = proxy_url(u, ref, ua)
            f.write(f'#EXTINF:-1 tvg-logo="{logo or ""}" group-title="{cat or ""}",{nome}\n{u}\n')
            n += 1
    con.close()
    print(f"{n} canais -> {saida}")


def main():
    ap = argparse.ArgumentParser(description="Saimo TV — scraper + resolver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    sub.add_parser("stats")
    p = sub.add_parser("canais")
    p.add_argument("--busca", default=None)
    p = sub.add_parser("resolve-canal")
    p.add_argument("nome")
    p = sub.add_parser("resolve-filme")
    p.add_argument("titulo")
    p.add_argument("--versao", default="dub")
    p.add_argument("--final", action="store_true",
                   help="resolve /api/stream até a URL final (faz 1 request HEAD)")
    p = sub.add_parser("resolve-serie")
    p.add_argument("titulo")
    p.add_argument("--temp", type=int, required=True)
    p.add_argument("--ep", type=int, required=True)
    p.add_argument("--versao", default="dub")
    p.add_argument("--final", action="store_true")
    p = sub.add_parser("m3u")
    p.add_argument("--saida", default="saimo.m3u")
    p = sub.add_parser("download-filme")
    p.add_argument("titulo")
    p.add_argument("--versao", default="dub")
    p.add_argument("--saida", default="downloads")
    p.add_argument("--max-mb", type=float, default=0,
                   help="baixa só os primeiros X MB (amostra, sem renomear)")
    p = sub.add_parser("download-serie")
    p.add_argument("titulo")
    p.add_argument("--temp", type=int, required=True)
    p.add_argument("--ep", type=int, required=True)
    p.add_argument("--versao", default="dub")
    p.add_argument("--saida", default="downloads")
    p.add_argument("--max-mb", type=float, default=0)
    p = sub.add_parser("radio")
    p.add_argument("--busca", default=None)
    a = ap.parse_args()
    if a.cmd == "sync":
        sync()
    elif a.cmd == "stats":
        cmd_stats()
    elif a.cmd == "canais":
        cmd_canais(a.busca)
    elif a.cmd == "resolve-canal":
        cmd_resolve_canal(a.nome)
    elif a.cmd == "resolve-filme":
        cmd_resolve_filme(a.titulo, a.versao, a.final)
    elif a.cmd == "resolve-serie":
        cmd_resolve_serie(a.titulo, a.temp, a.ep, a.versao, a.final)
    elif a.cmd == "m3u":
        cmd_m3u(a.saida)
    elif a.cmd == "download-filme":
        con = connect()
        real, por = filme_fontes(con, a.titulo)
        con.close()
        if real is None:
            print("filme não encontrado")
        else:
            cmd_download(real, por.get(a.versao) or por.get("dub")
                         or next(iter(por.values()), []),
                         f"{real} [{a.versao}]", a.saida, a.max_mb)
    elif a.cmd == "download-serie":
        con = connect()
        real, fontes = episodio_fontes(con, a.titulo, a.temp, a.ep, a.versao)
        con.close()
        if real is None:
            print("série não encontrada")
        else:
            cmd_download(real, fontes,
                         f"{real} S{a.temp:02d}E{a.ep:02d} [{a.versao}]",
                         a.saida, a.max_mb)
    elif a.cmd == "radio":
        con = connect()
        rows = con.execute("SELECT nome, url FROM radios ORDER BY nome").fetchall()
        con.close()
        if a.busca:
            b = a.busca.lower()
            rows = [r for r in rows if b in r[0].lower()]
        for nome, url in rows[:200]:
            print(f"{nome}\t{url}")


if __name__ == "__main__":
    main()
