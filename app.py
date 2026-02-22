# app.py — P&S REFRIGERAÇÃO (Supabase Postgres) | Orçamentos + PDF com Logo
# =============================================================================
# ✅ Persistência: Supabase (Postgres) via psycopg2
# ✅ ID curto: ANO-XXX (ex: 2026-001)
# ✅ PDF com logo (altura fixa) + "Orçamento Nº 003/26" (sem "OS")
# ✅ Itens em linhas com última linha em branco
# ✅ Edição mantém itens via ItensJSON
# ✅ Aba Histórico: PDF / Editar / Excluir
# =============================================================================

import os
import re
import json
import time
import uuid
import urllib.parse
from datetime import datetime

import streamlit as st
import pandas as pd
from fpdf import FPDF

import psycopg2
import psycopg2.extras


# =========================
# CONFIG / CAMINHOS
# =========================
st.set_page_config(page_title="PS REFRIGERAÇÃO - Gestão", layout="wide")
APP_TITLE = "❄️ P&S REFRIGERAÇÃO"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

LOGO_PNG = os.path.join(ASSETS_DIR, "logo.png")
LOGO_JPG = os.path.join(ASSETS_DIR, "logo.jpg")


# =========================
# CSS (Tab 2 legível no tema escuro)
# =========================
st.markdown(
    """
<style>
.detalhe-card {
    background-color:#ffffff !important;
    padding:20px;
    border-radius:10px;
    border-left:5px solid #0000FF;
    box-shadow:2px 2px 5px rgba(0,0,0,0.1);
}
.detalhe-card, .detalhe-card * { color:#111 !important; }
.valor-card { color:green !important; font-size:22px; font-weight:bold; }
.small-muted { opacity:.7; font-size:12px; }
div[data-baseweb="select"] * { color: #111 !important; }
</style>
""",
    unsafe_allow_html=True,
)


# =========================
# HELPERS
# =========================
def apenas_digitos(s: str) -> str:
    return re.sub(r"\D+", "", str(s or ""))


def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def pdf_safe(txt: str) -> str:
    """Evita UnicodeEncodeError no FPDF (latin-1)."""
    if txt is None:
        return ""
    s = str(txt)
    s = s.replace("\u2022", "-").replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u00a0", " ")
    return s.encode("latin-1", "ignore").decode("latin-1")


def parse_data_ddmmyyyy(s: str):
    try:
        return datetime.strptime(str(s), "%d/%m/%Y").date()
    except Exception:
        return None


def get_logo_path() -> str:
    """Prioriza PNG (recomendado)."""
    if os.path.exists(LOGO_PNG):
        return LOGO_PNG
    if os.path.exists(LOGO_JPG):
        return LOGO_JPG
    return ""


def id_key(id_str: str):
    """Ordenação do ID ANO-XXX."""
    try:
        ano, seq = str(id_str).split("-", 1)
        return (int(ano), int(re.sub(r"\D", "", seq) or "0"))
    except Exception:
        return (-1, -1)


def formatar_id_pdf(os_id: str) -> str:
    """
    Formato limpo para PDF:
    2026-003 -> 003/26
    (robusto mesmo se tiver hífen extra)
    """
    s = str(os_id)
    if "-" in s:
        ano, resto = s.split("-", 1)
        seq = re.sub(r"\D", "", resto) or resto
        return f"{seq}/{ano[-2:]}"
    return s


def itens_json_para_df(itens_json: str) -> pd.DataFrame:
    """Carrega ItensJSON para tabela."""
    try:
        if not itens_json:
            return pd.DataFrame(columns=["Item", "Qtd", "Valor Unit."])
        registros = json.loads(itens_json)
        df = pd.DataFrame(registros)
        for c in ["Item", "Qtd", "Valor Unit."]:
            if c not in df.columns:
                df[c] = "" if c == "Item" else 0
        df["Item"] = df["Item"].astype(str)
        df["Qtd"] = pd.to_numeric(df["Qtd"], errors="coerce").fillna(1).astype(int)
        df["Valor Unit."] = pd.to_numeric(df["Valor Unit."], errors="coerce").fillna(0.0)
        return df
    except Exception:
        return pd.DataFrame(columns=["Item", "Qtd", "Valor Unit."])


