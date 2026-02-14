import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from fpdf import FPDF
from datetime import datetime
import urllib.parse
import time
import uuid

# =====================================================
# CONFIGURAÇÃO
# =====================================================
st.set_page_config(page_title="PS REFRIGERAÇÃO - Gestão", layout="wide")

# =====================================================
# CSS
# =====================================================
st.markdown("""
<style>
.detalhe-card {
    background-color:#f8f9fa;
    padding:20px;
    border-radius:10px;
    border-left:5px solid #0000FF;
    box-shadow:2px 2px 5px rgba(0,0,0,0.1);
}
.valor-card {
    color:green;
    font-size:22px;
    font-weight:bold;
}
</style>
""", unsafe_allow_html=True)

# =====================================================
# SESSION STATE
# =====================================================
defaults = {
    "form_cliente": "",
    "form_whats": "",
    "form_item": "",
    "form_valor": 0.0,
    "id_edicao": None,
    "chave_tabela": str(uuid.uuid4()),
    "ultimo_orcamento": None
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# =====================================================
# GOOGLE SHEETS
# =====================================================
conn = st.connection("gsheets", type=GSheetsConnection)
url_planilha = st.secrets["connections"]["gsheets"]["spreadsheet"]

# =====================================================
# PDF
# =====================================================
def gerar_pdf(cliente, whatsapp, data, status, df, total):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "P&S REFRIGERAÇÃO", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, f"Cliente: {cliente}", ln=True)
    pdf.cell(0, 8, f"WhatsApp: {whatsapp}", ln=True)
    pdf.cell(0, 8, f"Data: {data}", ln=True)
    pdf.cell(0, 8, f"Status: {status}", ln=True)
    pdf.ln(5)

    pdf.set_font("Arial", "B", 11)
    pdf.cell(110, 8, "Item", 1)
    pdf.cell(30, 8, "Qtd", 1, align="C")
    pdf.cell(50, 8, "Subtotal", 1, align="C", ln=True)

    pdf.set_font("Arial", "", 11)
    for _, r in df.iterrows():
        pdf.cell(110, 8, str(r["Item"]), 1)
        pdf.cell(30, 8, str(r["Qtd"]), 1, align="C")
        pdf.cell(50, 8, f"R$ {r['Subtotal']:.2f}", 1, align="C", ln=True)

    pdf.ln(5)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(140, 10, "TOTAL:", align="R")
    pdf.cell(50, 10, f"R$ {total:.2f}", ln=True, align="C")

    return pdf.output(dest="S").encode("latin-1")

# =====================================================
# INTERFACE
# =====================================================
st.title("❄️ P&S REFRIGERAÇÃO")

tab1, tab2, tab3 = st.tabs([
    "📝 Novo Serviço",
    "📂 Histórico",
    "📊 Financeiro"
])

# =====================================================
# ABA 1 — NOVO / EDIÇÃO
# =====================================================
with tab1:
    editando = st.session_state["id_edicao"] is not None

    if editando:
        st.info("✏️ Editando orçamento")
        if st.button("Cancelar edição"):
            st.session_state["id_edicao"] = None
            st.session_state["chave_tabela"] = str(uuid.uuid4())
            st.rerun()

    with st.form("form_orcamento"):
        col1, col2 = st.columns(2)
        with col1:
            cliente = st.text_input("Cliente", st.session_state["form_cliente"])
            whatsapp = st.text_input("WhatsApp", st.session_state["form_whats"])
        with col2:
            data = st.date_input("Data", datetime.now())
            status = st.selectbox("Status", ["Pendente", "Em Andamento", "Concluído", "Cancelado"])

        df_init = pd.DataFrame([{
            "Item": st.session_state["form_item"],
            "Qtd": 1,
            "Valor Unit.": st.session_state["form_valor"]
        }])

        tabela = st.data_editor(df_init, num_rows="dynamic", key=st.session_state["chave_tabela"])

        submit = st.form_submit_button("Salvar")

    if submit and cliente:
        tabela["Subtotal"] = pd.to_numeric(tabela["Qtd"]) * pd.to_numeric(tabela["Valor Unit."])
        total = tabela["Subtotal"].sum()
        itens = ", ".join(tabela["Item"].dropna().astype(str))

        base = conn.read(spreadsheet=url_planilha, ttl=0)

        if editando:
            idx = st.session_state["id_edicao"]
            base.loc[idx, ["Cliente","WhatsApp","Status","Total","Itens"]] = [
                cliente, whatsapp, status, total, itens
            ]
        else:
            nova = pd.DataFrame([{
                "Data": data.strftime("%d/%m/%Y"),
                "Cliente": cliente,
                "WhatsApp": whatsapp,
                "Status": status,
                "Total": total,
                "Itens": itens
            }])
            base = pd.concat([base, nova], ignore_index=True)

        conn.update(spreadsheet=url_planilha, data=base)

        # 🔥 RESET COMPLETO
        st.session_state["id_edicao"] = None
        st.session_state["form_cliente"] = ""
        st.session_state["form_whats"] = ""
        st.session_state["form_item"] = ""
        st.session_state["form_valor"] = 0.0
        st.session_state["chave_tabela"] = str(uuid.uuid4())

        # 🔐 Guarda último orçamento (novo ou editado)
        st.session_state["ultimo_orcamento"] = {
            "cliente": cliente,
            "whatsapp": whatsapp,
            "data": data.strftime("%d/%m/%Y"),
            "status": status,
            "tabela": tabela.copy(),
            "total": total
        }

        st.success("Salvo com sucesso!")
        time.sleep(0.5)
        st.rerun()

    # =============================
    # PDF + WHATS (sempre disponíveis)
    # =============================
    if st.session_state["ultimo_orcamento"]:
        d = st.session_state["ultimo_orcamento"]

        st.divider()
        col_pdf, col_whats = st.columns(2)

        with col_pdf:
            pdf = gerar_pdf(
                d["cliente"], d["whatsapp"],
                d["data"], d["status"],
                d["tabela"], d["total"]
            )
            st.download_button(
                "📄 Gerar PDF",
                pdf,
                f"OS_{d['cliente']}.pdf",
                use_container_width=True
            )

        with col_whats:
            msg = (
                f"*P&S REFRIGERAÇÃO*\n\n"
                f"Olá *{d['cliente']}*, segue seu orçamento.\n"
                f"Valor total: R$ {d['total']:.2f}"
            )
            st.link_button(
                "🟢 Enviar WhatsApp",
                f"https://wa.me/55{d['whatsapp']}?text={urllib.parse.quote(msg)}",
                use_container_width=True
            )

