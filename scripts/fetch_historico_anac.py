"""
fetch_historico_anac.py — Dados históricos ANAC/VRA + Supabase v1.1
Correções aplicadas:
  - URL corrigida para a estrutura atual do portal ANAC:
    /Voos e operações aéreas/Voo Regular Ativo (VRA)/YYYY/MM - NomeMes/VRA_YYYYM.csv
  - Nome do arquivo: VRA_YYYYM.csv (mês SEM zero à esquerda: maio = VRA_20265.csv)
  - Pasta do mês: "05 - Maio", "06 - Junho" etc.
  - Mapeamento de colunas atualizado para o formato real do CSV

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

# ── Nomes dos meses em português ─────────────────────────────────────────────

MESES_PT = {
    1: "Janeiro",  2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",     6: "Junho",     7: "Julho",     8: "Agosto",
    9: "Setembro", 10: "Outubro",  11: "Novembro", 12: "Dezembro",
}

mes_nome  = MESES_PT[mes_int]
mes_pasta = f"{mes_int:02d} - {mes_nome}"          # ex: "06 - Junho"
arquivo   = f"VRA_{ano}{mes_int}.csv"               # ex: "VRA_20266.csv" (sem zero no mês)

# ── Construção da URL ─────────────────────────────────────────────────────────
# Estrutura atual do portal ANAC (verificada em junho/2026):
# /Voos e operações aéreas/Voo Regular Ativo (VRA)/YYYY/MM - NomeMes/VRA_YYYYM.csv

BASE_ANAC = "https://sistemas.anac.gov.br/dadosabertos/"
CAMINHO   = f"Voos e operações aéreas/Voo Regular Ativo (VRA)/{ano}/{mes_pasta}/{arquivo}"
VRA_URL   = BASE_ANAC + quote(CAMINHO, safe="/")

print(f"\nURL a buscar: {VRA_URL}")

# ── Mapeamentos de colunas do CSV ─────────────────────────────────────────────
# O VRA pode ter variações nos nomes de colunas entre versões.
# Cada lista abaixo tenta múltiplos nomes para maior compatibilidade.

COLS = {
    "empresa":      ["ICAO Empresa Aérea",      "Empresa (Sigla)",          "sg_empresa_icao"],
    "voo":          ["Número do Voo",            "Numero Voo",               "nr_voo"],
    "origem":       ["ICAO Aeródromo Origem",    "Aeroporto Origem",         "sg_icao_origem"],
    "destino":      ["ICAO Aeródromo Destino",   "Aeroporto Destino",        "sg_icao_destino"],
    "dt_ref":       ["Partida Prevista",          "DT_REFERENCIA",            "data_referencia"],
    "partida_prev": ["Partida Prevista",          "Partida Prevista",         "dt_partida_prevista"],
    "partida_real": ["Partida Real",              "Partida Real",             "dt_partida_real"],
    "chegada_prev": ["Chegada Prevista",          "Chegada Prevista",         "dt_chegada_prevista"],
    "chegada_real": ["Chegada Real",              "Chegada Real",             "dt_chegada_real"],
    "situacao":     ["Situação Voo",              "Situacao Voo",             "situacao"],
    "motivo":       ["Justificativa",             "Motivo Alteracao",         "motivo_alteracao"],
}


def get_col(row: dict, key: str) -> str:
    """Tenta múltiplos nomes de coluna para compatibilidade entre versões do CSV."""
    for nome in COLS.get(key, [key]):
        if nome in row:
            return (row[nome] or "").strip()
    return ""


def parse_dt_anac(dt_str: str) -> str | None:
    """Converte data/hora para ISO UTC."""
    if not dt_str or len(dt_str) < 10:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def extrair_data(dt_str: str) -> str | None:
    """Extrai apenas a data (YYYY-MM-DD) de uma string de data/hora."""
    if not dt_str or len(dt_str) < 10:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def diff_minutos(prev: str, real: str) -> int | None:
    """Calcula atraso em minutos entre horário previsto e real."""
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            dp = datetime.strptime(prev.strip(), fmt)
            dr = datetime.strptime(real.strip(), fmt)
            return int((dr - dp).total_seconds() / 60)
        except (ValueError, AttributeError):
            continue
    return None


# ── Busca o arquivo VRA ───────────────────────────────────────────────────────

def baixar_vra() -> list[dict]:
    print(f"GET {VRA_URL}")
    try:
        r = requests.get(VRA_URL, timeout=120)
        if r.status_code == 404:
            print(f"  Não encontrado (404). Verifique se o arquivo {arquivo} está disponível.")
            print(f"  URL completa: {VRA_URL}")
            return []
        r.raise_for_status()

        # Tenta decodificação: utf-8-sig (com BOM), depois latin-1
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                texto = r.content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            texto = r.content.decode("latin-1", errors="replace")

        # Detecta o separador (ponto-e-vírgula ou vírgula)
        primeira_linha = texto.split("\n")[0]
        sep = ";" if ";" in primeira_linha else ","

        reader   = csv.DictReader(io.StringIO(texto), delimiter=sep)
        registros = list(reader)
        print(f"  VRA carregado: {len(registros)} linhas brutas")

        # Mostra as colunas encontradas (útil para diagnóstico)
        if registros:
            print(f"  Colunas no CSV: {list(registros[0].keys())}")

        return registros
    except Exception as e:
        print(f"  [ERRO] {e}")
        return []


# ── Processa e filtra registros ───────────────────────────────────────────────

def processar_vra(linhas: list[dict]) -> list[dict]:
    resultado  = []
    sem_filtro = 0

    for row in linhas:
        # Tenta encontrar origem e destino em qualquer coluna disponível
        origem  = get_col(row, "origem").upper()
        destino = get_col(row, "destino").upper()

        if not origem and not destino:
            sem_filtro += 1
            continue

        if origem not in AIRPORTS and destino not in AIRPORTS:
            continue

        empresa       = get_col(row, "empresa")
        nr_voo        = get_col(row, "voo")
        partida_prev  = get_col(row, "partida_prev")
        partida_real  = get_col(row, "partida_real")
        chegada_prev  = get_col(row, "chegada_prev")
        chegada_real  = get_col(row, "chegada_real")
        situacao      = get_col(row, "situacao")
        motivo        = get_col(row, "motivo")
        dt_ref        = extrair_data(partida_prev or partida_real)

        resultado.append({
            "ano_mes":          ano_mes,
            "icao_empresa":     empresa or None,
            "nr_voo":           nr_voo  or None,
            "icao_origem":      origem  or None,
            "icao_destino":     destino or None,
            "dt_referencia":    dt_ref,
            "partida_real":     parse_dt_anac(partida_real),
            "chegada_real":     parse_dt_anac(chegada_real),
            "atraso_partida":   diff_minutos(partida_prev, partida_real),
            "atraso_chegada":   diff_minutos(chegada_prev, chegada_real),
            "situacao":         situacao.lower() if situacao else None,
            "motivo_alteracao": motivo or None,
        })

    if sem_filtro:
        print(f"  Aviso: {sem_filtro} linha(s) sem campos de origem/destino reconhecíveis")
    print(f"  Registros filtrados para os aeroportos configurados: {len(resultado)}")
    return resultado


# ── Inserção no Supabase ──────────────────────────────────────────────────────

linhas_vra = baixar_vra()

if not linhas_vra:
    print("\n[AVISO] VRA não disponível para o período. Encerrando.")
    sys.exit(0)

registros   = processar_vra(linhas_vra)
processados = 0
erros       = 0

if not registros:
    print("\n[AVISO] Nenhum registro filtrado para os aeroportos configurados.")
    print("  Verifique se as colunas de origem/destino no CSV correspondem ao mapeamento.")
    sys.exit(0)

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
