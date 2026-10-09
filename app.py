# app.py — P&S REFRIGERAÇÃO | Gestão de orçamentos (Streamlit + Supabase Postgres)
# =============================================================================
# Versão 3.0
# - Mesma tabela do Supabase (public.orcamentos); só ACRESCENTA colunas opcionais
#   (token, titulo, descritivo, pagamento, resposta_em, resposta_obs) — nada é apagado
# - PDF profissional (pdf_orcamento.py) com dados da empresa (empresa.py)
# - Aceite online: o cliente recebe um link pelo WhatsApp, aprova ou recusa,
#   e o status é atualizado sozinho no sistema
# - Área de gestão protegida por senha (APP_SENHA nos Secrets)
# - Histórico, dashboard financeiro, busca, status rápido, exclusão com confirmação
# =============================================================================

import base64
import hmac
import json
import os
import re
import secrets
import urllib.parse
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

from empresa import EMPRESA
from pdf_orcamento import gerar_pdf_orcamento


# =========================
# CONFIGURAÇÃO
# =========================
APP_NOME = "P&S REFRIGERAÇÃO"
APP_SUBTITULO = "Gestão de orçamentos e serviços"
APP_URL_PADRAO = "https://ps-refrigeracao.streamlit.app"
FUSO = ZoneInfo("America/Sao_Paulo")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

STATUS_OPCOES = ["Pendente", "Aprovado", "Em Andamento", "Concluído", "Recusado", "Cancelado"]
STATUS_ABERTOS = ["Pendente", "Aprovado", "Em Andamento"]
STATUS_APROVADOS = ["Aprovado", "Em Andamento", "Concluído"]
# Cores da marca (logo P&S)
NOITE = "#060B16"
PAINEL = "#0D1626"
BORDA = "#1C2A40"
GELO = "#1FA8FF"
BRASA = "#FF8C1A"
TINTA = "#E6EEF8"
TINTA_SUAVE = "#93A6C1"
DINHEIRO = "#3FD18F"

# Status: paleta validada (daltonismo, contraste e brilho) sobre o fundo escuro.
# ORDEM_GRAFICO é a ordem de empilhamento — evita cores parecidas lado a lado.
CORES_STATUS = {
    "Concluído": "#2A9F63",
    "Em Andamento": "#4B74F0",
    "Aprovado": "#0EA5A5",
    "Pendente": "#CF7313",
    "Cancelado": "#A07BDB",
    "Recusado": "#E5466A",
}
ORDEM_GRAFICO = list(CORES_STATUS.keys())
COR_OUTROS = "#6B7A90"

# Colunas opcionais acrescentadas à tabela (criadas automaticamente se não existirem)
COLUNAS_EXTRAS = {
    "token": "text",
    "titulo": "text",
    "descritivo": "text",
    "pagamento": "text",
    "resposta_em": "timestamptz",
    "resposta_obs": "text",
}

ABA_NOVO = ":material/note_add: Novo orçamento"
ABA_HIST = ":material/folder_open: Histórico"
ABA_FIN = ":material/monitoring: Financeiro"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

PERIODOS = ["Este mês", "Mês passado", "Últimos 90 dias", "Este ano", "Ano passado", "Tudo", "Personalizado"]

COLUNAS_BASE = [
    "ID", "Data", "Data_dt", "Cliente", "WhatsApp", "Status", "Total", "Itens", "ItensJSON",
    "Token", "Titulo", "Descritivo", "Pagamento", "RespostaEm", "RespostaObs",
]

CSS = """
<style>
:root {
  --noite: #060B16; --painel: #0D1626; --painel-2: #111D31; --borda: #1C2A40;
  --gelo: #1FA8FF; --brasa: #FF8C1A; --tinta: #E6EEF8; --tinta-suave: #93A6C1; --dinheiro: #3FD18F;
  --termica: linear-gradient(90deg, #1FA8FF 0%, #39C6FF 38%, #FFB23F 72%, #FF8C1A 100%);
}
.stApp {
  background:
    radial-gradient(900px 420px at 8% -8%, rgba(31,168,255,.13), transparent 60%),
    radial-gradient(700px 360px at 100% 0%, rgba(255,140,26,.07), transparent 60%),
    var(--noite);
}
.block-container { padding-top: 1.2rem; max-width: 1240px; }
h1, h2, h3, h4, h5 { letter-spacing: .01em; }

/* Cabeçalho da marca */
.ps-hero { display: flex; align-items: center; gap: 18px; padding: 6px 2px 14px; }
.ps-hero img { width: 82px; height: 82px; flex: none;
  filter: drop-shadow(0 0 14px rgba(31,168,255,.35)); }
.ps-nome { font-family: "Saira Semi Condensed", sans-serif; font-weight: 700; font-size: 1.75rem;
  line-height: 1.05; color: var(--tinta); }
.ps-slogan { margin-top: 4px; font-size: .78rem; letter-spacing: .32em; text-transform: uppercase;
  color: var(--tinta-suave); }
.ps-termica { height: 3px; border-radius: 3px; background: var(--termica); margin: 0 0 16px;
  box-shadow: 0 0 14px rgba(31,168,255,.35), 0 0 14px rgba(255,140,26,.25); }
@media (max-width: 640px) {
  .ps-hero img { width: 60px; height: 60px; }
  .ps-nome { font-size: 1.3rem; }
  .ps-slogan { letter-spacing: .2em; font-size: .7rem; }
}

/* Abas: a aba ativa ganha a faixa térmica */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--borda); }
.stTabs button[data-baseweb="tab"] { font-family: "Saira Semi Condensed", sans-serif; font-size: 1.02rem;
  font-weight: 600; padding: 8px 14px; color: var(--tinta-suave); }
.stTabs button[data-baseweb="tab"][aria-selected="true"] { color: var(--tinta); }
.stTabs [data-baseweb="tab-highlight"] { background: var(--termica); height: 3px; border-radius: 3px; }

/* Indicadores */
div[data-testid="stMetric"] { background: var(--painel); }
div[data-testid="stMetricValue"] { font-family: "Saira Semi Condensed", sans-serif; font-size: 1.6rem; }
div[data-testid="stMetricLabel"] p { color: var(--tinta-suave); }

/* Selos de status e textos auxiliares */
.badge { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: .8rem;
  font-weight: 600; vertical-align: middle; border: 1px solid; }
.rotulo { color: var(--tinta-suave); font-size: .82rem; margin-bottom: 1px; }
.valor-grande { font-family: "Saira Semi Condensed", sans-serif; font-size: 2rem; font-weight: 700;
  color: var(--dinheiro); line-height: 1.15; }
.valor-sub { height: 2px; width: 64px; border-radius: 2px; background: var(--termica); margin-top: 6px; }
.det-num { font-family: "Saira Semi Condensed", sans-serif; font-size: 1.45rem; font-weight: 700; }
.det-cli { font-size: 1.12rem; font-weight: 600; margin: 2px 0 2px; }
.linha-info { margin: 0 0 9px 0; font-size: 1rem; color: var(--tinta); }
.dinheiro { color: var(--dinheiro); font-weight: 700; }

/* Botão principal com brilho de "gelo" */
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  box-shadow: 0 0 0 1px rgba(31,168,255,.4), 0 6px 18px rgba(31,168,255,.25); font-weight: 600; }
button:focus-visible, a:focus-visible { outline: 2px solid var(--brasa) !important; outline-offset: 2px; }

/* Login */
.ps-login { text-align: center; margin: 4vh auto 10px; }
.ps-login img { width: 168px; height: 168px; filter: drop-shadow(0 0 24px rgba(31,168,255,.35)); }
.ps-login p { color: var(--tinta-suave); margin-top: 8px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


# =========================
# HELPERS
# =========================
def apenas_digitos(s) -> str:
    return re.sub(r"\D+", "", str(s or ""))


def para_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float, Decimal)):
        try:
            f = float(v)
            return 0.0 if pd.isna(f) else f
        except Exception:
            return 0.0
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:          # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                     # 1234,56
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def fmt_brl(valor) -> str:
    v = para_float(valor)
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".", ",")


def para_data(v):
    """Converte o campo 'data' do banco (date, datetime, 'dd/mm/aaaa' ou ISO) em date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.date()
    s = str(v).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def id_key(id_str):
    """Ordenação do ID ANO-XXX."""
    try:
        ano, seq = str(id_str).split("-", 1)
        return (int(ano), int(re.sub(r"\D", "", seq) or "0"))
    except Exception:
        return (-1, -1)


