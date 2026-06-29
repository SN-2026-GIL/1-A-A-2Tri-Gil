"""
fetch_historico_anac.py — Dados históricos ANAC/VRA + Supabase v1.2
Correções aplicadas:
  - CSV do VRA tem linha de metadado no topo ('Atualizado em: YYYY-MM-DD')
    antes do cabeçalho real — o script agora detecta e pula essas linhas
  - URL corrigida para a estrutura atual do portal ANAC
  - Nome do arquivo: VRA_YYYYM.csv (mês SEM zero à esquerda)

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
mes_pasta = f"{mes_int:02d} - {mes_nome}"   # ex: "05 - Maio"
arquivo   = f"VRA_{ano}{mes_int}.csv"        # ex: "VRA_20265.csv" (sem zero no mês)

BASE_ANAC = "https://sistemas.anac.gov.br/dadosabertos/"
CAMINHO   = f"Voos e operações aéreas/Voo Regular Ativo (VRA)/{ano}/{mes_pasta}/{arquivo}"
VRA_URL   = BASE_ANAC + quote(CAMINHO, safe="/")

print(f"\nURL a buscar: {VRA_URL}")

# ── Mapeamentos de colunas ────────────────────────────────────────────────────

COLS = {
    "empresa":      ["ICAO Empresa Aérea",    "Empresa (Sigla)",       "sg_empresa_icao"],
    "voo":          ["Número do Voo",          "Numero Voo",            "nr_voo"],
    "origem":       ["ICAO Aeródromo Origem",  "Aeroporto Origem",      "sg_icao_origem"],
    "destino":      ["ICAO Aeródromo Destino", "Aeroporto Destino",     "sg_icao_destino"],
    "partida_prev": ["Partida Prevista",        "Partida Prevista",      "dt_partida_prevista"],
    "partida_real": ["Partida Real",            "Partida Real",          "dt_partida_real"],
    "chegada_prev": ["Chegada Prevista",        "Chegada Prevista",      "dt_chegada_prevista"],
    "chegada_real": ["Chegada Real",            "Chegada Real",          "dt_chegada_real"],
    "situacao":     ["Situação Voo",            "Situacao Voo",          "situacao"],
    "motivo":       ["Justificativa",           "Motivo Alteracao",      "motivo_alteracao"],
}

# Palavras-chave que identificam a linha de cabeçalho real do CSV
# (usadas para pular linhas de metadado como "Atualizado em: YYYY-MM-DD")
CABECALHO_KEYWORDS = ["ICAO", "Empresa", "Voo", "Origem", "Destino",
                      "Partida", "Chegada", "Situação", "Situacao"]


def get_col(row: dict, key: str) -> str:
    for nome in COLS.get(key, [key]):
        if nome in row:
            return (row[nome] or "").strip()
    return ""


def parse_dt_anac(dt_str: str) -> str | None:
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
    if not dt_str or len(dt_str) < 10:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def diff_minutos(prev: str, real: str) -> int | None:
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            dp = datetime.strptime(prev.strip(), fmt)
            dr = datetime.strptime(real.strip(), fmt)
            return int((dr - dp).total_seconds() / 60)
        except (ValueError, AttributeError):
            continue
    return None


def encontrar_inicio_cabecalho(linhas: list[str], sep: str) -> int:
    """
    Localiza o índice da linha que contém o cabeçalho real do CSV.
    Pula linhas de metadado como 'Atualizado em: YYYY-MM-DD'.
    """
    for i, linha in enumerate(linhas):
        # Verifica se a linha contém pelo menos uma palavra-chave de cabeçalho
        if any(kw in linha for kw in CABECALHO_KEYWORDS):
            print(f"  Cabeçalho encontrado na linha {i + 1}: {linha.strip()[:80]}")
            return i
    return 0  # fallback: usa a primeira linha


# ── Busca o arquivo VRA ───────────────────────────────────────────────────────

def baixar_vra() -> list[dict]:
    print(f"GET {VRA_URL}")
    try:
        r = requests.get(VRA_URL, timeout=120)
        if r.status_code == 404:
            print(f"  Não encontrado (404).")
            print(f"  Verifique se o arquivo {arquivo} já foi publicado pela ANAC.")
            return []
        r.raise_for_status()

        # Decodificação: tenta utf-8-sig (com BOM), utf-8, latin-1
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                texto = r.content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            texto = r.content.decode("latin-1", errors="replace")

        linhas = texto.split("\n")
        print(f"  Total de linhas no arquivo: {len(linhas)}")

        # Detecta o separador (ponto-e-vírgula ou vírgula) usando a primeira linha
        # que pareça um cabeçalho real
        sep = ";"
        for linha in linhas[:10]:
            if any(kw in linha for kw in CABECALHO_KEYWORDS):
                sep = ";" if linha.count(";") >= linha.count(",") else ","
                break

        # Localiza o início do cabeçalho real (pula metadados do topo)
        inicio = encontrar_inicio_cabecalho(linhas, sep)

        # Reconstrói o texto CSV a partir do cabeçalho real
        texto_csv = "\n".join(linhas[inicio:])
        reader    = csv.DictReader(io.StringIO(texto_csv), delimiter=sep)
        registros = list(reader)

        print(f"  Registros carregados: {len(registros)}")
        if registros:
            print(f"  Colunas detectadas: {list(registros[0].keys())[:6]} ...")

        return registros

    except Exception as e:
        print(f"  [ERRO] {e}")
        return []


# ── Processa e filtra registros ───────────────────────────────────────────────

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

        empresa      = get_col(row, "empresa")
        nr_voo       = get_col(row, "voo")
        part_prev    = get_col(row, "partida_prev")
        part_real    = get_col(row, "partida_real")
        cheg_prev    = get_col(row, "chegada_prev")
        cheg_real    = get_col(row, "chegada_real")
        situacao     = get_col(row, "situacao")
        motivo       = get_col(row, "motivo")
        dt_ref       = extrair_data(part_prev or part_real)

        resultado.append({
            "ano_mes":          ano_mes,
            "icao_empresa":     empresa  or None,
            "nr_voo":           nr_voo   or None,
            "icao_origem":      origem   or None,
            "icao_destino":     destino  or None,
            "dt_referencia":    dt_ref,
            "partida_real":     parse_dt_anac(part_real),
            "chegada_real":     parse_dt_anac(cheg_real),
            "atraso_partida":   diff_minutos(part_prev, part_real),
            "atraso_chegada":   diff_minutos(cheg_prev, cheg_real),
            "situacao":         situacao.lower() if situacao else None,
            "motivo_alteracao": motivo or None,
        })

    if sem_icao:
        print(f"  Aviso: {sem_icao} linha(s) sem ICAO de origem/destino — ignoradas")
    print(f"  Registros filtrados para os aeroportos configurados: {len(resultado)}")
    return resultado


# ── Inserção no Supabase ──────────────────────────────────────────────────────

linhas_vra = baixar_vra()

if not linhas_vra:
    print("\n[AVISO] VRA não disponível ou vazio. Encerrando.")
    sys.exit(0)

registros   = processar_vra(linhas_vra)
processados = 0
erros       = 0

if not registros:
    print("\n[AVISO] Nenhum registro filtrado para os aeroportos configurados.")
    sys.exit(0)

print(f"\nEnviando {len(registros)} registros ao Supabase em lotes de {LOTE}...")

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