def garantir_linha_em_branco(df: pd.DataFrame) -> pd.DataFrame:
    """Garante uma última linha em branco."""
    if df is None or df.empty:
        return pd.DataFrame([{"Item": "", "Qtd": 1, "Valor Unit.": 0.0}])
    if str(df.iloc[-1].get("Item", "")).strip() != "":
        df.loc[len(df)] = {"Item": "", "Qtd": 1, "Valor Unit.": 0.0}
    return df


def limpar_calcular(df: pd.DataFrame):
    """Remove linhas vazias, calcula Subtotal/Total e gera Itens + ItensJSON."""
    df = df.copy()
    for c in ["Item", "Qtd", "Valor Unit."]:
        if c not in df.columns:
            df[c] = "" if c == "Item" else 0

    df["Item"] = df["Item"].astype(str)
    df_limpo = df[df["Item"].str.strip() != ""].copy()

    df_limpo["Qtd"] = pd.to_numeric(df_limpo["Qtd"], errors="coerce").fillna(1).astype(int)
    df_limpo["Valor Unit."] = pd.to_numeric(df_limpo["Valor Unit."], errors="coerce").fillna(0.0)

    df_limpo["Subtotal"] = df_limpo["Qtd"] * df_limpo["Valor Unit."]
    total = float(df_limpo["Subtotal"].sum())

    itens_txt = ", ".join(df_limpo["Item"].str.strip().tolist())
    itens_json = json.dumps(
        df_limpo[["Item", "Qtd", "Valor Unit."]].to_dict(orient="records"),
        ensure_ascii=False,
    )
    return df_limpo, total, itens_txt, itens_json


# =========================
# DB STORAGE (Supabase Postgres)
# =========================
def get_conn():
    db_url = st.secrets.get("SUPABASE_DB_URL")
    if not db_url:
        st.error("Faltou configurar SUPABASE_DB_URL em Settings → Secrets no Streamlit Cloud.")
        st.stop()
    return psycopg2.connect(db_url)


def ler_base() -> pd.DataFrame:
    """Lê tudo do banco e devolve DataFrame no formato do app."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select
                    id       as "ID",
                    data     as "Data",
                    cliente  as "Cliente",
                    whatsapp as "WhatsApp",
                    status   as "Status",
                    coalesce(total, 0)::text as "Total",
                    coalesce(itens, '')      as "Itens",
                    coalesce(itensjson, '')  as "ItensJSON"
                from public.orcamentos
                order by created_at desc;
                """
            )
            rows = cur.fetchall()

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["ID", "Data", "Cliente", "WhatsApp", "Status", "Total", "Itens", "ItensJSON"])
    return df.fillna("")