def formatar_id_pdf(os_id) -> str:
    """2026-003 -> 003/26"""
    s = str(os_id)
    if "-" in s:
        ano, resto = s.split("-", 1)
        seq = re.sub(r"\D", "", resto) or resto
        return f"{seq}/{ano[-2:]}"
    return s


def badge_status(status: str) -> str:
    cor = CORES_STATUS.get(status, COR_OUTROS)
    return (f'<span class="badge" style="background:{cor}26;border-color:{cor}99;'
            f'color:{_clarear(cor)}">{status}</span>')


def _clarear(hex_cor: str, f: float = 0.45) -> str:
    """Versão mais clara da cor para texto legível sobre o fundo escuro."""
    h = hex_cor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * f) for c in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


def whatsapp_url(numero, mensagem: str):
    """Monta o link wa.me sem duplicar o 55. Retorna None se não houver número."""
    d = apenas_digitos(numero).lstrip("0")
    if len(d) < 8:
        return None
    if not (d.startswith("55") and len(d) in (12, 13)):
        d = "55" + d
    return f"https://wa.me/{d}?text={urllib.parse.quote(mensagem)}"


def mensagem_whatsapp(cliente: str, os_id: str, total: float, link: str = "") -> str:
    msg = (
        f"*{APP_NOME}*\n\n"
        f"Olá *{cliente}*, segue seu orçamento.\n"
        f"Nº: {formatar_id_pdf(os_id)}\n"
        f"Valor total: {fmt_brl(total)}"
    )
    if link:
        msg += (
            "\n\n👉 Veja o orçamento completo, baixe o PDF e *aprove ou recuse* por aqui:\n"
            f"{link}"
        )
    return msg


def app_url() -> str:
    try:
        url = st.secrets.get("APP_URL")
    except Exception:
        url = None
    return (url or APP_URL_PADRAO).rstrip("/")


def link_cliente(token: str) -> str:
    return f"{app_url()}/?orc={token}" if token else ""


def dt_resposta(v):
    """Data/hora da resposta como datetime com fuso (ou None)."""
    if v is None or isinstance(v, (int, float)):
        return None
    if not isinstance(v, str) and pd.isna(v):
        return None
    if isinstance(v, str):
        if not v.strip():
            return None
        try:
            v = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def fmt_datahora(v) -> str:
    """Data/hora da resposta do cliente no fuso de São Paulo."""
    d = dt_resposta(v)
    return d.astimezone(FUSO).strftime("%d/%m/%Y às %H:%M") if d else ""


def get_logo_path() -> str:
    """Busca a logo ignorando maiúsculas/minúsculas (ex: Logo.PNG, logo.jpeg)."""
    for pasta in (ASSETS_DIR, BASE_DIR):
        if os.path.isdir(pasta):
            for arquivo in sorted(os.listdir(pasta)):
                nome = arquivo.lower()
                if "logo" in nome and nome.endswith((".png", ".jpg", ".jpeg")):
                    return os.path.join(pasta, arquivo)
    return ""


def itens_json_para_df(itens_json, itens_txt="", total_antigo=0.0) -> pd.DataFrame:
    """Carrega ItensJSON; se vazio, recupera os itens do texto antigo (e o total no 1º item)."""
    colunas = ["Item", "Qtd", "Valor Unit."]
    try:
        if not itens_json or str(itens_json).strip() in ("", "[]"):
            lista = [i.strip() for i in str(itens_txt or "").split(",") if i.strip()]
            if not lista:
                return pd.DataFrame(columns=colunas)
            registros = [{"Item": i, "Qtd": 1, "Valor Unit.": 0.0} for i in lista]
            if para_float(total_antigo) > 0:
                registros[0]["Valor Unit."] = para_float(total_antigo)
            return pd.DataFrame(registros, columns=colunas)

        df = pd.DataFrame(json.loads(itens_json))
        for c in colunas:
            if c not in df.columns:
                df[c] = "" if c == "Item" else 0
        df["Item"] = df["Item"].fillna("").astype(str)
        df["Qtd"] = pd.to_numeric(df["Qtd"], errors="coerce").fillna(1).astype(int)
        df["Valor Unit."] = pd.to_numeric(df["Valor Unit."], errors="coerce").fillna(0.0).astype(float)
        return df[colunas]
    except Exception:
        return pd.DataFrame(columns=colunas)


def garantir_linha_em_branco(df: pd.DataFrame) -> pd.DataFrame:
    vazio = {"Item": "", "Qtd": 1, "Valor Unit.": 0.0}
    if df is None or df.empty:
        return pd.DataFrame([vazio])
    df = df.reset_index(drop=True)
    if str(df.iloc[-1].get("Item", "")).strip() != "":
        df = pd.concat([df, pd.DataFrame([vazio])], ignore_index=True)
    return df


def limpar_calcular(df: pd.DataFrame):
    """Remove linhas vazias, calcula Subtotal/Total e gera Itens + ItensJSON."""
    df = pd.DataFrame(df).copy()
    for c in ["Item", "Qtd", "Valor Unit."]:
        if c not in df.columns:
            df[c] = "" if c == "Item" else 0

    df["Item"] = df["Item"].fillna("").astype(str)
    df_limpo = df[df["Item"].str.strip() != ""].copy()

    df_limpo["Item"] = df_limpo["Item"].str.strip()
    df_limpo["Qtd"] = pd.to_numeric(df_limpo["Qtd"], errors="coerce").fillna(1).astype(int)
    df_limpo["Valor Unit."] = pd.to_numeric(df_limpo["Valor Unit."], errors="coerce").fillna(0.0).astype(float)
    df_limpo["Subtotal"] = df_limpo["Qtd"] * df_limpo["Valor Unit."]
    df_limpo = df_limpo[["Item", "Qtd", "Valor Unit.", "Subtotal"]].reset_index(drop=True)

    total = float(df_limpo["Subtotal"].sum())
    itens_txt = ", ".join(df_limpo["Item"].tolist())
    registros = [
        {"Item": r["Item"], "Qtd": int(r["Qtd"]), "Valor Unit.": float(r["Valor Unit."])}
        for r in df_limpo.to_dict(orient="records")
    ]
    itens_json = json.dumps(registros, ensure_ascii=False)
    return df_limpo, total, itens_txt, itens_json


# =========================
# BANCO (Supabase Postgres) — mesma tabela public.orcamentos
# =========================
def _db_url() -> str:
    url = None
    try:
        url = st.secrets.get("SUPABASE_DB_URL")
    except Exception:
        url = None
    url = url or os.environ.get("SUPABASE_DB_URL")
    if not url:
        st.error("Faltou configurar SUPABASE_DB_URL em Settings → Secrets no Streamlit Cloud.")
        st.stop()
    return url


@contextmanager
def conexao():
    """Abre, faz commit/rollback e SEMPRE fecha a conexão."""
    conn = psycopg2.connect(_db_url(), connect_timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@st.cache_resource(show_spinner=False)
def tipo_coluna_data() -> str:
    """Descobre se a coluna 'data' é texto ou date, para gravar no formato que já existe."""
    try:
        with conexao() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select data_type from information_schema.columns
                    where table_schema = 'public' and table_name = 'orcamentos' and column_name = 'data'
                    """
                )
                r = cur.fetchone()
        return (r[0] if r else "text").lower()
    except Exception:
        return "text"


def data_para_banco(d: date):
    t = tipo_coluna_data()
    if "date" in t or "timestamp" in t:
        return d
    return d.strftime("%d/%m/%Y")


@st.cache_resource(show_spinner=False)
def colunas_disponiveis() -> frozenset:
    """Cria (se faltarem) as colunas opcionais e devolve as colunas que existem na tabela.
    Só ACRESCENTA colunas vazias: nenhum dado existente é alterado."""
    try:
        with conexao() as conn:
            with conn.cursor() as cur:
                for nome, tipo in COLUNAS_EXTRAS.items():
                    cur.execute(f"alter table public.orcamentos add column if not exists {nome} {tipo}")
    except Exception:
        pass  # sem permissão para alterar: o app segue funcionando sem os recursos novos
    try:
        with conexao() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select column_name from information_schema.columns
                    where table_schema = 'public' and table_name = 'orcamentos'
                    """
                )
                return frozenset(r[0] for r in cur.fetchall())
    except Exception:
        return frozenset()


