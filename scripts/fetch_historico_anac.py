"""
fetch_historico_anac.py — Dados históricos ANAC/VRA + Supabase v1.3
Correções baseadas no formato real do arquivo VRA (maio/2026):
  - Separador: TAB (\t)
  - Linha 1: metadado "Atualizado em: YYYY-MM-DD" — ignorada
  - Linha 2: cabeçalho real com nomes das colunas
  - Coluna de voo: "Número Voo" (sem "do")
  - Data prevista: "2026-05-29 01:00:00.100000000" (ISO com nanossegundos)
  - Data real:     "29/05/2026 00:50" (DD/MM/YYYY HH:MM)
  - Valor nulo:    string "null" tratada como None

Variáveis de ambiente:
  SUPABASE_URL         → URL do projeto (GitHub Secret)
  SUPABASE_SERVICE_KEY → secret key / service_role key (GitHub Secret)
  AIRPORTS             → ICAOs para filtrar (GitHub Variable)
  ANO_MES              → Período no formato YYYY-MM (ex: 2026-05)
                         Padrão: mês anterior ao atual
"""

import csv
import io
import os
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
from supabase import create_client

# ── Credenciais ───────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[ERRO CRÍTICO] SUPABASE_URL e SUPABASE_SERVICE_KEY são obrigatórios.")
    sys.exit(1)

db = create_client(SUPABASE_URL, SUPABASE_KEY)
print(f"Supabase conectado: {SUPABASE_URL}")

# ── Configurações ─────────────────────────────────────────────────────────────

airports_env = os.environ.get("AIRPORTS", "SBCA")
AIRPORTS     = [a.strip().upper() for a in airports_env.split(",") if a.strip()]
LOTE         = 500

BRT  = timezone(timedelta(hours=-3))
hoje = datetime.now(BRT)

if os.environ.get("ANO_MES"):
    ano_mes = os.environ["ANO_MES"].strip()
else:
    primeiro_do_mes = hoje.replace(day=1)
    mes_anterior    = primeiro_do_mes - timedelta(days=1)
    ano_mes         = mes_anterior.strftime("%Y-%m")

ano, mes = ano_mes.split("-")
mes_int  = int(mes)

print(f"Período histórico: {ano_mes}")
print(f"Aeroportos filtrados: {', '.join(AIRPORTS)}")

# ── URL do VRA ────────────────────────────────────────────────────────────────

MESES_PT = {
    1: "Janeiro",  2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",     6: "Junho",     7: "Julho",     8: "Agosto",
    9: "Setembro", 10: "Outubro",  11: "Novembro", 12: "Dezembro",
}

mes_nome  = MESES_PT[mes_int]
mes_pasta = f"{mes_int:02d} - {mes_nome}"   # ex: "05 - Maio"
arquivo   = f"VRA_{ano}{mes_int}.csv"        # ex: "VRA_20265.csv"

BASE_ANAC = "https://sistemas.anac.gov.br/dadosabertos/"
CAMINHO   = f"Voos e operações aéreas/Voo Regular Ativo (VRA)/{ano}/{mes_pasta}/{arquivo}"
VRA_URL   = BASE_ANAC + quote(CAMINHO, safe="/")

print(f"\nURL a buscar: {VRA_URL}")

# ── Mapeamento de colunas (formato real do VRA) ───────────────────────────────

COLS = {
    # A ordem importa: o primeiro nome encontrado no CSV é usado
    "empresa":      ["ICAO Empresa Aérea",    "Empresa (Sigla)"],
    "voo":          ["Número Voo",             "Número do Voo",    "Numero Voo"],
    "origem":       ["ICAO Aeródromo Origem",  "Aeroporto Origem"],
    "destino":      ["ICAO Aeródromo Destino", "Aeroporto Destino"],
    "partida_prev": ["Partida Prevista"],
    "partida_real": ["Partida Real"],
    "chegada_prev": ["Chegada Prevista"],
    "chegada_real": ["Chegada Real"],
    "situacao":     ["Situação Voo",           "Situacao Voo"],
    "motivo":       ["Código Justificativa",   "Justificativa",    "Motivo Alteracao"],
}

