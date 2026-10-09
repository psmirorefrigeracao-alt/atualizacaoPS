# app.py — P&S REFRIGERAÇÃO | Gestão de orçamentos (Streamlit + Supabase Postgres)
# =============================================================================
# Versão 2.0
# - Mesma tabela do Supabase (public.orcamentos) e mesmos campos: nada muda no banco
# - Botão "Editar" leva direto para a aba de edição, já preenchida
# - Histórico com busca, filtro de status, itens, WhatsApp, troca rápida de status
#   e exclusão com confirmação
# - Dashboard financeiro: períodos rápidos, comparação com o período anterior,
#   gráficos interativos, ranking de clientes, pendências e exportação CSV
# - Datas lidas de forma robusta (texto dd/mm/aaaa, ISO ou coluna do tipo date)
# - Link do WhatsApp sem "55" duplicado
# =============================================================================

import base64
import json
import os
import re
import urllib.parse
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st
from fpdf import FPDF


# =========================
# CONFIGURAÇÃO
# =========================
APP_NOME = "P&S REFRIGERAÇÃO"
APP_SUBTITULO = "Gestão de orçamentos e serviços"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

STATUS_OPCOES = ["Pendente", "Em Andamento", "Concluído", "Cancelado"]
STATUS_ABERTOS = ["Pendente", "Em Andamento"]
CORES_STATUS = {
    "Concluído": "#1E9E62",
    "Em Andamento": "#2F6FD6",
    "Pendente": "#E3A008",
    "Cancelado": "#9AA4B2",
}
COR_OUTROS = "#6B7785"

ABA_NOVO = "📝 Novo Serviço"
ABA_HIST = "📂 Histórico"
ABA_FIN = "📊 Financeiro"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

PERIODOS = ["Este mês", "Mês passado", "Últimos 90 dias", "Este ano", "Ano passado", "Tudo", "Personalizado"]

COLUNAS_BASE = ["ID", "Data", "Data_dt", "Cliente", "WhatsApp", "Status", "Total", "Itens", "ItensJSON"]

CSS = """
<style>
.block-container { padding-top: 1.6rem; max-width: 1240px; }
.app-header {
    display: flex; align-items: center; gap: 16px;
    padding: 18px 22px; margin-bottom: 14px; border-radius: 14px;
    background: linear-gradient(135deg, #0B4F8A 0%, #1679C9 100%);
    box-shadow: 0 6px 18px rgba(11, 79, 138, .18);
}
.app-header img { height: 52px; border-radius: 10px; background: #fff; padding: 4px; }
.app-header .titulo { color: #fff; font-size: 1.55rem; font-weight: 700; line-height: 1.2; }
.app-header .sub { color: rgba(255,255,255,.85); font-size: .92rem; }
.badge {
    display: inline-block; padding: 2px 11px; border-radius: 999px;
    font-size: .78rem; font-weight: 600; color: #fff; vertical-align: middle;
}
.rotulo { color: #6B7785; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
.valor-grande { font-size: 1.9rem; font-weight: 700; color: #1E9E62; line-height: 1.2; }
.linha-info { margin: 2px 0 8px 0; font-size: .98rem; }
div[data-testid="stMetric"] { background: #FFFFFF; }
div[data-testid="stMetricValue"] { font-size: 1.55rem; }
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


def pdf_safe(txt) -> str:
    """Evita UnicodeEncodeError no FPDF (latin-1)."""
    if txt is None:
        return ""
    s = str(txt)
    s = s.replace("•", "-").replace("–", "-").replace("—", "-")
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace(" ", " ")
    return s.encode("latin-1", "ignore").decode("latin-1")


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
    return f'<span class="badge" style="background:{cor}">{status}</span>'


def whatsapp_url(numero, mensagem: str):
    """Monta o link wa.me sem duplicar o 55. Retorna None se não houver número."""
    d = apenas_digitos(numero).lstrip("0")
    if len(d) < 8:
        return None
    if not (d.startswith("55") and len(d) in (12, 13)):
        d = "55" + d
    return f"https://wa.me/{d}?text={urllib.parse.quote(mensagem)}"


def mensagem_whatsapp(cliente: str, os_id: str, total: float) -> str:
    return (
        f"*{APP_NOME}*\n\n"
        f"Olá *{cliente}*, segue seu orçamento.\n"
        f"Nº: {formatar_id_pdf(os_id)}\n"
        f"Valor total: {fmt_brl(total)}"
    )


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
            }
        )
    df = pd.DataFrame(registros, columns=COLUNAS_BASE)
    df["Data_dt"] = pd.to_datetime(df["Data_dt"], errors="coerce")
    df["Total"] = pd.to_numeric(df["Total"], errors="coerce").fillna(0.0).astype(float)
    return df


@st.cache_data(ttl=60, show_spinner="Carregando dados...")
def ler_base() -> pd.DataFrame:
    with conexao() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select id, data, cliente, whatsapp, status, total, itens, itensjson
                from public.orcamentos
                order by created_at desc
                """
            )
            rows = cur.fetchall()
    return montar_df(rows)


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