def tem_coluna(nome: str) -> bool:
    return nome in colunas_disponiveis()


def recursos_aceite_ok() -> bool:
    return all(tem_coluna(c) for c in ("token", "resposta_em", "resposta_obs"))


def novo_token() -> str:
    return secrets.token_urlsafe(12)


def montar_df(rows) -> pd.DataFrame:
    registros = []
    for r in rows:
        dt = para_data(r.get("data"))
        status = str(r.get("status") or "").strip() or "Pendente"
        registros.append(
            {
                "ID": str(r.get("id") or ""),
                "Data": fmt_data(dt) if dt else str(r.get("data") or ""),
                "Data_dt": pd.Timestamp(dt) if dt else pd.NaT,
                "Cliente": str(r.get("cliente") or "").strip(),
                "WhatsApp": str(r.get("whatsapp") or ""),
                "Status": status,
                "Total": para_float(r.get("total")),
                "Itens": str(r.get("itens") or ""),
                "ItensJSON": str(r.get("itensjson") or ""),
                "Token": str(r.get("token") or ""),
                "Titulo": str(r.get("titulo") or ""),
                "Descritivo": str(r.get("descritivo") or ""),
                "Pagamento": str(r.get("pagamento") or ""),
                "RespostaEm": dt_resposta(r.get("resposta_em")),
                "RespostaObs": str(r.get("resposta_obs") or ""),
            }
        )
    df = pd.DataFrame(registros, columns=COLUNAS_BASE)
    df["Data_dt"] = pd.to_datetime(df["Data_dt"], errors="coerce")
    df["Total"] = pd.to_numeric(df["Total"], errors="coerce").fillna(0.0).astype(float)
    df["RespostaEm"] = df["RespostaEm"].astype(object)
    return df


def _colunas_select() -> str:
    base = ["id", "data", "cliente", "whatsapp", "status", "total", "itens", "itensjson"]
    extras = [c for c in COLUNAS_EXTRAS if tem_coluna(c)]
    return ", ".join(base + extras)


@st.cache_data(ttl=60, show_spinner="Carregando dados...")
def ler_base() -> pd.DataFrame:
    with conexao() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"select {_colunas_select()} from public.orcamentos order by created_at desc")
            rows = cur.fetchall()
    return montar_df(rows)


def buscar_por_token(token: str):
    """Um único orçamento pelo link do cliente (sem cache: sempre o dado mais novo)."""
    if not token or not recursos_aceite_ok():
        return None
    with conexao() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"select {_colunas_select()} from public.orcamentos where token = %s", (token,))
            rows = cur.fetchall()
    df = montar_df(rows)
    return None if df.empty else df.iloc[0]


