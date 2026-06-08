# 08-05-2026 — SIROS + Supabase

Sub-projeto do programa **SN-2026**.  
Pipeline de dados com CI/CD completo: SIROS/ANAC → Supabase → GitHub Pages.

\---

## Arquitetura

```
GitHub Actions (4x ao dia)
   → scripts/fetch\_flights.py
   → GET sas.anac.gov.br/sas/siros\_api/voos
   → UPSERT tabela voos no Supabase
         ↓
Supabase (PostgreSQL)
   → API REST automática (anon key — leitura pública)
         ↓
GitHub Pages serve index.html
   → fetch da API Supabase
   → Filtros, ordenação, paginação e histórico
```

## O que ganhamos em relação ao projeto anterior

||SIROS + JSON (anterior)|**SIROS + Supabase (este)**|
|-|-|-|
|Histórico|Sobrescreve a cada execução|**Acumula — histórico completo**|
|Consultas|Apenas por aeroporto/data fixa|**Filtros, ordenação, paginação**|
|API própria|❌ Não|**✅ REST gerada automaticamente**|
|Dados no repo|data/\*.json commitados|**Só código — dados no banco**|
|Busca histórica|❌|**✅ Qualquer data disponível**|

## Como configurar

### 1\. Criar repositório na organização SN-2026

`github.com/SN-2026` → New repository → `07-05-2026-supabase` → Public

### 2\. Executar o SQL de setup no Supabase

Copie o conteúdo de `sql/setup.sql` e execute no **SQL Editor** do Supabase.

### 3\. Configurar Secrets no GitHub

Settings → Secrets and variables → Actions:

|Secret|Valor|
|-|-|
|`SUPABASE\_URL`|URL do projeto (ex: https://XXXX.supabase.co)|
|`SUPABASE\_SERVICE\_KEY`|service\_role key do Supabase|

### 4\. Configurar variável AIRPORTS

Settings → Variables → Actions:

* Nome: `AIRPORTS`
* Valor: `SBCA,SBGR,SBSP,SBCT,SBGL,SBBR,SBFL,SBPA,SBSV,SBFZ`

### 5\. Atualizar index.html com suas chaves

Edite as duas constantes no topo do `<script>` no `index.html`:

```javascript
const SUPABASE\_URL      = "https://SEU\_PROJETO.supabase.co";
const SUPABASE\_ANON\_KEY = "eyJ...SUA\_ANON\_KEY...";
```

### 6\. Ativar GitHub Pages

Settings → Pages → Branch: main / (root) → Save

### 7\. Executar o workflow pela primeira vez

Actions → Pipeline SIROS → Supabase → Run workflow

## Execução local

```bash
pip install requests supabase
export SUPABASE\_URL="https://SEU\_PROJETO.supabase.co"
export SUPABASE\_SERVICE\_KEY="SUA\_SERVICE\_KEY"
export AIRPORTS="SBCA"
python scripts/fetch\_flights.py
```

## Licença

MIT

