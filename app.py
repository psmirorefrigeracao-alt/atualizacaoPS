import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from fpdf import FPDF
from datetime import datetime
import urllib.parse

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="PS REFRIGERAÇÃO - Gestão", layout="wide")

# --- DADOS FIXOS DA EMPRESA (Edite aqui se precisar mudar) ---
MEU_CNPJ = "62.967.478/0001-42" # <--- Seu CNPJ aqui

# --- CONEXÃO COM GOOGLE SHEETS ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    url_planilha = st.secrets["connections"]["gsheets"]["spreadsheet"]
except Exception as e:
    st.error(f"Erro de conexão: Verifique o arquivo secrets.toml. Detalhe: {e}")
    st.stop()

# --- FUNÇÃO PARA GERAR PDF ---
def gerar_pdf_os(cliente, fone, email, data, status, df_itens, total, obs, validade):
    pdf = FPDF()
    pdf.add_page()
    
    # 1. Cabeçalho com Logo e CNPJ FIXO
    try:
        pdf.image('logo.jpg', 10, 8, 33) 
    except:
        pass 
    
    pdf.set_font("Arial", "B", 15)
    pdf.cell(80) 
    pdf.cell(100, 10, "P&S REFRIGERACAO", ln=True, align='L')
    pdf.set_font("Arial", "", 10)
    pdf.cell(80)
    pdf.cell(100, 5, f"CNPJ: 62.967.478.0001-42", ln=True, align='L') # CNPJ Fixo no PDF
    pdf.cell(80)
    pdf.cell(100, 5, "Especialista em Refrigeracao", ln=True, align='L')
    pdf.ln(15)

    # 2. Título e Dados do Orçamento
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "ORCAMENTO / ORDEM DE SERVICO", ln=True, align='C', fill=True)
    pdf.ln(5)
    
    pdf.set_font("Arial", "", 11)
    data_br = data.strftime('%d/%m/%Y')
    pdf.cell(100, 8, f"Cliente: {cliente}")
    pdf.cell(0, 8, f"Data: {data_br}", ln=True)
    pdf.cell(100, 8, f"WhatsApp: {fone}")
    pdf.cell(0, 8, f"Validade: {validade} dias", ln=True)
    pdf.ln(5)
    
    # 3. Tabela de Itens
    pdf.set_font("Arial", "B", 11)
    pdf.cell(110, 8, "Descricao do Item/Servico", border=1, fill=True)
    pdf.cell(30, 8, "Qtd", border=1, fill=True, align='C')
    pdf.cell(50, 8, "Subtotal", border=1, fill=True, align='C', ln=True)
    
    pdf.set_font("Arial", "", 11)
    for _, row in df_itens.iterrows():
        if row['Item']:
            pdf.cell(110, 8, str(row['Item']), border=1)
            pdf.cell(30, 8, str(row['Qtd']), border=1, align='C')
            pdf.cell(50, 8, f"R$ {row['Subtotal']:.2f}", border=1, align='C', ln=True)
        
    pdf.ln(5)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(140, 10, "TOTAL DO INVESTIMENTO:", align='R')
    pdf.cell(50, 10, f"R$ {total:.2f}", ln=True, align='C')
    
    # 4. Termos Jurídicos CDC
    pdf.ln(5)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 5, "CONDICOES GERAIS E GARANTIA:", ln=True)
    pdf.set_font("Arial", "", 9)
    termos = (
        f"1. GARANTIA: Servicos e pecas novas possuem garantia legal de 90 dias (Art. 26, II do CDC).\n"
        f"2. PECAS: Sao utilizadas apenas pecas novas e originais (Art. 21 do CDC).\n"
        f"3. VALIDADE: Orcamento valido por {validade} dias (Art. 40 do CDC).\n"
        f"OBSERVACOES: {obs}"
    )
    pdf.multi_cell(0, 5, termos.encode('latin-1', 'ignore').decode('latin-1'))
    
    # 5. Assinaturas
    pdf.ln(20)
    y_pos = pdf.get_y()
    pdf.line(20, y_pos, 85, y_pos)
    pdf.line(125, y_pos, 190, y_pos)
    pdf.set_font("Arial", "", 9)
    pdf.cell(85, 10, "Assinatura PS REFRIGERACAO", align='C')
    pdf.cell(20)
    pdf.cell(85, 10, "Assinatura do Cliente", align='C')
    
    return pdf.output(dest='S').encode('latin-1', 'ignore')

