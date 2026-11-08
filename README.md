# Saimo Scraper

## sync

Baixa tudo e monta `saimo.db`.

```bash
python3 saimo.py sync
```

Fontes: `https://canais-saimo.pages.dev/catalogo.txt`, `canais.txt`,
`restritos.txt`, `radios.txt` e `https://filmes-saimo.pages.dev/indice.txt`,
`filmes-{A-Z,#}.txt`, `reservado-{L}.txt`, `series-{L}.txt`,
`series-{L}-{pedaco}.txt`, `fichas.txt`, `destaques.txt`,
`https://saimo-tv.pages.dev/fontes-desativadas.json`.

## stats

Mostra a quantidade de registros em cada tabela.

```bash
python3 saimo.py stats
```

Fonte: `saimo.db` local.

## canais

Lista os canais de TV ao vivo, com filtro opcional.

```bash
python3 saimo.py canais --busca "globo"
```

Fonte: `saimo.db` (tabela `canais`, vinda de `catalogo.txt`).

## resolve-canal

Mostra as fontes diretas de um canal na ordem que o player tenta,
cada uma com `url`, `tipo`, `headers` e `proxy`.

```bash
python3 saimo.py resolve-canal "Globo RJ"
```

Fonte: `saimo.db` (tabela `canal_fontes`); o campo `proxy` usa
`https://saimo-tv.pages.dev/api/proxy`.

## resolve-filme

Mostra as fontes de um filme em `dub` ou `leg`. Com `--final`,
resolve o `api/stream` até o mp4 final.

```bash
python3 saimo.py resolve-filme "A Teia" --versao dub --final
```

Fonte: `saimo.db` (tabela `filme_fontes`); links `api/stream`
redirecionam via `https://saimo-tv.pages.dev/api/stream`.

## resolve-serie

Mostra as fontes de um episódio em `dub` ou `leg`. Com `--final`,
resolve o `api/stream` até a mídia final.

```bash
python3 saimo.py resolve-serie "A Casa do Dragão" --temp 1 --ep 1 --versao dub --final
```

Fonte: `saimo.db` (tabela `episodios`); links `api/stream`
redirecionam via `https://saimo-tv.pages.dev/api/stream`.

## radio

Lista as rádios, com filtro opcional.

```bash
python3 saimo.py radio --busca "rock"
```

Fonte: `saimo.db` (tabela `radios`, vinda de `radios.txt`).

## m3u

Gera playlist M3U com todos os canais para usar em player externo.

```bash
python3 saimo.py m3u --saida saimo.m3u
```

Fonte: `saimo.db` (tabela `canal_fontes`).

## download-filme / download-serie

Baixa a mídia na maior fonte disponível (sonda o tamanho de cada
fonte e pega a maior). `--max-mb` baixa só uma amostra, para teste.

```bash
python3 saimo.py download-filme "A Teia" --versao dub --saida downloads
python3 saimo.py download-serie "A Casa do Dragão" --temp 1 --ep 1 --saida downloads
```

Fonte: download direto dos hosts das fontes (mesmos de `resolve-*`).

## server

Sobe o front de teste com API em cima do banco.

```bash
python3 server.py --port 8000
```

Abrir `http://localhost:8000/app.html`. Fonte: `saimo.db` local.