def salvar_orcamento(reg: dict, data_ref: date) -> str:
    """Insere um novo orçamento e devolve o ID gerado."""
    ultimo_erro = None
    for _ in range(3):
        os_id = gerar_novo_id(data_ref.year)
        try:
            with conexao() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into public.orcamentos
                        (id, data, cliente, whatsapp, status, total, itens, itensjson)
                        values (%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            os_id,
                            data_para_banco(data_ref),
                            reg["Cliente"],
                            reg["WhatsApp"],
                            reg["Status"],
                            float(reg["Total"]),
                            reg["Itens"],
                            reg["ItensJSON"],
                        ),
                    )
            _depois_de_gravar()
            return os_id
        except psycopg2.IntegrityError as e:  # ID já usado em outro aparelho: tenta o próximo
            ultimo_erro = e
    raise ultimo_erro


def atualizar_orcamento(os_id: str, reg: dict, data_ref: date):
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.orcamentos
                set data=%s, cliente=%s, whatsapp=%s, status=%s, total=%s, itens=%s, itensjson=%s
                where id=%s
                """,
                (
                    data_para_banco(data_ref),
                    reg["Cliente"],
                    reg["WhatsApp"],
                    reg["Status"],
                    float(reg["Total"]),
                    reg["Itens"],
                    reg["ItensJSON"],
                    os_id,
                ),
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
# PDF (mantido como estava — será reformulado na próxima etapa)
# =========================
def gerar_pdf(os_id, cliente, whatsapp, data, status, df: pd.DataFrame, total: float) -> bytes:
    pdf = FPDF(format="A4")
    pdf.add_page()

    id_pdf = formatar_id_pdf(os_id)

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 7, pdf_safe(APP_NOME), ln=True, align="C")

    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 6, pdf_safe(f"Orçamento Nº {id_pdf}"), ln=True, align="C")
    pdf.ln(6)

    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, pdf_safe(f"Cliente: {cliente}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"WhatsApp: {apenas_digitos(whatsapp)}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"Data: {data}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"Status: {status}"), ln=True)
    pdf.ln(4)

    pdf.set_font("Arial", "B", 11)
    pdf.cell(110, 8, pdf_safe("Item"), 1)
    pdf.cell(20, 8, pdf_safe("Qtd"), 1, align="C")
    pdf.cell(30, 8, pdf_safe("V. Unit."), 1, align="R")
    pdf.cell(30, 8, pdf_safe("Subtotal"), 1, align="R", ln=True)

    pdf.set_font("Arial", "", 11)
    for _, r in df.iterrows():
        pdf.cell(110, 8, pdf_safe(str(r["Item"])), 1)
        pdf.cell(20, 8, str(int(r["Qtd"])), 1, align="C")
        pdf.cell(30, 8, pdf_safe(fmt_brl(float(r["Valor Unit."]))), 1, align="R")
        pdf.cell(30, 8, pdf_safe(fmt_brl(float(r["Subtotal"]))), 1, align="R", ln=True)

    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(160, 10, pdf_safe("TOTAL:"), align="R")
    pdf.cell(30, 10, pdf_safe(fmt_brl(float(total))), ln=True, align="R")

    out = pdf.output(dest="S")
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return out.encode("latin-1")


def pdf_do_registro(r) -> bytes:
    """PDF de um orçamento salvo (com recuperação de itens de orçamentos antigos)."""
    itens = itens_json_para_df(r["ItensJSON"], r["Itens"], r["Total"])
    itens_limpos, _, _, _ = limpar_calcular(itens)
    return gerar_pdf(r["ID"], r["Cliente"], r["WhatsApp"], r["Data"], r["Status"], itens_limpos, float(r["Total"]))


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
def logo_cabecalho_b64(caminho: str) -> str:
    """Versão pequena da logo para o cabeçalho (a original é pesada para enviar a cada clique)."""
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(caminho) as im:
            if im.mode in ("RGBA", "LA", "P"):
                fundo = Image.new("RGB", im.size, "white")
                fundo.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
                im = fundo
            im = im.convert("RGB")
            im.thumbnail((360, 120))
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def render_cabecalho():
    st.markdown(CSS, unsafe_allow_html=True)
    logo = get_logo_path()
    b64 = logo_cabecalho_b64(logo) if logo else ""
    img = f'<img src="data:image/jpeg;base64,{b64}" alt="logo">' if b64 else ""
    st.markdown(
        f"""
        <div class="app-header">
            {img}
            <div>
                <div class="titulo">❄️ {APP_NOME}</div>
                <div class="sub">{APP_SUBTITULO}</div>
            </div>
        </div>
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
        c1.info(f"✏️ Editando o orçamento **Nº {formatar_id_pdf(ss['id_edicao'])}** ({ss['id_edicao']})")
        c2.button("Cancelar edição", on_click=cb_cancelar_edicao, width="stretch")

    with st.form("form_orcamento", border=True):
        st.markdown("##### 👤 Dados do cliente")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Cliente", key="f_cliente", placeholder="Nome do cliente")
            st.text_input("WhatsApp", key="f_whats", placeholder="(11) 91234-5678")
        with col2:
            st.date_input("Data", key="f_data", format="DD/MM/YYYY")
            st.selectbox("Status", STATUS_OPCOES, key="f_status")

        st.markdown("##### 🧾 Itens do orçamento")
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

        rotulo = "💾 Salvar alterações" if editando else "💾 Salvar orçamento"
        enviado = st.form_submit_button(rotulo, type="primary", width="stretch")

    if enviado:
        processar_salvar(tabela, editando)

    # Ações do último orçamento salvo
    d = ss.get("ultimo_orcamento")
    if d:
        with st.container(border=True):
            st.markdown(
                f"**Orçamento Nº {formatar_id_pdf(d['id'])}** — {d['cliente']} · "
                f"<span style='color:#1E9E62;font-weight:700'>{fmt_brl(d['total'])}</span>",
                unsafe_allow_html=True,
            )
            c_pdf, c_whats, c_fechar = st.columns(3)
            with c_pdf:
                st.download_button(
                    "📄 Baixar PDF",
                    gerar_pdf(d["id"], d["cliente"], d["whatsapp"], d["data"], d["status"], d["tabela"], d["total"]),
                    file_name=nome_arquivo_pdf(d["id"], d["cliente"]),
                    mime="application/pdf",
                    width="stretch",
                    key="ult_pdf",
                )
            with c_whats:
                url = whatsapp_url(d["whatsapp"], mensagem_whatsapp(d["cliente"], d["id"], d["total"]))
                if url:
                    st.link_button("🟢 Enviar WhatsApp", url, width="stretch")
                else:
                    st.button("🟢 Sem WhatsApp", disabled=True, width="stretch", key="ult_sem_whats")
            with c_fechar:
                st.button("➕ Novo orçamento", on_click=cb_fechar_ultimo, width="stretch", key="ult_fechar")


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
    }

    try:
        if editando:
            os_id = ss["id_edicao"]
            atualizar_orcamento(os_id, reg, data_ref)
        else:
            os_id = salvar_orcamento(reg, data_ref)
    except Exception as e:
        st.error(f"Não foi possível salvar no banco. Tente novamente. Detalhe: {e}")
        return

    ss["ultimo_orcamento"] = {
        "id": os_id,
        "cliente": cliente,
        "whatsapp": reg["WhatsApp"],
        "data": fmt_data(data_ref),
        "status": reg["Status"],
        "tabela": tabela_limpa.copy(),
        "total": total,
    }
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
    if c2.button("🗑️ Excluir", type="primary", width="stretch", key="dlg_excluir"):
        try:
            excluir_orcamento(os_id)
        except Exception as e:
            st.error(f"Não foi possível excluir: {e}")
            return
        st.session_state["_limpar_selecao"] = True
        st.session_state["_flash"] = f"Orçamento Nº {formatar_id_pdf(os_id)} excluído."
        st.rerun()