# --- INTERFACE ---
st.title("❄️ P&S REFRIGERAÇÃO")

aba_nova, aba_hist = st.tabs(["📝 Nova Ordem de Serviço", "📂 Histórico de Serviços"])

with aba_nova:
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            nome_cliente = st.text_input("Nome do Cliente")
            whatsapp_cliente = st.text_input("WhatsApp (Ex: 43999998888)")
        with c2:
            data_orcamento = st.date_input("Data do Orçamento", datetime.now())
            dias_validade = st.number_input("Validade (Dias)", min_value=1, value=7)
            status_os = st.selectbox("Status", ["Pendente", "Em Andamento", "Concluído", "Cancelado"])

    st.subheader("📋 Tabela de Produtos/Serviços")
    df_pecas = pd.DataFrame([{"Item": "", "Qtd": 1, "Valor Unit.": 0.0}])
    tabela_editada = st.data_editor(df_pecas, num_rows="dynamic", use_container_width=True)

    # Cálculos Automáticos
    tabela_editada["Subtotal"] = pd.to_numeric(tabela_editada["Qtd"]) * pd.to_numeric(tabela_editada["Valor Unit."])
    valor_total = tabela_editada["Subtotal"].sum()
    st.markdown(f"### TOTAL: :blue[R$ {valor_total:.2f}]")

    obs_tecnica = st.text_area("Observações Técnicas")

    # Botões de Ação
    btn_salvar, btn_pdf, btn_whats = st.columns(3)

    with btn_salvar:
        if st.button("💾 Salvar na Planilha", type="primary", use_container_width=True):
            if nome_cliente and whatsapp_cliente:
                try:
                    resumo = ", ".join([f"{r['Item']}" for _, r in tabela_editada.iterrows() if r['Item']])
                    nova_os = pd.DataFrame([{
                        "Data": data_orcamento.strftime('%d/%m/%Y'),
                        "Cliente": nome_cliente, "WhatsApp": whatsapp_cliente,
                        "Status": status_os, "Total": valor_total, "Itens": resumo
                    }])
                    df_base = conn.read(spreadsheet=url_planilha)
                    df_atualizado = pd.concat([df_base, nova_os], ignore_index=True)
                    conn.update(spreadsheet=url_planilha, data=df_atualizado)
                    st.success("✅ OS Salva com Sucesso!")
                except Exception as e:
                    st.error(f"Erro ao salvar: {e}")

    with btn_pdf:
        pdf_bytes = gerar_pdf_os(nome_cliente, whatsapp_cliente, "", data_orcamento, status_os, tabela_editada, valor_total, obs_tecnica, dias_validade)
        st.download_button("📄 1º Baixar PDF", pdf_bytes, f"OS_{nome_cliente}.pdf", "application/pdf", use_container_width=True)

    with btn_whats:
        msg_zap = (
            f"*P&S REFRIGERAÇÃO*\n\n"
            f"Olá *{nome_cliente}*, segue o seu orçamento.\n"
            f"💰 *Valor:* R$ {valor_total:.2f}\n"
            f"⏳ *Validade:* {dias_validade} dias\n\n"
            f"Confira os detalhes no PDF enviado."
        )
        st.link_button("🟢 2º Enviar WhatsApp", f"https://wa.me/55{whatsapp_cliente}?text={urllib.parse.quote(msg_zap)}", use_container_width=True)

with aba_hist:
    st.subheader("🔍 Consulta de Histórico")
    try:
        dados_historico = conn.read(spreadsheet=url_planilha)
        
        # Busca por nome
        busca = st.text_input("Filtrar por nome do cliente")
        if busca:
            dados_historico = dados_historico[dados_historico['Cliente'].str.contains(busca, case=False, na=False)]
            
        st.dataframe(dados_historico, use_container_width=True, hide_index=True)
    except Exception as e:

        st.info("O histórico aparecerá aqui após o primeiro salvamento.")