# =====================================================
# ABA 2 — HISTÓRICO (EDITAR + EXCLUIR)
# =====================================================
with tab2:
    df = conn.read(spreadsheet=url_planilha, ttl=0)

    opcoes = list(df.index)[::-1]

    def formatar(idx):
        r = df.loc[idx]
        return f"{r['Data']} | {r['Cliente']} (R$ {r['Total']})"

    selecionado = st.selectbox(
        "Selecione um orçamento",
        opcoes,
        format_func=formatar,
        index=None
    )

    if selecionado is not None:
        dados = df.loc[selecionado]

        st.markdown(f"""
        <div class="detalhe-card">
            <b>Cliente:</b> {dados['Cliente']}<br>
            <b>Data:</b> {dados['Data']}<br>
            <b>Status:</b> {dados['Status']}<br>
            <b>Itens:</b> {dados['Itens']}<br>
            <span class="valor-card">R$ {dados['Total']}</span>
        </div>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns(2)

        with col1:
            if st.button("✏️ Editar"):
                st.session_state["id_edicao"] = selecionado
                st.session_state["form_cliente"] = dados["Cliente"]
                st.session_state["form_whats"] = dados["WhatsApp"]
                st.session_state["form_item"] = dados["Itens"]
                st.session_state["form_valor"] = dados["Total"]
                st.session_state["chave_tabela"] = str(uuid.uuid4())
                st.rerun()

        with col2:
            confirmar = st.checkbox("Confirmar exclusão", key=f"conf_{selecionado}")
            if st.button("🗑️ Excluir", disabled=not confirmar):
                df = df.drop(index=selecionado).reset_index(drop=True)
                conn.update(spreadsheet=url_planilha, data=df)
                st.success("Orçamento excluído")
                time.sleep(0.5)
                st.rerun()

    st.dataframe(df, use_container_width=True, hide_index=True)

# =====================================================
# ABA 3 — DASHBOARD FINANCEIRO
# =====================================================
with tab3:
    df = conn.read(spreadsheet=url_planilha, ttl=0)
    df["Total"] = pd.to_numeric(df["Total"], errors="coerce").fillna(0)
    df["Data"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")

    col1, col2 = st.columns(2)
    with col1:
        d_ini = st.date_input("Data inicial", df["Data"].min())
    with col2:
        d_fim = st.date_input("Data final", df["Data"].max())

    df_f = df[(df["Data"] >= pd.to_datetime(d_ini)) & (df["Data"] <= pd.to_datetime(d_fim))]

    fat = df_f["Total"].sum()
    qtd = len(df_f)
    ticket = fat / qtd if qtd else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("💰 Faturamento", f"R$ {fat:,.2f}")
    c2.metric("📄 Orçamentos", qtd)
    c3.metric("🎯 Ticket Médio", f"R$ {ticket:,.2f}")

    st.subheader("📌 Por Status")
    st.bar_chart(df_f.groupby("Status")["Total"].sum())

    st.subheader("📈 Evolução Mensal")
    serie = df_f.groupby(df_f["Data"].dt.to_period("M"))["Total"].sum()
    serie.index = serie.index.astype(str)
    st.line_chart(serie)