def render_historico():
    df = ler_base()
    if df.empty:
        st.info("Ainda não há orçamentos salvos.")
        return

    c_busca, c_status = st.columns([2, 1])
    busca = c_busca.text_input("🔎 Buscar", key="h_busca", placeholder="Cliente, nº ou item")
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
        placeholder=f"{len(opcoes)} orçamento(s) — escolha um para ver detalhes",
    )

    if sel:
        render_detalhe(df[df["ID"] == sel].iloc[0])

    st.markdown("##### Todos os orçamentos")
    tabela = dff[["ID", "Data", "Cliente", "WhatsApp", "Status", "Total", "Itens"]].copy()
    tabela.insert(0, "Nº", tabela["ID"].map(formatar_id_pdf))
    st.dataframe(
        tabela,
        width="stretch",
        hide_index=True,
        column_config={
            "Nº": st.column_config.TextColumn("Nº", width="small"),
            "ID": st.column_config.TextColumn("ID", width="small"),
            "Total": st.column_config.NumberColumn("Total", format="R$ %.2f"),
            "Itens": st.column_config.TextColumn("Itens", width="large"),
        },
    )
    st.caption(f"{len(dff)} orçamento(s) · Total listado: {fmt_brl(dff['Total'].sum())}")


def render_detalhe(r):
    with st.container(border=True):
        c_info, c_valor = st.columns([3, 1])
        with c_info:
            st.markdown(
                f"""
                <div class="rotulo">Orçamento</div>
                <div class="linha-info"><b>Nº {formatar_id_pdf(r['ID'])}</b> &nbsp;{badge_status(r['Status'])}</div>
                <div class="rotulo">Cliente</div>
                <div class="linha-info">{r['Cliente']}</div>
                <div class="rotulo">WhatsApp · Data</div>
                <div class="linha-info">{r['WhatsApp'] or '—'} · {r['Data'] or '—'}</div>
                """,
                unsafe_allow_html=True,
            )
        with c_valor:
            st.markdown(
                f'<div class="rotulo">Valor total</div><div class="valor-grande">{fmt_brl(r["Total"])}</div>',
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
        with c_pdf:
            st.download_button(
                "📄 PDF",
                pdf_do_registro(r),
                file_name=nome_arquivo_pdf(os_id, r["Cliente"]),
                mime="application/pdf",
                width="stretch",
                key=f"h_pdf_{os_id}",
            )
        with c_whats:
            url = whatsapp_url(r["WhatsApp"], mensagem_whatsapp(r["Cliente"], os_id, r["Total"]))
            if url:
                st.link_button("🟢 WhatsApp", url, width="stretch")
            else:
                st.button("🟢 WhatsApp", disabled=True, width="stretch", key=f"h_wpp_{os_id}",
                          help="Este orçamento não tem número de WhatsApp.")
        with c_edit:
            st.button("✏️ Editar", on_click=cb_editar, args=(os_id,), width="stretch", key=f"h_edit_{os_id}")
        with c_status:
            with st.popover("🔄 Status", width="stretch"):
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
            if st.button("🗑️ Excluir", width="stretch", key=f"h_del_{os_id}"):
                dialog_excluir(os_id, r["Cliente"], float(r["Total"]))


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
    aprovados = len(df[df["Status"].isin(["Concluído", "Em Andamento"])])
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
    dominio = [s for s in STATUS_OPCOES if s in status_presentes] + sorted(
        s for s in status_presentes if s not in STATUS_OPCOES
    )
    return alt.Scale(domain=dominio, range=[CORES_STATUS.get(s, COR_OUTROS) for s in dominio])


def grafico_mensal(df: pd.DataFrame):
    base = df.dropna(subset=["Data_dt"]).copy()
    base["MesData"] = base["Data_dt"].dt.to_period("M").dt.to_timestamp()
    m = base.groupby(["MesData", "Status"], as_index=False).agg(Total=("Total", "sum"), Qtd=("ID", "count"))
    m["Mês"] = m["MesData"].map(lambda d: f"{MESES[d.month - 1]}/{str(d.year)[-2:]}")
    m["Valor"] = m["Total"].map(fmt_brl)
    ordem_meses = [f"{MESES[d.month - 1]}/{str(d.year)[-2:]}" for d in sorted(m["MesData"].unique())]
    m["ordem_status"] = m["Status"].map(lambda s: STATUS_OPCOES.index(s) if s in STATUS_OPCOES else 99)

    return (
        alt.Chart(m)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
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
    return (
        alt.Chart(s)
        .mark_arc(innerRadius=62, outerRadius=110, cornerRadius=3, padAngle=0.015)
        .encode(
            theta=alt.Theta("Total:Q", stack=True),
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
    rotulos = base.mark_text(align="left", dx=5, fontSize=12, color="#41505F").encode(text="Valor:N")
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
    k1.metric("💰 Faturado", fmt_brl(r["faturado"]), delta_pct(r["faturado"], ra["faturado"] if ra else None),
              border=True, help="Soma dos orçamentos com status Concluído no período.")
    k2.metric("⏳ Em aberto", fmt_brl(r["aberto"]), f"{r['qtd_abertos']} orçamento(s)", delta_color="off",
              border=True, help="Pendente + Em Andamento: valor que ainda pode entrar.")
    k3.metric("📄 Orçamentos", r["qtd"], (r["qtd"] - ra["qtd"]) if ra else None,
              border=True, help="Quantidade de orçamentos emitidos no período (todos os status).")
    k4.metric("🎯 Ticket médio", fmt_brl(r["ticket"]), delta_pct(r["ticket"], ra["ticket"] if ra else None),
              border=True, help="Valor médio dos orçamentos concluídos.")
    k5.metric("✅ Aprovação", fmt_pct(r["aprovacao"]),
              (f"{'+' if r['aprovacao'] - ra['aprovacao'] >= 0 else ''}{(r['aprovacao'] - ra['aprovacao']) * 100:.1f} p.p.".replace(".", ",")
               if ra and ra["qtd"] else None),
              border=True, help="(Concluído + Em Andamento) ÷ orçamentos não cancelados.")

    if atual.empty:
        st.info("Nenhum orçamento neste período.")
        return

    # ---- Gráficos
    g1, g2 = st.columns([2, 1])
    with g1:
        with st.container(border=True):
            st.markdown("##### 📈 Evolução mensal por status")
            st.altair_chart(grafico_mensal(atual), width="stretch")
    with g2:
        with st.container(border=True):
            st.markdown("##### 🧩 Distribuição por status")
            st.altair_chart(grafico_status(atual), width="stretch")

    g3, g4 = st.columns(2)
    with g3:
        with st.container(border=True):
            st.markdown("##### 🏆 Top clientes (faturado)")
            ch = grafico_clientes(atual)
            if ch is None:
                st.caption("Nenhum orçamento concluído no período.")
            else:
                st.altair_chart(ch, width="stretch")
    with g4:
        with st.container(border=True):
            st.markdown("##### 📌 Pendências para acompanhar")
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
    with st.expander(f"📋 Orçamentos do período ({len(atual)})"):
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
            "⬇️ Exportar para Excel (CSV)",
            csv,
            file_name=f"orcamentos_{ini:%Y%m%d}_{fim:%Y%m%d}.csv",
            mime="text/csv",
            key="fin_csv",
        )


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
    st.set_page_config(page_title="PS REFRIGERAÇÃO - Gestão", page_icon="❄️", layout="wide")
    init_state()
    render_cabecalho()

    tab_novo, tab_hist, tab_fin = criar_abas()
    with tab_novo:
        render_novo()
    with tab_hist:
        render_historico()
    with tab_fin:
        render_financeiro()


if __name__ == "__main__":
    main()