def registrar_resposta(token: str, aprovado: bool, obs: str) -> bool:
    """Grava a resposta do cliente. Só vale para orçamento Pendente (não sobrescreve)."""
    novo = "Aprovado" if aprovado else "Recusado"
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.orcamentos
                set status=%s, resposta_em=%s, resposta_obs=%s
                where token=%s and status=%s
                """,
                (novo, datetime.now(timezone.utc), (obs or "").strip()[:500], token, "Pendente"),
            )
            ok = cur.rowcount > 0
    _depois_de_gravar()
    return ok


def garantir_token(os_id: str, token_atual: str = "") -> str:
    """Devolve o token do orçamento, criando um se ainda não existir (orçamentos antigos)."""
    if token_atual or not recursos_aceite_ok():
        return token_atual or ""
    tok = novo_token()
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update public.orcamentos set token=%s where id=%s and (token is null or token='')",
                (tok, os_id),
            )
            cur.execute("select token from public.orcamentos where id=%s", (os_id,))
            r = cur.fetchone()
    _depois_de_gravar()
    return (r[0] if r else tok) or tok


def _depois_de_gravar():
    ler_base.clear()


def gerar_novo_id(ano: int) -> str:
    """Próximo ID ANO-XXX consultando o banco na hora (evita repetir número)."""
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from public.orcamentos where id like %s", (f"{ano}-%",))
            ids = [r[0] for r in cur.fetchall()]
    seqs = [int(re.sub(r"\D", "", str(i).split("-", 1)[1]) or "0") for i in ids if "-" in str(i)]
    return f"{ano}-{(max(seqs) + 1 if seqs else 1):03d}"


def _campos_extras(reg: dict) -> dict:
    """Campos opcionais que só são gravados se a coluna existir no banco."""
    extras = {}
    for col, chave in (("titulo", "Titulo"), ("descritivo", "Descritivo"), ("pagamento", "Pagamento")):
        if tem_coluna(col):
            extras[col] = (reg.get(chave) or "").strip()
    return extras


def salvar_orcamento(reg: dict, data_ref: date):
    """Insere um novo orçamento e devolve (ID, token)."""
    ultimo_erro = None
    for _ in range(3):
        os_id = gerar_novo_id(data_ref.year)
        campos = {
            "id": os_id,
            "data": data_para_banco(data_ref),
            "cliente": reg["Cliente"],
            "whatsapp": reg["WhatsApp"],
            "status": reg["Status"],
            "total": float(reg["Total"]),
            "itens": reg["Itens"],
            "itensjson": reg["ItensJSON"],
            **_campos_extras(reg),
        }
        tok = ""
        if tem_coluna("token"):
            tok = novo_token()
            campos["token"] = tok
        try:
            with conexao() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"insert into public.orcamentos ({', '.join(campos)}) "
                        f"values ({', '.join(['%s'] * len(campos))})",
                        tuple(campos.values()),
                    )
            _depois_de_gravar()
            return os_id, tok
        except psycopg2.IntegrityError as e:  # ID já usado em outro aparelho: tenta o próximo
            ultimo_erro = e
    raise ultimo_erro


def atualizar_orcamento(os_id: str, reg: dict, data_ref: date):
    campos = {
        "data": data_para_banco(data_ref),
        "cliente": reg["Cliente"],
        "whatsapp": reg["WhatsApp"],
        "status": reg["Status"],
        "total": float(reg["Total"]),
        "itens": reg["Itens"],
        "itensjson": reg["ItensJSON"],
        **_campos_extras(reg),
    }
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update public.orcamentos set {', '.join(f'{c}=%s' for c in campos)} where id=%s",
                (*campos.values(), os_id),
            )
    _depois_de_gravar()


def atualizar_status(os_id: str, status: str):
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("update public.orcamentos set status=%s where id=%s", (status, os_id))
    _depois_de_gravar()


def excluir_orcamento(os_id: str):
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from public.orcamentos where id=%s", (os_id,))
    _depois_de_gravar()


# =========================
# PDF (pdf_orcamento.py + dados de empresa.py)
# =========================
def pdf_orcamento_bytes(r, link: str = "") -> bytes:
    """PDF de um orçamento (registro do banco ou dict com as mesmas chaves)."""
    itens = itens_json_para_df(r["ItensJSON"], r["Itens"], r["Total"])
    itens_limpos, _, _, _ = limpar_calcular(itens)
    orc = {
        "id": r["ID"],
        "numero": formatar_id_pdf(r["ID"]),
        "cliente": r["Cliente"],
        "whatsapp": r["WhatsApp"],
        "data": r["Data"],
        "titulo": r.get("Titulo", ""),
        "descritivo": r.get("Descritivo", ""),
        "pagamento": r.get("Pagamento", ""),
        "total": float(r["Total"]),
        "status": r["Status"],
        "resposta_em": fmt_datahora(r.get("RespostaEm")),
        "resposta_obs": r.get("RespostaObs", ""),
    }
    if r["Status"] != "Pendente":
        link = ""
    return gerar_pdf_orcamento(orc, itens_limpos.to_dict(orient="records"), EMPRESA, get_logo_path(), link)


def pdf_do_registro(r) -> bytes:
    return pdf_orcamento_bytes(r, link_cliente(r.get("Token", "")))


def nome_arquivo_pdf(os_id, cliente) -> str:
    cli = re.sub(r"[^\w\- ]+", "", str(cliente)).strip().replace(" ", "_")[:40]
    return f"ORC_{os_id}_{cli}.pdf"


# =========================
# ESTADO DA TELA
# =========================
LINHA_VAZIA = {"Item": "", "Qtd": 1, "Valor Unit.": 0.0}


def _limpar_form():
    ss = st.session_state
    ss["id_edicao"] = None
    ss["f_cliente"] = ""
    ss["f_whats"] = ""
    ss["f_data"] = date.today()
    ss["f_status"] = "Pendente"
    ss["f_titulo"] = ""
    ss["f_descritivo"] = ""
    ss["f_pagamento"] = ""
    ss["itens_base"] = [dict(LINHA_VAZIA)]
    ss["chave_tabela"] = str(uuid.uuid4())


def init_state():
    ss = st.session_state
    if "f_cliente" not in ss:
        _limpar_form()
    ss.setdefault("ultimo_orcamento", None)
    ss.setdefault("aba", ABA_NOVO)
    # Pedidos feitos na execução anterior (precisam rodar antes de os campos aparecerem)
    if ss.pop("_reset_form", False):
        _limpar_form()
    if ss.pop("_limpar_selecao", False):
        ss["h_sel"] = None
    msg = ss.pop("_flash", None)
    if msg:
        st.toast(msg, icon="✅")


def cb_editar(os_id: str):
    """Carrega o orçamento no formulário e muda para a aba de edição."""
    ss = st.session_state
    df = ler_base()
    linha = df[df["ID"] == str(os_id)]
    if linha.empty:
        ss["_flash"] = "Orçamento não encontrado."
        return
    r = linha.iloc[0]
    ss["id_edicao"] = str(r["ID"])
    ss["f_cliente"] = r["Cliente"]
    ss["f_whats"] = r["WhatsApp"]
    ss["f_status"] = r["Status"] if r["Status"] in STATUS_OPCOES else "Pendente"
    ss["f_data"] = r["Data_dt"].date() if pd.notna(r["Data_dt"]) else date.today()
    ss["f_titulo"] = r["Titulo"]
    ss["f_descritivo"] = r["Descritivo"]
    ss["f_pagamento"] = r["Pagamento"]
    itens = itens_json_para_df(r["ItensJSON"], r["Itens"], r["Total"])
    ss["itens_base"] = itens.to_dict(orient="records") or [dict(LINHA_VAZIA)]
    ss["chave_tabela"] = str(uuid.uuid4())
    ss["ultimo_orcamento"] = None
    ss["aba"] = ABA_NOVO


def cb_cancelar_edicao():
    _limpar_form()


def cb_fechar_ultimo():
    st.session_state["ultimo_orcamento"] = None


# =========================
# CABEÇALHO
# =========================
@st.cache_resource(show_spinner=False)
def logo_cabecalho_b64(caminho: str, lado: int = 200) -> str:
    """Logo reduzida em PNG (mantém a transparência do círculo) para o cabeçalho."""
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(caminho) as im:
            im = im.convert("RGBA")
            im.thumbnail((lado, lado))
            buf = BytesIO()
            im.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def logo_img_tag(lado: int = 200) -> str:
    logo = get_logo_path()
    b64 = logo_cabecalho_b64(logo, lado) if logo else ""
    return f'<img src="data:image/png;base64,{b64}" alt="Logo P&amp;S Refrigeração">' if b64 else ""


@st.cache_resource(show_spinner=False)
def icone_pagina():
    """Logo como ícone da aba do navegador (cai no emoji se não houver logo)."""
    try:
        from PIL import Image

        im = Image.open(get_logo_path()).convert("RGBA")
        im.thumbnail((64, 64))
        return im
    except Exception:
        return "❄️"


def render_cabecalho():
    st.markdown(CSS, unsafe_allow_html=True)
    render_cabecalho_marca()


def render_cabecalho_marca():
    nome = EMPRESA.get("nome_fantasia") or APP_NOME
    st.markdown(
        f"""
        <div class="ps-hero">
            {logo_img_tag(200)}
            <div>
                <div class="ps-nome">{nome}</div>
                <div class="ps-slogan">Soluções em conforto</div>
            </div>
        </div>
        <div class="ps-termica"></div>
        """,
        unsafe_allow_html=True,
    )


# =========================
# ABA 1 — NOVO / EDIÇÃO
# =========================
def render_novo():
    ss = st.session_state
    editando = ss.get("id_edicao") is not None

    if editando:
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.info(f"Editando o orçamento **Nº {formatar_id_pdf(ss['id_edicao'])}** ({ss['id_edicao']})")
        c2.button("Cancelar edição", on_click=cb_cancelar_edicao, width="stretch")

    with st.form("form_orcamento", border=True):
        st.markdown("##### :material/person: Dados do cliente")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Cliente", key="f_cliente", placeholder="Nome do cliente")
            st.text_input("WhatsApp", key="f_whats", placeholder="(11) 91234-5678")
        with col2:
            st.date_input("Data", key="f_data", format="DD/MM/YYYY")
            st.selectbox("Status", STATUS_OPCOES, key="f_status")

        st.markdown("##### :material/receipt_long: Itens do orçamento")
        st.caption("A última linha fica em branco para você adicionar um novo item.")
        df_init = garantir_linha_em_branco(pd.DataFrame(ss["itens_base"]))
        tabela = st.data_editor(
            df_init,
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=ss["chave_tabela"],
            column_config={
                "Item": st.column_config.TextColumn("Item", width="large"),
                "Qtd": st.column_config.NumberColumn("Qtd", min_value=1, step=1, format="%d"),
                "Valor Unit.": st.column_config.NumberColumn(
                    "Valor Unit.", min_value=0.0, step=0.5, format="R$ %.2f"
                ),
            },
        )

        with st.expander("Detalhes para o PDF (opcional)", icon=":material/description:", expanded=bool(ss.get("f_descritivo") or ss.get("f_titulo"))):
            st.text_input("Título do orçamento", key="f_titulo",
                          placeholder="Ex.: Instalação de sistema de climatização")
            st.text_area("Descritivo dos serviços e equipamentos", key="f_descritivo", height=130,
                         placeholder="Descreva o serviço. Linhas começando com - viram tópicos no PDF.")
            st.text_area("Forma de pagamento", key="f_pagamento", height=80,
                         placeholder="Vazio = texto padrão (definida em comum acordo entre as partes).")

        rotulo = "Salvar alterações" if editando else "Salvar orçamento"
        enviado = st.form_submit_button(rotulo, icon=":material/save:", type="primary", width="stretch")

    if enviado:
        processar_salvar(tabela, editando)

    # Ações do último orçamento salvo
    os_ult = ss.get("ultimo_orcamento")
    if os_ult:
        df = ler_base()
        linha = df[df["ID"] == os_ult]
        if linha.empty:
            ss["ultimo_orcamento"] = None
        else:
            r = linha.iloc[0]
            with st.container(border=True):
                st.markdown(
                    f"✅ **Orçamento Nº {formatar_id_pdf(r['ID'])}** — {r['Cliente']} · "
                    f"<span class='dinheiro'>{fmt_brl(r['Total'])}</span>",
                    unsafe_allow_html=True,
                )
                c_pdf, c_whats, c_fechar = st.columns(3)
                acoes_envio(r, c_pdf, c_whats, prefixo="ult")
                with c_fechar:
                    st.button("Novo orçamento", icon=":material/add:", on_click=cb_fechar_ultimo, width="stretch", key="ult_fechar")


def acoes_envio(r, col_pdf, col_whats, prefixo: str):
    """Botões de PDF e WhatsApp (com link de aceite) de um orçamento."""
    tok = r["Token"]
    if not tok and r["Status"] == "Pendente":
        try:
            tok = garantir_token(r["ID"], tok)
        except Exception:
            tok = ""
    link = link_cliente(tok) if r["Status"] == "Pendente" else ""
    with col_pdf:
        st.download_button(
            "PDF",
            pdf_orcamento_bytes(r, link),
            icon=":material/picture_as_pdf:",
            file_name=nome_arquivo_pdf(r["ID"], r["Cliente"]),
            mime="application/pdf",
            width="stretch",
            key=f"{prefixo}_pdf_{r['ID']}",
        )
    with col_whats:
        url = whatsapp_url(r["WhatsApp"], mensagem_whatsapp(r["Cliente"], r["ID"], r["Total"], link))
        if url:
            st.link_button("WhatsApp", url, icon=":material/chat:", width="stretch",
                           help="Envia o orçamento com o link para o cliente aprovar ou recusar.")
        else:
            st.button("WhatsApp", icon=":material/chat:", disabled=True, width="stretch", key=f"{prefixo}_wpp_{r['ID']}",
                      help="Este orçamento não tem número de WhatsApp.")
    return link


def processar_salvar(tabela, editando: bool):
    ss = st.session_state
    cliente = str(ss.get("f_cliente", "")).strip()
    if not cliente:
        st.error("Informe o nome do cliente.")
        return

    tabela_limpa, total, itens_txt, itens_json = limpar_calcular(tabela)
    if tabela_limpa.empty:
        st.error("Adicione pelo menos um item ao orçamento.")
        return

    data_ref = ss.get("f_data") or date.today()
    reg = {
        "Cliente": cliente,
        "WhatsApp": apenas_digitos(ss.get("f_whats", "")),
        "Status": ss.get("f_status", "Pendente"),
        "Total": total,
        "Itens": itens_txt,
        "ItensJSON": itens_json,
        "Titulo": ss.get("f_titulo", ""),
        "Descritivo": ss.get("f_descritivo", ""),
        "Pagamento": ss.get("f_pagamento", ""),
    }

    try:
        if editando:
            os_id = ss["id_edicao"]
            atualizar_orcamento(os_id, reg, data_ref)
        else:
            os_id, _ = salvar_orcamento(reg, data_ref)
    except Exception as e:
        st.error(f"Não foi possível salvar no banco. Tente novamente. Detalhe: {e}")
        return

    ss["ultimo_orcamento"] = os_id
    ss["_reset_form"] = True
    ss["_flash"] = f"Orçamento Nº {formatar_id_pdf(os_id)} salvo com sucesso!"
    st.rerun()


# =========================
# ABA 2 — HISTÓRICO
# =========================
@st.dialog("Excluir orçamento")
def dialog_excluir(os_id: str, cliente: str, total: float):
    st.write(f"Tem certeza que deseja excluir o orçamento **Nº {formatar_id_pdf(os_id)}** de **{cliente}** ({fmt_brl(total)})?")
    st.caption("Esta ação não pode ser desfeita.")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", width="stretch", key="dlg_cancelar"):
        st.rerun()
    if c2.button("Excluir", icon=":material/delete:", type="primary", width="stretch", key="dlg_excluir"):
        try:
            excluir_orcamento(os_id)
        except Exception as e:
            st.error(f"Não foi possível excluir: {e}")
            return
        st.session_state["_limpar_selecao"] = True
        st.session_state["_flash"] = f"Orçamento Nº {formatar_id_pdf(os_id)} excluído."
        st.rerun()


def cb_selecionar_linha():
    """Clique numa linha da tabela -> seleciona o orçamento e mostra as ações."""
    ss = st.session_state
    evento = ss.get("h_tabela") or {}
    try:
        selecao = evento["selection"] if isinstance(evento, dict) else evento.selection
        linhas = selecao["rows"] if isinstance(selecao, dict) else selecao.rows
    except Exception:
        linhas = []
    ids = ss.get("_h_ids", [])
    if linhas and 0 <= linhas[0] < len(ids):
        ss["h_sel"] = ids[linhas[0]]


def render_historico():
    df = ler_base()
    if df.empty:
        st.info("Ainda não há orçamentos salvos.")
        return

    c_busca, c_status = st.columns([2, 1])
    busca = c_busca.text_input("Buscar", key="h_busca", placeholder="Cliente, nº ou item")
    status_sel = c_status.multiselect("Status", STATUS_OPCOES, key="h_status", placeholder="Todos")

    dff = df.copy()
    if busca.strip():
        termo = busca.strip().lower()
        alvo = (
            dff["Cliente"].str.lower() + " " + dff["ID"].str.lower() + " "
            + dff["ID"].map(formatar_id_pdf).str.lower() + " " + dff["Itens"].str.lower()
        )
        dff = dff[alvo.str.contains(termo, regex=False, na=False)]
    if status_sel:
        dff = dff[dff["Status"].isin(status_sel)]
    dff = dff.sort_values(by="ID", key=lambda s: s.map(id_key), ascending=False)

    if dff.empty:
        st.warning("Nenhum orçamento encontrado com esses filtros.")
        return

    rotulos = {
        r["ID"]: f"Nº {formatar_id_pdf(r['ID'])} | {r['Data']} | {r['Cliente']} | {r['Status']} ({fmt_brl(r['Total'])})"
        for r in dff.to_dict(orient="records")
    }
    opcoes = list(rotulos.keys())
    # Sem "index=" aqui: o valor inicial vem do session_state (evita aviso do Streamlit)
    if st.session_state.get("h_sel") not in opcoes:
        st.session_state["h_sel"] = None
    sel = st.selectbox(
        "Selecione um orçamento",
        opcoes,
        format_func=lambda i: rotulos.get(i, i),
        key="h_sel",
        placeholder=f"{len(opcoes)} orçamento(s) — escolha um para ver PDF, WhatsApp, Editar...",
    )

    st.caption("👆 Escolha na lista acima **ou toque numa linha da tabela** para abrir as ações (PDF, WhatsApp, Editar, Status, Excluir).")
    tabela = dff[["ID", "Data", "Cliente", "WhatsApp", "Status", "Total", "Itens"]].copy()
    tabela.insert(0, "Nº", tabela["ID"].map(formatar_id_pdf))
    st.session_state["_h_ids"] = tabela["ID"].tolist()
    st.dataframe(
        tabela,
        width="stretch",
        height=320,
        hide_index=True,
        key="h_tabela",
        on_select=cb_selecionar_linha,
        selection_mode="single-row",
        column_config={
            "Nº": st.column_config.TextColumn("Nº", width="small"),
            "ID": st.column_config.TextColumn("ID", width="small"),
            "Total": st.column_config.NumberColumn("Total", format="R$ %.2f"),
            "Itens": st.column_config.TextColumn("Itens", width="large"),
        },
    )
    st.caption(f"{len(dff)} orçamento(s) · Total listado: {fmt_brl(dff['Total'].sum())}")

    if sel:
        render_detalhe(df[df["ID"] == sel].iloc[0])


def render_detalhe(r):
    with st.container(border=True):
        c_info, c_valor = st.columns([3, 1])
        with c_info:
            st.markdown(
                f"""
                <div class="det-num">Nº {formatar_id_pdf(r['ID'])} &nbsp;{badge_status(r['Status'])}</div>
                <div class="det-cli">{r['Cliente']}</div>
                <div class="rotulo">{('WhatsApp ' + r['WhatsApp']) if r['WhatsApp'] else 'Sem WhatsApp'}, emitido em {r['Data'] or '—'}</div>
                """,
                unsafe_allow_html=True,
            )
            if fmt_datahora(r["RespostaEm"]):
                icone = "✅" if r["Status"] in STATUS_APROVADOS else "❌"
                st.markdown(
                    f"{icone} **Resposta do cliente** em {fmt_datahora(r['RespostaEm'])}"
                    + (f" — _{r['RespostaObs']}_" if r["RespostaObs"] else "")
                )
        with c_valor:
            st.markdown(
                f'<div class="rotulo">Valor total</div><div class="valor-grande">{fmt_brl(r["Total"])}</div>'
                '<div class="valor-sub"></div>',
                unsafe_allow_html=True,
            )

        itens = itens_json_para_df(r["ItensJSON"], r["Itens"], r["Total"])
        itens_limpos, _, _, _ = limpar_calcular(itens)
        if not itens_limpos.empty:
            st.dataframe(
                itens_limpos,
                width="stretch",
                hide_index=True,
                column_config={
                    "Valor Unit.": st.column_config.NumberColumn("Valor Unit.", format="R$ %.2f"),
                    "Subtotal": st.column_config.NumberColumn("Subtotal", format="R$ %.2f"),
                },
            )

        os_id = r["ID"]
        c_pdf, c_whats, c_edit, c_status, c_del = st.columns(5)
        link = acoes_envio(r, c_pdf, c_whats, prefixo="h")
        with c_edit:
            st.button("Editar", icon=":material/edit:", on_click=cb_editar, args=(os_id,), width="stretch", key=f"h_edit_{os_id}")
        with c_status:
            with st.popover("Status", icon=":material/sync_alt:", width="stretch"):
                idx = STATUS_OPCOES.index(r["Status"]) if r["Status"] in STATUS_OPCOES else 0
                novo = st.radio("Novo status", STATUS_OPCOES, index=idx, key=f"h_novo_status_{os_id}")
                if st.button("Salvar status", type="primary", width="stretch", key=f"h_salvar_status_{os_id}"):
                    try:
                        atualizar_status(os_id, novo)
                    except Exception as e:
                        st.error(f"Não foi possível alterar: {e}")
                    else:
                        st.session_state["_flash"] = f"Status do Nº {formatar_id_pdf(os_id)} alterado para {novo}."
                        st.rerun()
        with c_del:
            if st.button("Excluir", icon=":material/delete:", width="stretch", key=f"h_del_{os_id}"):
                dialog_excluir(os_id, r["Cliente"], float(r["Total"]))

        if link:
            st.caption("🔗 Link de aceite do cliente (vai junto na mensagem do WhatsApp e no QR code do PDF):")
            st.code(link, language=None)


# =========================
# ABA 3 — FINANCEIRO
# =========================
def _fim_do_mes(d: date) -> date:
    prox = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    return prox - timedelta(days=1)


def _mes_anterior(d: date) -> date:
    """Primeiro dia do mês anterior a d."""
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def calcular_periodo(chave: str, hoje: date, dmin: date, dmax: date, intervalo=None):
    """Retorna (inicio, fim, inicio_anterior, fim_anterior); anterior = None quando não se aplica."""
    if chave == "Este mês":
        ini = hoje.replace(day=1)
        fim = _fim_do_mes(hoje)
        p_ini = _mes_anterior(hoje)
        p_fim = min(p_ini.replace(day=1) + timedelta(days=hoje.day - 1), _fim_do_mes(p_ini))
        return ini, fim, p_ini, p_fim
    if chave == "Mês passado":
        ini = _mes_anterior(hoje)
        fim = _fim_do_mes(ini)
        p_ini = _mes_anterior(ini)
        return ini, fim, p_ini, _fim_do_mes(p_ini)
    if chave == "Este ano":
        ini = date(hoje.year, 1, 1)
        fim = date(hoje.year, 12, 31)
        p_ini = date(hoje.year - 1, 1, 1)
        try:
            p_fim = hoje.replace(year=hoje.year - 1)
        except ValueError:  # 29/02
            p_fim = date(hoje.year - 1, 2, 28)
        return ini, fim, p_ini, p_fim
    if chave == "Ano passado":
        return date(hoje.year - 1, 1, 1), date(hoje.year - 1, 12, 31), date(hoje.year - 2, 1, 1), date(hoje.year - 2, 12, 31)
    if chave == "Últimos 90 dias":
        ini = hoje - timedelta(days=89)
        return ini, hoje, ini - timedelta(days=90), ini - timedelta(days=1)
    if chave == "Personalizado" and intervalo:
        ini, fim = intervalo
        dias = (fim - ini).days + 1
        return ini, fim, ini - timedelta(days=dias), ini - timedelta(days=1)
    return dmin, dmax, None, None  # "Tudo"


def filtrar_periodo(df: pd.DataFrame, ini: date, fim: date) -> pd.DataFrame:
    m = (df["Data_dt"] >= pd.Timestamp(ini)) & (df["Data_dt"] <= pd.Timestamp(fim))
    return df[m]


def resumo(df: pd.DataFrame) -> dict:
    conc = df[df["Status"] == "Concluído"]
    abertos = df[df["Status"].isin(STATUS_ABERTOS)]
    qtd = len(df)
    validos = len(df[df["Status"] != "Cancelado"])
    aprovados = len(df[df["Status"].isin(STATUS_APROVADOS)])
    return {
        "faturado": float(conc["Total"].sum()),
        "qtd_concluidos": len(conc),
        "aberto": float(abertos["Total"].sum()),
        "qtd_abertos": len(abertos),
        "qtd": qtd,
        "ticket": float(conc["Total"].mean()) if len(conc) else 0.0,
        "aprovacao": (aprovados / validos) if validos else 0.0,
    }


def delta_pct(atual: float, anterior):
    if anterior is None or anterior == 0:
        return None
    v = (atual - anterior) / anterior
    return f"{'+' if v >= 0 else ''}{fmt_pct(v)}"


def _eixo_reais():
    return alt.Axis(
        title=None,
        labelExpr=(
            "datum.value >= 1000 "
            "? 'R$ ' + replace(format(datum.value / 1000, '.1~f'), '.', ',') + ' mil' "
            ": 'R$ ' + format(datum.value, '.0f')"
        ),
        grid=True,
    )


def _escala_status(status_presentes):
    dominio = [s for s in ORDEM_GRAFICO if s in status_presentes] + sorted(
        s for s in status_presentes if s not in ORDEM_GRAFICO
    )
    return alt.Scale(domain=dominio, range=[CORES_STATUS.get(s, COR_OUTROS) for s in dominio])


def grafico_mensal(df: pd.DataFrame):
    base = df.dropna(subset=["Data_dt"]).copy()
    base["MesData"] = base["Data_dt"].dt.to_period("M").dt.to_timestamp()
    m = base.groupby(["MesData", "Status"], as_index=False).agg(Total=("Total", "sum"), Qtd=("ID", "count"))
    m["Mês"] = m["MesData"].map(lambda d: f"{MESES[d.month - 1]}/{str(d.year)[-2:]}")
    m["Valor"] = m["Total"].map(fmt_brl)
    ordem_meses = [f"{MESES[d.month - 1]}/{str(d.year)[-2:]}" for d in sorted(m["MesData"].unique())]
    m["ordem_status"] = m["Status"].map(lambda s: ORDEM_GRAFICO.index(s) if s in ORDEM_GRAFICO else 99)

    return (
        alt.Chart(m)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke=PAINEL, strokeWidth=2)
        .encode(
            x=alt.X("Mês:N", sort=ordem_meses, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Total:Q", axis=_eixo_reais(), stack="zero"),
            color=alt.Color("Status:N", scale=_escala_status(set(m["Status"])),
                            legend=alt.Legend(orient="bottom", title=None)),
            order=alt.Order("ordem_status:Q"),
            tooltip=[
                alt.Tooltip("Mês:N"),
                alt.Tooltip("Status:N"),
                alt.Tooltip("Valor:N", title="Valor"),
                alt.Tooltip("Qtd:Q", title="Orçamentos"),
            ],
        )
        .properties(height=320)
    )


def grafico_status(df: pd.DataFrame):
    s = df.groupby("Status", as_index=False).agg(Total=("Total", "sum"), Qtd=("ID", "count"))
    tot = s["Total"].sum()
    s["Valor"] = s["Total"].map(fmt_brl)
    s["Part"] = s["Total"].map(lambda v: fmt_pct(v / tot) if tot else "0%")
    s["ordem_status"] = s["Status"].map(lambda x: ORDEM_GRAFICO.index(x) if x in ORDEM_GRAFICO else 99)
    return (
        alt.Chart(s)
        .mark_arc(innerRadius=62, outerRadius=110, cornerRadius=3, stroke=PAINEL, strokeWidth=2)
        .encode(
            theta=alt.Theta("Total:Q", stack=True),
            order=alt.Order("ordem_status:Q"),
            color=alt.Color("Status:N", scale=_escala_status(set(s["Status"])),
                            legend=alt.Legend(orient="bottom", title=None, columns=2)),
            tooltip=[
                alt.Tooltip("Status:N"),
                alt.Tooltip("Valor:N", title="Valor"),
                alt.Tooltip("Part:N", title="Participação"),
                alt.Tooltip("Qtd:Q", title="Orçamentos"),
            ],
        )
        .properties(height=320)
    )


def grafico_clientes(df: pd.DataFrame, top: int = 10):
    conc = df[df["Status"] == "Concluído"]
    if conc.empty:
        return None
    c = conc.groupby("Cliente", as_index=False).agg(Total=("Total", "sum"), Qtd=("ID", "count"))
    c = c.sort_values("Total", ascending=False).head(top)
    c["Valor"] = c["Total"].map(fmt_brl)
    base = alt.Chart(c).encode(
        y=alt.Y("Cliente:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)),
        x=alt.X("Total:Q", axis=None, scale=alt.Scale(domainMax=float(c["Total"].max()) * 1.3)),
        tooltip=[alt.Tooltip("Cliente:N"), alt.Tooltip("Valor:N", title="Faturado"),
                 alt.Tooltip("Qtd:Q", title="Serviços")],
    )
    barras = base.mark_bar(color=CORES_STATUS["Concluído"], cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
    rotulos = base.mark_text(align="left", dx=6, fontSize=12, color=TINTA_SUAVE).encode(text="Valor:N")
    return (barras + rotulos).properties(height=max(140, 34 * len(c)))


def render_financeiro():
    df = ler_base()
    if df.empty:
        st.info("Sem dados ainda.")
        return

    hoje = date.today()
    validas = df["Data_dt"].dropna()
    dmin = validas.min().date() if not validas.empty else hoje
    dmax = validas.max().date() if not validas.empty else hoje

    # ---- Filtros
    with st.container(border=True):
        c_per, c_cli = st.columns([3, 2])
        with c_per:
            periodo = st.segmented_control("Período", PERIODOS, default="Este ano", key="fin_periodo")
            periodo = periodo or "Este ano"
            intervalo = None
            if periodo == "Personalizado":
                valor = st.date_input("Intervalo", value=(dmin, dmax), format="DD/MM/YYYY", key="fin_intervalo")
                if isinstance(valor, (tuple, list)) and len(valor) == 2:
                    intervalo = (valor[0], valor[1])
                else:
                    st.caption("Selecione a data inicial e a final.")
                    intervalo = (dmin, dmax)
        with c_cli:
            clientes = st.multiselect(
                "Clientes", sorted(c for c in df["Cliente"].unique() if c), key="fin_clientes", placeholder="Todos"
            )

    ini, fim, p_ini, p_fim = calcular_periodo(periodo, hoje, dmin, dmax, intervalo)

    base = df[df["Cliente"].isin(clientes)] if clientes else df
    atual = filtrar_periodo(base, ini, fim)
    anterior = filtrar_periodo(base, p_ini, p_fim) if p_ini else None

    st.caption(
        f"📅 {fmt_data(ini)} a {fmt_data(fim)}"
        + (f" · comparando com {fmt_data(p_ini)} a {fmt_data(p_fim)}" if p_ini else "")
    )
    sem_data = int(df["Data_dt"].isna().sum())
    if sem_data:
        st.caption(f"⚠️ {sem_data} orçamento(s) sem data válida não entram nos filtros de período.")

    # ---- KPIs
    r = resumo(atual)
    ra = resumo(anterior) if anterior is not None else None
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Faturado", fmt_brl(r["faturado"]), delta_pct(r["faturado"], ra["faturado"] if ra else None),
              border=True, help="Soma dos orçamentos com status Concluído no período.")
    k2.metric("Em aberto", fmt_brl(r["aberto"]), f"{r['qtd_abertos']} orçamento(s)", delta_color="off",
              border=True, help="Pendente + Aprovado + Em Andamento: valor que ainda pode entrar.")
    k3.metric("Orçamentos", r["qtd"], (r["qtd"] - ra["qtd"]) if ra else None,
              border=True, help="Quantidade de orçamentos emitidos no período (todos os status).")
    k4.metric("Ticket médio", fmt_brl(r["ticket"]), delta_pct(r["ticket"], ra["ticket"] if ra else None),
              border=True, help="Valor médio dos orçamentos concluídos.")
    k5.metric("Aprovação", fmt_pct(r["aprovacao"]),
              (f"{'+' if r['aprovacao'] - ra['aprovacao'] >= 0 else ''}{(r['aprovacao'] - ra['aprovacao']) * 100:.1f} p.p.".replace(".", ",")
               if ra and ra["qtd"] else None),
              border=True, help="(Aprovado + Em Andamento + Concluído) ÷ orçamentos não cancelados.")

    if atual.empty:
        st.info("Nenhum orçamento neste período.")
        return

    # ---- Gráficos
    g1, g2 = st.columns([2, 1])
    with g1:
        with st.container(border=True):
            st.markdown("##### :material/bar_chart: Evolução mensal por status")
            st.altair_chart(grafico_mensal(atual), width="stretch")
    with g2:
        with st.container(border=True):
            st.markdown("##### :material/donut_large: Distribuição por status")
            st.altair_chart(grafico_status(atual), width="stretch")

    g3, g4 = st.columns(2)
    with g3:
        with st.container(border=True):
            st.markdown("##### :material/leaderboard: Top clientes (faturado)")
            ch = grafico_clientes(atual)
            if ch is None:
                st.caption("Nenhum orçamento concluído no período.")
            else:
                st.altair_chart(ch, width="stretch")
    with g4:
        with st.container(border=True):
            st.markdown("##### :material/pending_actions: Pendências para acompanhar")
            abertos = atual[atual["Status"].isin(STATUS_ABERTOS)].copy()
            if abertos.empty:
                st.caption("Nenhum orçamento em aberto. 🎉")
            else:
                abertos["Dias"] = (pd.Timestamp(hoje) - abertos["Data_dt"]).dt.days
                abertos = abertos.sort_values("Dias", ascending=False)
                abertos.insert(0, "Nº", abertos["ID"].map(formatar_id_pdf))
                st.dataframe(
                    abertos[["Nº", "Cliente", "Data", "Dias", "Status", "Total"]],
                    width="stretch",
                    hide_index=True,
                    height=min(38 * (len(abertos) + 1) + 4, 330),
                    column_config={
                        "Dias": st.column_config.NumberColumn("Dias", help="Dias desde a data do orçamento", format="%d"),
                        "Total": st.column_config.NumberColumn("Valor", format="R$ %.2f"),
                    },
                )

    # ---- Detalhe + exportação
    with st.expander(f"Orçamentos do período ({len(atual)})", icon=":material/table_rows:"):
        lista = atual.sort_values(by="ID", key=lambda s: s.map(id_key), ascending=False)
        lista = lista[["ID", "Data", "Cliente", "WhatsApp", "Status", "Total", "Itens"]].copy()
        lista.insert(0, "Nº", lista["ID"].map(formatar_id_pdf))
        st.dataframe(
            lista,
            width="stretch",
            hide_index=True,
            column_config={"Total": st.column_config.NumberColumn("Total", format="R$ %.2f")},
        )
        csv = lista.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "Exportar para Excel (CSV)",
            csv,
            icon=":material/download:",
            file_name=f"orcamentos_{ini:%Y%m%d}_{fim:%Y%m%d}.csv",
            mime="text/csv",
            key="fin_csv",
        )


# =========================
# APP
# =========================
# =========================
# PÁGINA DO CLIENTE (link do WhatsApp) — sem senha, só o orçamento do link
# =========================
CSS_CLIENTE = """
<style>
[data-testid="stToolbar"], [data-testid="stHeader"], [data-testid="stSidebar"] { display: none !important; }
.block-container { max-width: 760px; padding-top: 1rem; }
.cli-total { font-family: "Saira Semi Condensed", sans-serif; font-size: 2.3rem; font-weight: 700;
  color: var(--dinheiro); line-height: 1.1; }
