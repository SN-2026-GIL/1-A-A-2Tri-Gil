"""
fetch_flights.py — SIROS/ANAC v2
Correcoes aplicadas:
  1. URL base corrigida: /sas/siros_api/voos (sem /api/)
  2. Resposta da API e string JSON com duplo encode — json.loads() duplo
  3. Campo correto da empresa: sg_empresa_icao
  4. Parsing de data no formato DD/MM/YYYY HH:MM
  5. Tipo de operacao extraido do campo ds_tipo_servico
"""

import json
import os
from datetime import datetime, timezone, timedelta

import requests

# ── Configuracoes ─────────────────────────────────────────────────────────────

# URL CORRETA — sem /api/
API_BASE     = "https://sas.anac.gov.br/sas/siros_api"
airports_env = os.environ.get("AIRPORTS", "SBCA")
AIRPORTS     = [a.strip().upper() for a in airports_env.split(",") if a.strip()]

# Horario de Brasilia: UTC-3
BRT  = timezone(timedelta(hours=-3))
hoje = datetime.now(BRT)

# Formato exigido pela API: DDMMYYYY
data_ref = hoje.strftime("%d%m%Y")
data_iso = hoje.strftime("%Y-%m-%d")

print(f"SIROS/ANAC — Data: {hoje.strftime('%d/%m/%Y')} | Aeroportos: {', '.join(AIRPORTS)}")

# ── Mapeamentos ───────────────────────────────────────────────────────────────

AIRPORT_NAMES = {
    "SBRB":"Rio Branco","SBMO":"Maceio","SBMQ":"Macapa","SBEG":"Manaus",
    "SBSV":"Salvador","SBIL":"Ilheus","SBPS":"Porto Seguro",
    "SBFZ":"Fortaleza","SBJU":"Juazeiro do Norte","SBBR":"Brasilia",
    "SBVT":"Vitoria","SBGO":"Goiania","SBSL":"Sao Luis","SBCY":"Cuiaba",
    "SBCG":"Campo Grande","SBCF":"Belo Horizonte / Confins",
    "SBBH":"Belo Horizonte / Pampulha","SBUL":"Uberlandia",
    "SBBE":"Belem","SBSN":"Santarem","SBJP":"Joao Pessoa",
    "SBCT":"Curitiba","SBFI":"Foz do Iguacu","SBCA":"Cascavel",
    "SBLO":"Londrina","SBMG":"Maringa","SBRF":"Recife","SBTE":"Teresina",
    "SBGL":"Rio de Janeiro / Galeao","SBRJ":"Rio / Santos Dumont",
    "SBSG":"Natal","SBPA":"Porto Alegre","SBCX":"Caxias do Sul",
    "SBPV":"Porto Velho","SBBV":"Boa Vista","SBFL":"Florianopolis",
    "SBJV":"Joinville","SBNF":"Navegantes","SBGR":"Sao Paulo / Guarulhos",
    "SBSP":"Sao Paulo / Congonhas","SBKP":"Campinas / Viracopos",
    "SBRP":"Ribeirao Preto","SBSE":"Aracaju","SBPJ":"Palmas",
}

AIRLINES = {
    "GLO":"GOL","TAM":"LATAM","AZU":"Azul","ONE":"VOEPASS",
    "PTB":"Passaredo","TAP":"TAP Portugal","DAL":"Delta","UAL":"United",
    "AFR":"Air France","DLH":"Lufthansa","IBE":"Iberia","AAL":"American Airlines",
    "LAN":"LATAM Internacional","AVA":"Avianca","BAW":"British Airways",
    "UAE":"Emirates","THY":"Turkish Airlines","ETH":"Ethiopian Airlines",
    "SWR":"Swiss","ACA":"Air Canada","AMX":"Aeromexico","SAA":"South African",
    "SKU":"Sky Airline","CMP":"Copa Airlines","TSC":"Air Transat",
}

EQUIPAMENTOS = {
    "A20N":"Airbus A320neo","A21N":"Airbus A321neo","A319":"Airbus A319",
    "A320":"Airbus A320","A321":"Airbus A321","A332":"Airbus A330-200",
    "A333":"Airbus A330-300","A339":"Airbus A330-900neo","A35K":"Airbus A350-900",
    "A359":"Airbus A350-900","A388":"Airbus A380","B737":"Boeing 737",
    "B738":"Boeing 737-800","B739":"Boeing 737-900","B38M":"Boeing 737 MAX 8",
    "B748":"Boeing 747-8","B763":"Boeing 767-300","B764":"Boeing 767-400",
    "B772":"Boeing 777-200","B773":"Boeing 777-300","B77W":"Boeing 777-300ER",
    "B788":"Boeing 787-8","B789":"Boeing 787-9","E190":"Embraer E190",
    "E195":"Embraer E195","E295":"Embraer E195-E2","AT76":"ATR 72",
}


def get_airline(icao: str) -> str:
    return AIRLINES.get((icao or "").strip().upper(), icao or "?")


def get_equip(icao: str) -> str:
    return EQUIPAMENTOS.get((icao or "").strip().upper(), icao or "?")


def parse_siros_dt(dt_str: str) -> str:
    """
    Converte 'DD/MM/YYYY HH:MM' (UTC da API) para ISO com offset BRT (-03:00).
    Retorna string vazia se nao conseguir parsear.
    """
    if not dt_str or len(dt_str) < 16:
        return ""
    try:
        # Formato da API: "31/12/2026 23:45"
        dt_utc = datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M")
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_brt = dt_utc.astimezone(BRT)
        return dt_brt.isoformat()
    except Exception:
        return dt_str


def fmt_hora(dt_str: str) -> str:
    """Extrai apenas HH:MM de uma string ISO com timezone."""
    if not dt_str:
        return "?"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%H:%M")
    except Exception:
        return dt_str


