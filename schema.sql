PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bases (
  id INTEGER PRIMARY KEY,
  prefixo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS desativados (
  host TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS canais (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL UNIQUE,
  logo TEXT,
  categoria TEXT,
  restrito INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_canais_nome ON canais(nome);
CREATE INDEX IF NOT EXISTS idx_canais_categoria ON canais(categoria);

CREATE TABLE IF NOT EXISTS canal_fontes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canal_id INTEGER NOT NULL REFERENCES canais(id) ON DELETE CASCADE,
  ordem INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL,
  nome TEXT,
  qualidade TEXT,
  referer TEXT,
  user_agent TEXT,
  key_id TEXT,
  key TEXT
);
CREATE INDEX IF NOT EXISTS idx_cfontes_canal ON canal_fontes(canal_id);

CREATE TABLE IF NOT EXISTS radios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL,
  logo TEXT
);

CREATE TABLE IF NOT EXISTS filmes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  titulo TEXT NOT NULL,
  letra TEXT NOT NULL,
  reservado INTEGER NOT NULL DEFAULT 0,
  UNIQUE(titulo, reservado)
);
CREATE INDEX IF NOT EXISTS idx_filmes_titulo ON filmes(titulo);
CREATE INDEX IF NOT EXISTS idx_filmes_letra ON filmes(letra);

CREATE TABLE IF NOT EXISTS filme_fontes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filme_id INTEGER NOT NULL REFERENCES filmes(id) ON DELETE CASCADE,
  versao TEXT NOT NULL,
  ordem INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ffontes_filme ON filme_fontes(filme_id);

CREATE TABLE IF NOT EXISTS series (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  titulo TEXT NOT NULL,
  ano TEXT NOT NULL DEFAULT '',
  letra TEXT NOT NULL,
  pedaco INTEGER NOT NULL DEFAULT 0,
  episodios INTEGER NOT NULL DEFAULT 0,
  UNIQUE(titulo, ano)
);
CREATE INDEX IF NOT EXISTS idx_series_titulo ON series(titulo);

CREATE TABLE IF NOT EXISTS episodios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serie_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  temporada INTEGER NOT NULL DEFAULT 0,
  numero INTEGER NOT NULL DEFAULT 0,
  versao TEXT NOT NULL DEFAULT 'dub',
  ordem INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ep_serie ON episodios(serie_id, temporada, numero);

CREATE TABLE IF NOT EXISTS fichas (
  tipo TEXT NOT NULL,
  titulo TEXT NOT NULL,
  tmdb_id INTEGER,
  poster TEXT,
  generos TEXT,
  PRIMARY KEY (tipo, titulo)
);