def salvar_orcamento(novo: dict):
    """Insere (novo) no banco."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.orcamentos
                (id, data, cliente, whatsapp, status, total, itens, itensjson)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    novo["ID"],
                    novo["Data"],
                    novo["Cliente"],
                    novo["WhatsApp"],
                    novo["Status"],
                    float(novo["Total"]),
                    novo["Itens"],
                    novo["ItensJSON"],
                ),
            )
        conn.commit()


def atualizar_orcamento(os_id: str, dados: dict):
    """Atualiza (edição) mantendo o mesmo ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.orcamentos
                set data=%s, cliente=%s, whatsapp=%s, status=%s, total=%s, itens=%s, itensjson=%s
                where id=%s
                """,
                (
                    dados["Data"],
                    dados["Cliente"],
                    dados["WhatsApp"],
                    dados["Status"],
                    float(dados["Total"]),
                    dados["Itens"],
                    dados["ItensJSON"],
                    os_id,
                ),
            )
        conn.commit()


def excluir_orcamento(os_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from public.orcamentos where id=%s", (os_id,))
        conn.commit()


def gerar_novo_id_ano(base: pd.DataFrame, data_ref=None) -> str:
    """Gera ID ANO-XXX baseado no que já existe no banco."""
    if data_ref is None:
        data_ref = datetime.now()
    ano_atual = data_ref.year

    if base is None or base.empty or "ID" not in base.columns:
        return f"{ano_atual}-001"

    ids = base["ID"].astype(str)
    mask_ano = ids.str.startswith(f"{ano_atual}-")

    if not mask_ano.any():
        return f"{ano_atual}-001"

    seqs = (
        ids[mask_ano]
        .str.split("-", n=1)
        .str[1]
        .apply(lambda x: int(re.sub(r"\D", "", str(x)) or "0"))
    )

    novo_seq = int(seqs.max()) + 1 if len(seqs) else 1
    return f"{ano_atual}-{novo_seq:03d}"


# =========================
# PDF (Logo + "Orçamento Nº 003/26")
# =========================
def gerar_pdf(os_id: str, cliente: str, whatsapp: str, data: str, status: str, df: pd.DataFrame, total: float) -> bytes:
    pdf = FPDF(format="A4")
    pdf.add_page()

    logo_path = get_logo_path()

    # Logo no topo com altura fixa (não tampa nada)
    if logo_path and os.path.exists(logo_path):
        header_h = 18  # ajuste fino: 16..24
        try:
            pdf.image(logo_path, x=10, y=6, h=header_h)
            pdf.set_y(6 + header_h + 6)
        except Exception:
            pdf.set_y(20)
    else:
        pdf.set_y(20)

    # Cabeçalho limpo (sem OS)
    id_pdf = formatar_id_pdf(os_id)

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 7, pdf_safe("P&S REFRIGERAÇÃO"), ln=True, align="C")

    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 6, pdf_safe(f"Orçamento Nº {id_pdf}"), ln=True, align="C")
    pdf.ln(6)

    # Dados
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, pdf_safe(f"Cliente: {cliente}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"WhatsApp: {apenas_digitos(whatsapp)}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"Data: {data}"), ln=True)
    pdf.cell(0, 7, pdf_safe(f"Status: {status}"), ln=True)
    pdf.ln(4)

    # Tabela
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

    # Total
    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(160, 10, pdf_safe("TOTAL:"), align="R")
    pdf.cell(30, 10, pdf_safe(fmt_brl(float(total))), ln=True, align="R")

    out = pdf.output(dest="S")
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return out.encode("latin-1")


# =========================
# SESSION STATE
# =========================
defaults = {
    "form_cliente": "",
    "form_whats": "",
    "form_data": datetime.now().date(),
    "form_status": "Pendente",
    "id_edicao": None,           # guarda o ID (ex: 2026-003)
    "chave_tabela": str(uuid.uuid4()),
    "ultimo_orcamento": None,
    "form_itens_json": "",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def reset_form():
    st.session_state["id_edicao"] = None
    st.session_state["form_cliente"] = ""
    st.session_state["form_whats"] = ""
    st.session_state["form_data"] = datetime.now().date()
    st.session_state["form_status"] = "Pendente"
    st.session_state["form_itens_json"] = ""
    st.session_state["chave_tabela"] = str(uuid.uuid4())


# =========================
# INTERFACE
# =========================
st.title(APP_TITLE)
tab1, tab2, tab3 = st.tabs(["📝 Novo Serviço", "📂 Histórico", "📊 Financeiro"])


# -------------------------
# TAB 1 — NOVO / EDIÇÃO
# -------------------------
with tab1:
    editando = st.session_state.get("id_edicao") is not None

    if editando:
        st.info(f"✏️ Editando orçamento: {st.session_state['id_edicao']}")
        if st.button("Cancelar edição"):
            reset_form()
            st.rerun()

    with st.form("form_orcamento"):
        col1, col2 = st.columns(2)

        with col1:
            cliente = st.text_input("Cliente", st.session_state.get("form_cliente", ""))
            whatsapp = st.text_input("WhatsApp", st.session_state.get("form_whats", ""))

        with col2:
            data = st.date_input("Data", st.session_state.get("form_data", datetime.now().date()))
            status_opcoes = ["Pendente", "Em Andamento", "Concluído", "Cancelado"]
            status_atual = st.session_state.get("form_status", "Pendente")
            status = st.selectbox(
                "Status",
                status_opcoes,
                index=status_opcoes.index(status_atual) if status_atual in status_opcoes else 0
            )

        # tabela inicial (mantém valores antigos na edição)
        if editando and st.session_state.get("form_itens_json"):
            df_init = itens_json_para_df(st.session_state["form_itens_json"])
        else:
            df_init = pd.DataFrame([{"Item": "", "Qtd": 1, "Valor Unit.": 0.0}])

        df_init = garantir_linha_em_branco(df_init)

        st.caption("A última linha fica em branco para você adicionar um novo item.")
        tabela = st.data_editor(
            df_init,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key=st.session_state.get("chave_tabela", "tabela_default"),
            column_config={
                "Item": st.column_config.TextColumn("Item", width="large"),
                "Qtd": st.column_config.NumberColumn("Qtd", min_value=1, step=1),
                "Valor Unit.": st.column_config.NumberColumn("Valor Unit.", min_value=0.0, step=0.5, format="R$ %.2f"),
            },
        )

        submit = st.form_submit_button("Salvar")

    if submit:
        if not str(cliente).strip():
            st.error("Informe o nome do cliente.")
            st.stop()

        tabela_limpa, total, itens_txt, itens_json = limpar_calcular(tabela)
        whatsapp_norm = apenas_digitos(whatsapp)

        base = ler_base()

        # EDITAR (mantém ID)
        if editando:
            os_id = st.session_state["id_edicao"]
            atualizar_orcamento(os_id, {
                "Data": data.strftime("%d/%m/%Y"),
                "Cliente": str(cliente).strip(),
                "WhatsApp": whatsapp_norm,
                "Status": status,
                "Total": str(total),
                "Itens": itens_txt,
                "ItensJSON": itens_json,
            })

        # NOVO (gera ID ANO-XXX)
        else:
            os_id = gerar_novo_id_ano(base, datetime.combine(data, datetime.min.time()))
            salvar_orcamento({
                "ID": os_id,
                "Data": data.strftime("%d/%m/%Y"),
                "Cliente": str(cliente).strip(),
                "WhatsApp": whatsapp_norm,
                "Status": status,
                "Total": str(total),
                "Itens": itens_txt,
                "ItensJSON": itens_json,
            })

        st.session_state["ultimo_orcamento"] = {
            "id": os_id,
            "cliente": str(cliente).strip(),
            "whatsapp": whatsapp_norm,
            "data": data.strftime("%d/%m/%Y"),
            "status": status,
            "tabela": tabela_limpa.copy(),
            "total": total,
        }

        reset_form()
        st.success(f"Salvo com sucesso! ID: {os_id}")
        time.sleep(0.2)
        st.rerun()

    # PDF + Whats do último orçamento
    if st.session_state.get("ultimo_orcamento"):
        d = st.session_state["ultimo_orcamento"]

        st.divider()
        col_pdf, col_whats = st.columns(2)

        with col_pdf:
            pdf_bytes = gerar_pdf(
                d["id"],
                d["cliente"], d["whatsapp"],
                d["data"], d["status"],
                d["tabela"], d["total"]
            )
            st.download_button(
                "📄 Baixar PDF",
                pdf_bytes,
                file_name=f"ORC_{d['id']}_{d['cliente']}.pdf",
                use_container_width=True,
            )

        with col_whats:
            msg = (
                f"*P&S REFRIGERAÇÃO*\n\n"
                f"Olá *{d['cliente']}*, segue seu orçamento.\n"
                f"Nº: {formatar_id_pdf(d['id'])}\n"
                f"Valor total: {fmt_brl(d['total'])}"
            )
            st.link_button(
                "🟢 Enviar WhatsApp",
                f"https://wa.me/55{d['whatsapp']}?text={urllib.parse.quote(msg)}",
                use_container_width=True,
            )


# -------------------------
# TAB 2 — HISTÓRICO (PDF + Editar + Excluir)
# -------------------------
with tab2:
    df = ler_base()

    if df.empty:
        st.info("Ainda não há orçamentos salvos.")
    else:
        df_show = df.copy()
        df_show["Total_num"] = pd.to_numeric(df_show["Total"], errors="coerce").fillna(0.0)

        ids = df_show["ID"].astype(str).tolist()
        ids_ordenados = sorted(ids, key=id_key, reverse=True)

        def formatar(os_id: str):
            r = df_show[df_show["ID"].astype(str) == str(os_id)].iloc[0]
            return (
                f"{r.get('ID','')} | {r.get('Data','')} | {r.get('Cliente','')} | "
                f"{r.get('Status','')} ({fmt_brl(float(r.get('Total_num', 0.0)))})"
            )

        selecionado_id = st.selectbox(
            "Selecione um orçamento",
            ids_ordenados,
            format_func=formatar,
            index=None
        )

        if selecionado_id:
            dados = df_show[df_show["ID"].astype(str) == str(selecionado_id)].iloc[0]
            total = float(dados.get("Total_num", 0.0))

            st.markdown(
                f"""
                <div class="detalhe-card" style="background:#fff">
                    <b>ID:</b> {dados.get('ID','')}<br>
                    <b>Nº:</b> {formatar_id_pdf(dados.get('ID',''))}<br>
                    <b>Cliente:</b> {dados.get('Cliente','')}<br>
                    <b>WhatsApp:</b> {dados.get('WhatsApp','')}<br>
                    <b>Data:</b> {dados.get('Data','')}<br>
                    <b>Status:</b> {dados.get('Status','')}<br>
                    <b>Itens:</b> {dados.get('Itens','')}<br>
                    <span class="valor-card">{fmt_brl(total)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col_pdf, col_edit, col_del = st.columns(3)

            with col_pdf:
                itens_df = itens_json_para_df(str(dados.get("ItensJSON", "") or ""))
                itens_limpos, total_calc, _, _ = limpar_calcular(itens_df)

                total_pdf = pd.to_numeric(dados.get("Total", ""), errors="coerce")
                total_pdf = float(total_pdf) if pd.notna(total_pdf) else float(total_calc)

                pdf_bytes = gerar_pdf(
                    str(dados.get("ID", "")),
                    str(dados.get("Cliente", "")),
                    str(dados.get("WhatsApp", "")),
                    str(dados.get("Data", "")),
                    str(dados.get("Status", "")),
                    itens_limpos,
                    total_pdf,
                )

                st.download_button(
                    "📄 Baixar PDF",
                    pdf_bytes,
                    file_name=f"ORC_{dados.get('ID','')}_{dados.get('Cliente','')}.pdf",
                    use_container_width=True,
                    key=f"btn_pdf_{selecionado_id}"
                )

            with col_edit:
                if st.button("✏️ Editar"):
                    st.session_state["id_edicao"] = str(dados.get("ID", ""))
                    st.session_state["form_cliente"] = str(dados.get("Cliente", "") or "")
                    st.session_state["form_whats"] = str(dados.get("WhatsApp", "") or "")
                    st.session_state["form_status"] = str(dados.get("Status", "") or "Pendente")

                    d = parse_data_ddmmyyyy(str(dados.get("Data", "")))
                    st.session_state["form_data"] = d if d else datetime.now().date()

                    st.session_state["form_itens_json"] = str(dados.get("ItensJSON", "") or "")
                    st.session_state["chave_tabela"] = str(uuid.uuid4())
                    st.rerun()

            with col_del:
                confirmar = st.checkbox("Confirmar exclusão", key=f"conf_{selecionado_id}")
                if st.button("🗑️ Excluir", disabled=not confirmar):
                    excluir_orcamento(str(selecionado_id))
                    st.success(f"Orçamento {selecionado_id} excluído.")
                    time.sleep(0.2)
                    st.rerun()

        df_table = df_show.drop(columns=["Total_num"], errors="ignore").copy()
        df_table = df_table.sort_values(by="ID", key=lambda s: s.map(id_key), ascending=False)
        st.dataframe(df_table, use_container_width=True, hide_index=True)


# -------------------------
# TAB 3 — FINANCEIRO
# -------------------------
with tab3:
    df = ler_base()

    if df.empty:
        st.info("Sem dados ainda.")
    else:
        df["Total"] = pd.to_numeric(df["Total"], errors="coerce").fillna(0.0)
        df["Data_dt"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")

        dmin = df["Data_dt"].min().date() if df["Data_dt"].notna().any() else datetime.now().date()
        dmax = df["Data_dt"].max().date() if df["Data_dt"].notna().any() else datetime.now().date()

        col1, col2 = st.columns(2)
        with col1:
            d_ini = st.date_input("Data inicial", dmin)
        with col2:
            d_fim = st.date_input("Data final", dmax)

        df_f = df[(df["Data_dt"] >= pd.to_datetime(d_ini)) & (df["Data_dt"] <= pd.to_datetime(d_fim))]

        fat = float(df_f["Total"].sum())
        qtd = int(len(df_f))
        ticket = fat / qtd if qtd else 0.0

        c1, c2, c3 = st.columns(3)
        c1.metric("💰 Faturamento", fmt_brl(fat))
        c2.metric("📄 Orçamentos", qtd)
        c3.metric("🎯 Ticket Médio", fmt_brl(ticket))

        st.subheader("📌 Por Status")
        st.bar_chart(df_f.groupby("Status")["Total"].sum())

        st.subheader("📈 Evolução Mensal")
        serie = df_f.groupby(df_f["Data_dt"].dt.to_period("M"))["Total"].sum()
        serie.index = serie.index.astype(str)
        st.line_chart(serie)