def get_tipo_operacao(ds_tipo_servico: str) -> str:
    """
    Extrai 'Domestico' ou 'Internacional' do campo ds_tipo_servico.
    Exemplos reais:
      'REGULAR DE PASSAGEIROS INTERNACIONAL'
      'REGULAR DE PASSAGEIROS DOMESTICO'
      'SOBREVOOS OU TRASLADOS OPERACIONAIS INTERNACIONAL'
    """
    s = (ds_tipo_servico or "").upper()
    if "INTERNAC" in s:
        return "Internacional"
    return "Domestico"


# ── Busca principal ───────────────────────────────────────────────────────────

def buscar_todos_voos() -> list:
    url = f"{API_BASE}/voos"
    params = {"dataReferencia": data_ref}
    print(f"\nGET {url}?dataReferencia={data_ref}")

    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()

        # A API retorna uma STRING JSON com o array escapado dentro
        # Exemplo: "[{\"dt_referencia\":\"31/12/2026\",...}]"
        # r.json() decodifica a string externa → obtemos uma str Python
        # json.loads() decodifica a string interna → obtemos a lista
        decoded = r.json()

        if isinstance(decoded, list):
            # Resposta ja e uma lista (nao duplo-encoded)
            voos = decoded
        elif isinstance(decoded, str):
            # Resposta e uma string — decode duplo necessario
            voos = json.loads(decoded)
        else:
            print(f"  [AVISO] Tipo inesperado na resposta: {type(decoded)}")
            return []

        print(f"  Total de voos retornados: {len(voos)}")
        return voos

    except Exception as e:
        print(f"  [ERRO] Falha ao buscar voos: {e}")
        return []


def filtrar_aeroporto(todos_voos: list, icao: str):
    """Separa chegadas e partidas para um aeroporto específico."""
    chegadas  = []
    partidas  = []

    for f in todos_voos:
        origem  = (f.get("sg_icao_origem")  or "").strip().upper()
        destino = (f.get("sg_icao_destino") or "").strip().upper()

        if origem != icao and destino != icao:
            continue

        empresa  = (f.get("sg_empresa_icao")      or "").strip()
        nr_voo   = (f.get("nr_voo")               or "").strip().lstrip("0") or "?"
        equip    = (f.get("sg_equipamento_icao")   or "").strip()
        assentos = (f.get("qt_assentos_previstos") or "").strip()
        partida  = (f.get("dt_partida_prevista_utc") or "").strip()
        chegada  = (f.get("dt_chegada_prevista_utc") or "").strip()
        tipo_srv = (f.get("ds_tipo_servico")       or "").strip()

        partida_iso = parse_siros_dt(partida)
        chegada_iso = parse_siros_dt(chegada)

        registro = {
            "callsign":      f"{empresa}{nr_voo}",
            "numero_voo":    nr_voo,
            "airline_icao":  empresa,
            "airline":       get_airline(empresa),
            "equipamento_icao": equip,
            "equipamento":   get_equip(equip),
            "assentos":      assentos,
            "etapa":         str(f.get("nr_etapa") or ""),
            "tipo_operacao": get_tipo_operacao(tipo_srv),
            "tipo_servico":  tipo_srv,
            "origem_icao":   origem,
            "destino_icao":  destino,
            "partida_iso":   partida_iso,
            "chegada_iso":   chegada_iso,
            "partida_brt":   fmt_hora(partida_iso),
            "chegada_brt":   fmt_hora(chegada_iso),
            "status":        "programado",
            "fonte":         "SIROS/ANAC",
        }

        if destino == icao:
            registro["rota"]      = origem
            registro["rota_nome"] = AIRPORT_NAMES.get(origem, origem)
            chegadas.append(registro)

        if origem == icao:
            registro["rota"]      = destino
            registro["rota_nome"] = AIRPORT_NAMES.get(destino, destino)
            partidas.append(registro)

    chegadas.sort(key=lambda x: x.get("chegada_iso") or "")
    partidas.sort(key=lambda x: x.get("partida_iso") or "")
    return chegadas, partidas


def buscar_aerodromo(icao: str) -> dict:
    try:
        r = requests.get(
            f"{API_BASE}/aerodromo",
            params={"sg_aerodromo_icao_ou_iata": icao},
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f"  [AVISO] Aerodromo {icao}: {e}")
        return {}


# ── Execucao ──────────────────────────────────────────────────────────────────

os.makedirs("data", exist_ok=True)

todos_voos = buscar_todos_voos()

if not todos_voos:
    print("\n[AVISO] Nenhum voo retornado. Verifique a URL e o formato da data.")
else:
    for icao in AIRPORTS:
        nome = AIRPORT_NAMES.get(icao, icao)
        print(f"\nProcessando {icao} — {nome}...")

        chegadas, partidas = filtrar_aeroporto(todos_voos, icao)
        print(f"  {len(chegadas)} chegadas, {len(partidas)} partidas")

        aerodromo = buscar_aerodromo(icao)
        nome_oficial = (
            aerodromo.get("nm_aerodromo") or
            aerodromo.get("nome") or
            nome
        )

        output = {
            "updated_at":      datetime.now(timezone.utc).isoformat(),
            "data_referencia": data_iso,
            "airport_icao":    icao,
            "airport_name":    nome_oficial,
            "airport_info":    aerodromo,
            "source":          "SIROS/ANAC",
            "source_url":      "https://sas.anac.gov.br/sas/siros_api/",
            "arrivals":        chegadas,
            "departures":      partidas,
        }

        with open(f"data/{icao}.json", "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)

        print(f"  Salvo: data/{icao}.json")

print("\nConcluido.")