.cli-contato { color: var(--tinta-suave); font-size: .9rem; margin: -6px 0 12px; }
</style>
"""


def _validade(r):
    dt = r["Data_dt"]
    if pd.isna(dt):
        return None
    return dt.date() + timedelta(days=int(EMPRESA.get("validade_dias") or 5))


def _contato_empresa_url(texto: str):
    """WhatsApp da empresa: usa o campo 'whatsapp' ou o telefone, se for celular."""
    numero = EMPRESA.get("whatsapp") or ""
    if not numero:
        tel = apenas_digitos(EMPRESA.get("telefone", ""))
        if len(tel) == 11 and tel[2] == "9":  # DDD + celular (9xxxx-xxxx)
            numero = tel
    return whatsapp_url(numero, texto) if numero else None


@st.dialog("Aprovar orçamento")
def dialog_aprovar(token: str, numero: str):
    st.write(f"Você confirma a **aprovação** do orçamento **Nº {numero}**?")
    obs = st.text_area("Observação (opcional)", placeholder="Ex.: melhor dia para a instalação", key="dlg_obs_ap")
    if st.button("Confirmar aprovação", icon=":material/check_circle:", type="primary", width="stretch", key="dlg_ok_ap"):
        _responder(token, True, obs)


@st.dialog("Recusar orçamento")
def dialog_recusar(token: str, numero: str):
    st.write(f"Você deseja **recusar** o orçamento **Nº {numero}**?")
    obs = st.text_area("Motivo (opcional)", placeholder="Ex.: valor acima do esperado", key="dlg_obs_rc")
    if st.button("Confirmar recusa", type="primary", width="stretch", key="dlg_ok_rc"):
        _responder(token, False, obs)


def _responder(token: str, aprovado: bool, obs: str):
    try:
        ok = registrar_resposta(token, aprovado, obs)
    except Exception:
        st.error("Não foi possível registrar agora. Tente novamente em instantes.")
        return
    st.session_state["_resp_cliente"] = "ok" if ok else "ja"
    st.rerun()


def render_pagina_cliente(token: str):
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(CSS_CLIENTE, unsafe_allow_html=True)
    render_cabecalho_marca()
    contato = "   |   ".join(x for x in [EMPRESA.get("telefone", ""), EMPRESA.get("email", "")] if x)
    if contato:
        st.markdown(f'<div class="cli-contato">{contato}</div>', unsafe_allow_html=True)

    try:
        r = buscar_por_token(str(token).strip())
    except Exception:
        st.error("Não foi possível carregar o orçamento agora. Tente novamente em instantes.")
        return
    if r is None:
        st.error("Orçamento não encontrado. Confira o link recebido ou fale com a nossa equipe.")
        return

    numero = formatar_id_pdf(r["ID"])
    resp = st.session_state.pop("_resp_cliente", None)
    if resp == "ok":
        st.balloons() if r["Status"] == "Aprovado" else None
        st.success("Resposta registrada com sucesso. Obrigado! Nossa equipe já foi informada.")
    elif resp == "ja":
        st.info("Este orçamento já tinha uma resposta registrada.")

    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            if r["Titulo"]:
                st.markdown(f"**{r['Titulo']}**")
            st.markdown(f"Orçamento **Nº {numero}** &nbsp;{badge_status(r['Status'])}", unsafe_allow_html=True)
            st.markdown(f"Cliente: **{r['Cliente']}**")
            val = _validade(r)
            st.caption(f"Emitido em {r['Data']}" + (f" · válido até {fmt_data(val)}" if val else ""))
        with c2:
            st.markdown(f'<div class="rotulo">Valor total</div><div class="cli-total">{fmt_brl(r["Total"])}</div><div class="valor-sub"></div>',
                        unsafe_allow_html=True)

        itens = itens_json_para_df(r["ItensJSON"], r["Itens"], r["Total"])
        itens_limpos, _, _, _ = limpar_calcular(itens)
        if not itens_limpos.empty:
            st.dataframe(
                itens_limpos,
                width="stretch",
                hide_index=True,
                column_config={
                    "Valor Unit.": st.column_config.NumberColumn("Valor Unit.", format="R$ %.2f"),
                    "Subtotal": st.column_config.NumberColumn("Subtotal", format="R$ %.2f"),
                },
            )
        if r["Descritivo"]:
            st.markdown("**Descritivo dos serviços**")
            st.markdown(r["Descritivo"])
        st.markdown("**Forma de pagamento**")
        st.write(r["Pagamento"] or EMPRESA.get("pagamento_padrao", ""))

        st.download_button(
            "Baixar orçamento em PDF",
            pdf_orcamento_bytes(r, link_cliente(token)),
            icon=":material/picture_as_pdf:",
            file_name=nome_arquivo_pdf(r["ID"], r["Cliente"]),
            mime="application/pdf",
            width="stretch",
            key="cli_pdf",
        )

    status = r["Status"]
    if status == "Pendente":
        val = _validade(r)
        if val and val < date.today():
            st.warning(f"A validade deste orçamento terminou em {fmt_data(val)}. "
                       "Você ainda pode responder, e confirmaremos os valores com você.")
        st.markdown("#### O que você decide?")
        b1, b2 = st.columns(2)
        if b1.button("Aprovar orçamento", icon=":material/check_circle:", type="primary", width="stretch", key="cli_aprovar"):
            dialog_aprovar(token, numero)
        if b2.button("Recusar", icon=":material/cancel:", width="stretch", key="cli_recusar"):
            dialog_recusar(token, numero)
    elif status in ("Aprovado", "Recusado"):
        quando = fmt_datahora(r["RespostaEm"])
        if status == "Aprovado":
            st.success(f"✅ Orçamento **aprovado**{' em ' + quando if quando else ''}. "
                       "Em breve entraremos em contato para agendar o serviço.")
        else:
            st.warning(f"Orçamento **recusado**{' em ' + quando if quando else ''}. "
                       "Se quiser rever algum ponto, fale com a gente.")
        url = _contato_empresa_url(
            f"Olá! Sou {r['Cliente']} e acabei de {'aprovar' if status == 'Aprovado' else 'recusar'} "
            f"o orçamento Nº {numero}."
        )
        if url:
            st.link_button("Avisar pelo WhatsApp", url, icon=":material/chat:", width="stretch")
    else:
        st.info(f"Situação atual do orçamento: **{status}**.")


# =========================
# ACESSO (senha da área de gestão)
# =========================
def senha_configurada() -> str:
    try:
        return str(st.secrets.get("APP_SENHA") or "")
    except Exception:
        return ""


def cb_sair():
    st.session_state["autenticado"] = False


def precisa_login() -> bool:
    return bool(senha_configurada()) and not st.session_state.get("autenticado")


def render_login():
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        f'<div class="ps-login">{logo_img_tag(340)}<p>Gestão de orçamentos e serviços</p></div>',
        unsafe_allow_html=True,
    )
    _, meio, _ = st.columns([1, 1.4, 1])
    with meio:
        with st.form("login", border=True):
            digitada = st.text_input("Senha de acesso", type="password", key="login_senha")
            entrar = st.form_submit_button("Entrar", type="primary", width="stretch")
        if entrar:
            if hmac.compare_digest(str(digitada).encode(), senha_configurada().encode()):
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta. Confira e tente de novo.")


def render_respostas_recentes():
    """Aviso no topo quando clientes responderam pelo link nos últimos 7 dias."""
    try:
        df = ler_base()
    except Exception:
        return
    if df.empty:
        return
    limite = datetime.now(timezone.utc) - timedelta(days=7)
    recentes = []
    for r in df.to_dict(orient="records"):
        v = dt_resposta(r["RespostaEm"])
        if v and v >= limite:
            recentes.append((v, r))
    if not recentes:
        return
    recentes.sort(key=lambda x: x[0], reverse=True)
    linhas = []
    for v, r in recentes[:5]:
        acao = "aprovou ✅" if r["Status"] in STATUS_APROVADOS else ("recusou ❌" if r["Status"] == "Recusado" else "respondeu")
        linhas.append(f"- **{r['Cliente']}** {acao} o Nº {formatar_id_pdf(r['ID'])} "
                      f"({fmt_brl(r['Total'])}) — {fmt_datahora(v)}"
                      + (f" · _{r['RespostaObs']}_" if r["RespostaObs"] else ""))
    with st.expander(f"Respostas de clientes nos últimos 7 dias ({len(recentes)})", icon=":material/notifications_active:", expanded=True):
        st.markdown("\n".join(linhas))


# =========================
# APP
# =========================
def criar_abas():
    """Abas com estado (permite o botão Editar abrir a aba certa)."""
    nomes = [ABA_NOVO, ABA_HIST, ABA_FIN]
    try:
        return st.tabs(nomes, key="aba", on_change="rerun")
    except TypeError:  # Streamlit antigo: abas sem troca automática
        return st.tabs(nomes)


def main():
    token = st.query_params.get("orc")
    if token:
        st.set_page_config(page_title="Orçamento | P&S Refrigeração", page_icon=icone_pagina(), layout="centered")
        render_pagina_cliente(token)
        return

    st.set_page_config(page_title="P&S Refrigeração | Gestão", page_icon=icone_pagina(), layout="wide")
    init_state()
    if precisa_login():
        render_login()
        return
    render_cabecalho()
    if not senha_configurada():
        st.warning("A área de gestão está sem senha. Configure `APP_SENHA` em Settings → Secrets "
                   "no Streamlit Cloud para proteger os dados dos clientes.", icon="⚠️")
    else:
        _, c_sair = st.columns([6, 1])
        c_sair.button("Sair", icon=":material/logout:", on_click=cb_sair, width="stretch", key="btn_sair")
    render_respostas_recentes()

    tab_novo, tab_hist, tab_fin = criar_abas()
    with tab_novo:
        render_novo()
    with tab_hist:
        render_historico()
    with tab_fin:
        render_financeiro()


if __name__ == "__main__":
    main()