# Palavras-chave para identificar a linha de cabeçalho real
CABECALHO_KEYWORDS = ["ICAO", "Empresa", "Número", "Numero", "Origem", "Destino"]


def get_col(row: dict, key: str) -> str:
    """Busca o valor de uma coluna tentando múltiplos nomes possíveis."""
    for nome in COLS.get(key, [key]):
        if nome in row:
            val = (row[nome] or "").strip()
            return "" if val.lower() == "null" else val
    return ""


def parse_dt_vra(dt_str: str) -> str | None:
    """
    Converte datas do VRA para ISO UTC. O arquivo usa dois formatos:
      - Prevista: "2026-05-29 01:00:00.100000000"  (ISO com nanossegundos)
      - Real:     "29/05/2026 00:50"               (DD/MM/YYYY HH:MM)
    """
    if not dt_str or dt_str.lower() == "null":
        return None
    # Remove nanossegundos se presentes: "2026-05-29 01:00:00.100000000"
    dt_str = dt_str.strip().split(".")[0]  # "2026-05-29 01:00:00"
    for fmt in (
        "%Y-%m-%d %H:%M:%S",   # ISO sem nanossegundos
        "%d/%m/%Y %H:%M",      # formato real
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def extrair_data(dt_str: str) -> str | None:
    """Extrai apenas YYYY-MM-DD de uma string de data/hora."""
    iso = parse_dt_vra(dt_str)
    if iso:
        return iso[:10]
    return None


def diff_minutos(prev: str, real: str) -> int | None:
    """Calcula atraso em minutos entre previsto e real."""
    if not prev or not real or real.lower() == "null":
        return None
    dt_prev = parse_dt_vra(prev)
    dt_real = parse_dt_vra(real)
    if not dt_prev or not dt_real:
        return None
    try:
        dp = datetime.fromisoformat(dt_prev)
        dr = datetime.fromisoformat(dt_real)
        return int((dr - dp).total_seconds() / 60)
    except Exception:
        return None


# ── Busca e parseia o arquivo VRA ─────────────────────────────────────────────

def baixar_vra() -> list[dict]:
    print(f"GET {VRA_URL}")
    try:
        r = requests.get(VRA_URL, timeout=120)
        if r.status_code == 404:
            print(f"  Não encontrado (404) — {arquivo} ainda não publicado.")
            return []
        r.raise_for_status()

        # Decodificação: tenta utf-8-sig (BOM), utf-8, latin-1
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                texto = r.content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            texto = r.content.decode("latin-1", errors="replace")

        linhas = texto.splitlines()
        print(f"  Total de linhas no arquivo: {len(linhas)}")

        # Localiza a linha de cabeçalho real (pula metadados do topo)
        inicio = 0
        for i, linha in enumerate(linhas):
            if any(kw in linha for kw in CABECALHO_KEYWORDS):
                print(f"  Cabeçalho encontrado na linha {i + 1}: {linha.strip()[:80]}")
                inicio = i
                break

        # Detecta o separador na linha de cabeçalho
        cab = linhas[inicio]
        if cab.count("\t") >= cab.count(";"):
            sep = "\t"
            print("  Separador detectado: TAB")
        else:
            sep = ";"
            print("  Separador detectado: ponto-e-vírgula")

        # Parseia o CSV a partir do cabeçalho real
        texto_csv = "\n".join(linhas[inicio:])
        reader    = csv.DictReader(io.StringIO(texto_csv), delimiter=sep)
        registros = list(reader)

        print(f"  Registros carregados: {len(registros)}")
        if registros:
            cols = list(registros[0].keys())
            print(f"  Colunas detectadas ({len(cols)}): {cols[:6]} ...")

        return registros

    except Exception as e:
        print(f"  [ERRO] {e}")
        return []


# ── Filtra e normaliza ────────────────────────────────────────────────────────

def processar_vra(linhas: list[dict]) -> list[dict]:
    resultado = []
    sem_icao  = 0

    for row in linhas:
        origem  = get_col(row, "origem").upper()
        destino = get_col(row, "destino").upper()

        if not origem and not destino:
            sem_icao += 1
            continue

        if origem not in AIRPORTS and destino not in AIRPORTS:
            continue

        empresa   = get_col(row, "empresa")
        nr_voo    = get_col(row, "voo")
        part_prev = get_col(row, "partida_prev")
        part_real = get_col(row, "partida_real")
        cheg_prev = get_col(row, "chegada_prev")
        cheg_real = get_col(row, "chegada_real")
        situacao  = get_col(row, "situacao")
        motivo    = get_col(row, "motivo")
        dt_ref    = extrair_data(part_prev or part_real)

        resultado.append({
            "ano_mes":          ano_mes,
            "icao_empresa":     empresa  or None,
            "nr_voo":           nr_voo   or None,
            "icao_origem":      origem   or None,
            "icao_destino":     destino  or None,
            "dt_referencia":    dt_ref,
            "partida_real":     parse_dt_vra(part_real),
            "chegada_real":     parse_dt_vra(cheg_real),
            "atraso_partida":   diff_minutos(part_prev, part_real),
            "atraso_chegada":   diff_minutos(cheg_prev, cheg_real),
            "situacao":         situacao.upper() if situacao else None,
            "motivo_alteracao": motivo or None,
        })

    if sem_icao:
        print(f"  Aviso: {sem_icao} linha(s) sem ICAO reconhecível — ignoradas")
    print(f"  Registros filtrados para os aeroportos configurados: {len(resultado)}")
    return resultado


# ── Inserção no Supabase ──────────────────────────────────────────────────────

linhas_vra = baixar_vra()

if not linhas_vra:
    print("\n[AVISO] VRA não disponível. Encerrando.")
    sys.exit(0)

registros   = processar_vra(linhas_vra)
processados = 0
erros       = 0

if not registros:
    print("\n[AVISO] Nenhum registro filtrado. Encerrando.")
    sys.exit(0)

# Deduplicação: remove registros com a mesma chave única antes do envio
# O VRA pode conter linhas duplicadas no arquivo, causando erro PostgreSQL
# 21000: ON CONFLICT DO UPDATE command cannot affect row a second time
def deduplicar(lista: list) -> list:
    seen   = set()
    result = []
    for r in lista:
        key = (
            r.get("ano_mes"),
            r.get("icao_empresa"),
            r.get("nr_voo"),
            r.get("icao_origem"),
            r.get("icao_destino"),
            r.get("dt_referencia"),
        )
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result

antes     = len(registros)
registros = deduplicar(registros)
removidos = antes - len(registros)
if removidos:
    print(f"  Deduplicação: {removidos} registro(s) duplicado(s) removido(s) antes do envio")

print(f"\nEnviando {len(registros)} registros em lotes de {LOTE}...")

for i in range(0, len(registros), LOTE):
    lote     = registros[i:i + LOTE]
    num_lote = i // LOTE + 1
    try:
        db.table("historico_vra").upsert(
            lote,
            on_conflict="ano_mes,icao_empresa,nr_voo,icao_origem,icao_destino,dt_referencia",
        ).execute()
        processados += len(lote)
        print(f"  Lote {num_lote}: {len(lote)} registros enviados/processados")
    except Exception as e:
        erros += 1
        print(f"  [ERRO] Lote {num_lote}: {e}")

print(f"\nConcluído — {processados} registros históricos enviados/processados.")
if erros > 0:
    print(f"[ATENÇÃO] {erros} lote(s) com erro.")
    sys.exit(1)
