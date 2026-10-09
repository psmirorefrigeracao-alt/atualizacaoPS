# pdf_orcamento.py — PDF do orçamento no formato profissional (P&S Refrigeração)
# Gera o PDF com reportlab: cabeçalho com logo em todas as páginas, marca d'água,
# seções numeradas, tabela de valores, QR code para aceite online e assinatura.

import io
import re
from datetime import date, datetime, timedelta

from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Flowable, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ---------- Paleta (identidade da logo P&S: noite, gelo e brasa) ----------
NOITE = colors.HexColor("#0A1222")
NOITE_2 = colors.HexColor("#122038")
GRAFITE = NOITE
AZUL = colors.HexColor("#0E7ACB")        # azul "gelo" legível sobre papel branco
AZUL_CLARO = colors.HexColor("#1FA8FF")  # azul da logo (faixas e destaques)
BRASA = colors.HexColor("#F28A12")
AZUL_FUNDO = colors.HexColor("#EAF5FD")
CINZA_TXT = colors.HexColor("#2E3A4B")
CINZA_SUAVE = colors.HexColor("#66748A")
CINZA_LINHA = colors.HexColor("#D6DEE8")
ZEBRA = colors.HexColor("#F4F7FB")
VERDE = colors.HexColor("#1E9E62")
VERMELHO = colors.HexColor("#C93C3C")
GELO_TXT = colors.HexColor("#8FD3FF")

MARGEM_X = 18 * mm
TOPO = 47 * mm
BASE = 20 * mm
LARGURA_UTIL = A4[0] - 2 * MARGEM_X


# ---------- Utilidades ----------
def _t(s) -> str:
    """Texto seguro para as fontes padrão do PDF (sem emojis) e escapado para Paragraph."""
    if s is None:
        return ""
    s = str(s)
    trocas = {"•": "•", "–": "-", "—": "-", "‘": "'", "’": "'",
              "“": '"', "”": '"', " ": " ", "➢": "•", "→": "->"}
    for a, b in trocas.items():
        s = s.replace(a, b)
    s = s.encode("cp1252", "ignore").decode("cp1252")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def brl(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def _qtd(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "1"


_cache_img = {}


def _logo_reader(caminho):
    """Logo em PNG com transparência (o círculo da logo fica sem cantos pretos)."""
    if not caminho:
        return None, (1, 1)
    if ("logo", caminho) not in _cache_img:
        try:
            im = Image.open(caminho).convert("RGBA")
            im.thumbnail((700, 700))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            buf.seek(0)
            _cache_img[("logo", caminho)] = (ImageReader(buf), im.size)
        except Exception:
            _cache_img[("logo", caminho)] = (None, (1, 1))
    return _cache_img[("logo", caminho)]


def _floco(c, cx, cy, raio, cor, alpha, largura):
    """Floco de neve vetorial (marca d'água): 6 braços com ramificações."""
    import math
    c.saveState()
    c.setStrokeColor(cor)
    if alpha < 1:
        c.setStrokeAlpha(alpha)
    c.setLineWidth(largura)
    c.setLineCap(1)
    for i in range(6):
        ang = math.radians(90 + i * 60)
        dx, dy = math.cos(ang), math.sin(ang)
        c.line(cx, cy, cx + dx * raio, cy + dy * raio)
        for frac, tam in ((0.42, 0.30), (0.70, 0.24)):
            px, py = cx + dx * raio * frac, cy + dy * raio * frac
            for lado in (-1, 1):
                a2 = ang + lado * math.radians(45)
                c.line(px, py, px + math.cos(a2) * raio * tam, py + math.sin(a2) * raio * tam)
    c.restoreState()


def _linha_termica(c, x0, x1, y, espessura):
    """Faixa azul → laranja (frio → quente), assinatura visual da marca."""
    c.saveState()
    p = c.beginPath()
    p.rect(x0, y, x1 - x0, espessura)
    c.clipPath(p, stroke=0, fill=0)
    c.linearGradient(x0, y, x1, y, (AZUL_CLARO, colors.HexColor("#39C6FF"), colors.HexColor("#FFB23F"), BRASA),
                     positions=(0, 0.38, 0.72, 1), extend=False)
    c.restoreState()


class _Secao(Flowable):
    """Título de seção numerado com sublinhado azul (como no modelo)."""

    def __init__(self, texto):
        super().__init__()
        self.texto = texto
        self.height = 9 * mm

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setFont("Helvetica-Bold", 12.5)
        c.setFillColor(GRAFITE)
        c.drawString(0, 2.6 * mm, self.texto)
        larg = c.stringWidth(self.texto, "Helvetica-Bold", 12.5)
        c.setStrokeColor(AZUL_CLARO)
        c.setLineWidth(1.4)
        c.line(0, 1.2 * mm, larg, 1.2 * mm)
        c.setFillColor(BRASA)
        c.circle(larg + 2.2 * mm, 1.2 * mm, 0.9 * mm, stroke=0, fill=1)


def _qr(link, tamanho=30 * mm):
    w = qr.QrCodeWidget(link)
    b = w.getBounds()
    d = Drawing(tamanho, tamanho, transform=[tamanho / (b[2] - b[0]), 0, 0, tamanho / (b[3] - b[1]), 0, 0])
    d.add(w)
    return d


class _QR(Flowable):
    def __init__(self, link, tamanho=30 * mm):
        super().__init__()
        self.d = _qr(link, tamanho)
        self.width = self.height = tamanho

    def draw(self):
        renderPDF.draw(self.d, self.canv, 0, 0)


# ---------- Documento ----------
def gerar_pdf_orcamento(orc: dict, itens: list, empresa: dict, logo_path: str = "", link_aceite: str = "") -> bytes:
    """
    orc: id, numero, cliente, whatsapp, data (dd/mm/aaaa), titulo, descritivo, pagamento,
         total, status, resposta_em, resposta_obs
    itens: lista de dicts {"Item", "Qtd", "Valor Unit."}
    """
    nome_emp = empresa.get("nome_fantasia") or "P&S Refrigeração"
    numero = orc.get("numero") or orc.get("id", "")
    data_txt = orc.get("data") or ""
    try:
        validade = (datetime.strptime(data_txt, "%d/%m/%Y").date()
                    + timedelta(days=int(empresa.get("validade_dias") or 5))).strftime("%d/%m/%Y")
    except Exception:
        validade = ""

    # ----- estilos
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10.2, leading=14.2, textColor=CINZA_TXT)
    st = {
        "base": base,
        "just": ParagraphStyle("just", parent=base, alignment=4),
        "atividade": ParagraphStyle("atv", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                                    textColor=AZUL),
        "contato": ParagraphStyle("ct", parent=base, fontSize=9.6, leading=13.4),
        "titulo": ParagraphStyle("tit", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=18,
                                 textColor=GRAFITE),
        "box_tit": ParagraphStyle("bt", parent=base, fontName="Helvetica-Bold", fontSize=8.6, leading=11,
                                  textColor=AZUL),
        "box": ParagraphStyle("bx", parent=base, fontSize=9.4, leading=13),
        "th": ParagraphStyle("th", parent=base, fontName="Helvetica-Bold", fontSize=9.2, leading=11.5,
                             textColor=colors.white, alignment=TA_CENTER),
        "td": ParagraphStyle("td", parent=base, fontSize=9.6, leading=12.4),
        "td_r": ParagraphStyle("tdr", parent=base, fontSize=9.6, leading=12.4, alignment=TA_RIGHT),
        "td_c": ParagraphStyle("tdc", parent=base, fontSize=9.6, leading=12.4, alignment=TA_CENTER),
        "tot": ParagraphStyle("tot", parent=base, fontName="Helvetica-Bold", fontSize=10.6, textColor=GRAFITE),
        "tot_r": ParagraphStyle("totr", parent=base, fontName="Helvetica-Bold", fontSize=11.5, textColor=AZUL,
                                alignment=TA_RIGHT),
        "sub": ParagraphStyle("sub", parent=base, fontName="Helvetica-Bold", fontSize=10.4, textColor=GRAFITE),
        "peq": ParagraphStyle("peq", parent=base, fontSize=8.6, leading=11.5, textColor=CINZA_SUAVE),
    }

    story = []

    # ----- Identificação da empresa (página 1)
    if empresa.get("atividade"):
        story.append(Paragraph(_t(empresa['atividade']), st["atividade"]))
        story.append(Spacer(0, 2.5 * mm))
    linhas = []
    if empresa.get("endereco"):
        linhas.append(f"<b>Endereço:</b> {_t(empresa['endereco'])}")
    if empresa.get("cep_cidade"):
        linhas.append(f"<b>{_t(empresa['cep_cidade'])}</b>")
    l3 = [f"<b>CNPJ:</b> {_t(empresa['cnpj'])}" if empresa.get("cnpj") else "",
          f"<b>Telefone:</b> {_t(empresa['telefone'])}" if empresa.get("telefone") else ""]
    l4 = [f"<b>E-mail:</b> <font color='#2479B4'>{_t(empresa['email'])}</font>" if empresa.get("email") else "",
          f"<b>Site:</b> <font color='#2479B4'>{_t(empresa['site'])}</font>" if empresa.get("site") else ""]
    for grupo in (l3, l4):
        g = [x for x in grupo if x]
        if g:
            linhas.append("  |  ".join(g))
    for ln in linhas:
        story.append(Paragraph(ln, st["contato"]))
    story.append(Spacer(0, 4 * mm))

    # ----- Título + número
    titulo = (orc.get("titulo") or "Orçamento de serviços").strip()
    story.append(Paragraph(_t(titulo).upper(), st["titulo"]))
    story.append(Spacer(0, 4 * mm))

    # ----- Caixas: empresa executora | cliente
    emp_ln = [f"<b>Nome fantasia:</b> {_t(nome_emp)}"]
    if empresa.get("razao_social"):
        emp_ln.insert(0, f"<b>Empresa:</b> {_t(empresa['razao_social'])}")
    if empresa.get("cnpj"):
        emp_ln.append(f"<b>CNPJ:</b> {_t(empresa['cnpj'])}")
    if empresa.get("tecnico"):
        emp_ln.append(f"<b>Técnico responsável:</b> {_t(empresa['tecnico'])}")
    if empresa.get("cpf_tecnico"):
        emp_ln.append(f"<b>CPF:</b> {_t(empresa['cpf_tecnico'])}")

    tel = re.sub(r"\D", "", str(orc.get("whatsapp") or ""))
    cli_ln = [f"<b>Nome:</b> {_t(orc.get('cliente'))}"]
    if tel:
        cli_ln.append(f"<b>Telefone:</b> {_t(tel)}")
    cli_ln.append(f"<b>Data:</b> {_t(data_txt)}")
    if validade:
        cli_ln.append(f"<b>Válido até:</b> {validade}")

    def caixa(titulo_box, linhas_box):
        return [Paragraph(titulo_box, st["box_tit"])] + [Paragraph(x, st["box"]) for x in linhas_box]

    caixas = Table(
        [[caixa("EMPRESA EXECUTORA", emp_ln), caixa("DADOS DO CLIENTE", cli_ln)]],
        colWidths=[LARGURA_UTIL / 2 - 2 * mm, LARGURA_UTIL / 2 - 2 * mm],
        hAlign="LEFT",
    )
    caixas.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
        ("LINEBEFORE", (0, 0), (0, 0), 2.2, AZUL),
        ("LINEBEFORE", (1, 0), (1, 0), 2.2, AZUL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("COLPADDING", (0, 0), (-1, -1), 4),
    ]))
    # espaço entre as caixas
    caixas = Table([[caixas]], colWidths=[LARGURA_UTIL])
    caixas.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(caixas)
    story.append(Spacer(0, 6 * mm))

    n = 0

    def secao(texto):
        nonlocal n
        n += 1
        return _Secao(f"{n}. {texto.upper()}")

    # ----- 1. Objetivo
    objetivo = (empresa.get("objetivo") or "").replace("{nome}", nome_emp)
    if objetivo:
        story.append(KeepTogether([secao("Objetivo"), Spacer(0, 1.5 * mm),
                                   Paragraph(_t(objetivo).replace(_t(nome_emp), f"<b>{_t(nome_emp).upper()}</b>", 1),
                                             st["just"])]))
        story.append(Spacer(0, 5 * mm))

    # ----- 2. Valores
    cab = [Paragraph("ITEM", st["th"]), Paragraph("QTD.", st["th"]),
           Paragraph("VALOR UNITÁRIO", st["th"]), Paragraph("SUBTOTAL", st["th"])]
    linhas_tab = [cab]
    total_calc = 0.0
    for it in itens:
        nome_it = str(it.get("Item") or "").strip()
        if not nome_it:
            continue
        q = it.get("Qtd") or 1
        vu = it.get("Valor Unit.") or 0
        try:
            sub = float(q) * float(vu)
        except (TypeError, ValueError):
            sub = 0.0
        total_calc += sub
        linhas_tab.append([Paragraph(_t(nome_it), st["td"]), Paragraph(_qtd(q), st["td_c"]),
                           Paragraph(brl(vu), st["td_r"]), Paragraph(brl(sub), st["td_r"])])
    total = orc.get("total")
    total = float(total) if total not in (None, "") else total_calc
    linhas_tab.append([Paragraph("TOTAL", st["tot"]), "", "", Paragraph(brl(total), st["tot_r"])])

    larguras = [LARGURA_UTIL - 15 * mm - 31 * mm - 33 * mm, 15 * mm, 31 * mm, 33 * mm]
    tab = Table(linhas_tab, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), GRAFITE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, CINZA_LINHA),
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_FUNDO),
        ("LINEABOVE", (0, -1), (-1, -1), 1.4, BRASA),
        ("SPAN", (0, -1), (2, -1)),
    ]
    for i in range(1, len(linhas_tab) - 1):
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    tab.setStyle(TableStyle(estilo))
    story.append(KeepTogether([secao("Valores"), Spacer(0, 2.5 * mm)]))
    story.append(tab)
    story.append(Spacer(0, 5 * mm))

    # ----- Descritivo (opcional)
    descr = (orc.get("descritivo") or "").strip()
    if descr:
        bloco = [Paragraph("DESCRITIVO DOS SERVIÇOS E EQUIPAMENTOS", st["sub"]), Spacer(0, 2 * mm)]
        bullets, paragrafos = [], []

        def fecha_bullets():
            if bullets:
                paragrafos.append(ListFlowable(
                    [ListItem(Paragraph(_t(b), st["base"]), leftIndent=12, value="•") for b in bullets],
                    bulletType="bullet", start="•", leftIndent=12, bulletColor=AZUL,
                ))
                bullets.clear()

        for linha in descr.splitlines():
            s = linha.strip()
            if not s:
                fecha_bullets()
                continue
            if re.match(r"^[-•*➢]\s*", s):
                bullets.append(re.sub(r"^[-•*➢]\s*", "", s))
            else:
                fecha_bullets()
                paragrafos.append(Paragraph(_t(s), st["just"]))
        fecha_bullets()
        for p in paragrafos:
            bloco.append(p)
            bloco.append(Spacer(0, 1.8 * mm))
        story.extend(bloco)
        story.append(Spacer(0, 1.5 * mm))

    story.append(Paragraph(f"<b>Valor total do orçamento: {brl(total)}.</b>", st["base"]))
    story.append(Spacer(0, 6 * mm))

    # ----- Forma de pagamento
    pag = (orc.get("pagamento") or "").strip() or empresa.get("pagamento_padrao") or ""
    if pag:
        story.append(KeepTogether([secao("Forma de pagamento"), Spacer(0, 1.5 * mm)]
                                  + [Paragraph(_t(p), st["just"]) for p in pag.splitlines() if p.strip()]))
        story.append(Spacer(0, 6 * mm))

    # ----- Observações finais
    obs = list(empresa.get("observacoes") or [])
    dias = int(empresa.get("validade_dias") or 5)
    extenso = {1: "um", 2: "dois", 3: "três", 5: "cinco", 7: "sete", 10: "dez", 15: "quinze", 30: "trinta"}
    obs.append(f"Este orçamento possui validade de {dias}"
               + (f" ({extenso[dias]})" if dias in extenso else "")
               + " dias, contados a partir da data de emissão.")
    lista_obs = ListFlowable(
        [ListItem(Paragraph(_t(o), st["base"]), leftIndent=14, value="•") for o in obs],
        bulletType="bullet", start="•", leftIndent=14, bulletColor=AZUL, bulletFontSize=11,
    )
    story.append(KeepTogether([secao("Observações finais"), Spacer(0, 1.5 * mm), lista_obs]))
    if empresa.get("fechamento"):
        story.append(Spacer(0, 4 * mm))
        story.append(Paragraph(_t(empresa["fechamento"]), st["base"]))
    story.append(Spacer(0, 6 * mm))

    # ----- Aceite online (QR)
    status = str(orc.get("status") or "")
    if status in ("Aprovado", "Recusado") and orc.get("resposta_em"):
        cor = "#1E9E62" if status == "Aprovado" else "#C93C3C"
        txt = f"<font color='{cor}'><b>{'APROVADO' if status == 'Aprovado' else 'RECUSADO'} PELO CLIENTE</b></font>" \
              f" em {_t(orc['resposta_em'])}"
        if orc.get("resposta_obs"):
            txt += f"<br/><font color='#6B7380'>Observação do cliente: {_t(orc['resposta_obs'])}</font>"
        story.append(KeepTogether([secao("Aceite do cliente"), Spacer(0, 1.5 * mm), Paragraph(txt, st["base"])]))
        story.append(Spacer(0, 6 * mm))
    elif link_aceite:
        txt = Paragraph(
            "<b>Aprove ou recuse este orçamento online</b><br/>"
            "Aponte a câmera do celular para o QR code ou acesse o link abaixo. "
            "A resposta chega na hora para a nossa equipe.<br/>"
            f"<font size='8.4' color='#2479B4'>{_t(link_aceite)}</font>",
            st["base"],
        )
        caixa_qr = Table([[_QR(link_aceite, 27 * mm), txt]], colWidths=[33 * mm, LARGURA_UTIL - 33 * mm])
        caixa_qr.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, -1), AZUL_FUNDO),
            ("BOX", (0, 0), (-1, -1), 0.6, AZUL_CLARO),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether([secao("Aceite do orçamento"), Spacer(0, 2 * mm), caixa_qr]))
        story.append(Spacer(0, 6 * mm))

    # ----- Assinatura
    ass_emp = []
    if empresa.get("razao_social"):
        ass_emp.append(f"<b>Empresa executora:</b> {_t(empresa['razao_social'])}")
    ass_emp.append(f"<b>Nome fantasia:</b> {_t(nome_emp)}")
    if empresa.get("cnpj"):
        ass_emp.append(f"<b>CNPJ:</b> {_t(empresa['cnpj'])}")
    if empresa.get("tecnico"):
        ass_emp.append(f"<b>Técnico responsável:</b> {_t(empresa['tecnico'])}")

    linha_ass = "_" * 38
    assin = Table(
        [[Paragraph("<br/>".join(ass_emp), st["base"]), ""],
         [Paragraph(f"<br/><br/>{linha_ass}<br/>{_t(nome_emp)}", st["peq"]),
          Paragraph(f"<br/><br/>{linha_ass}<br/>De acordo: {_t(orc.get('cliente'))}<br/>Data: ____/____/______",
                    st["peq"])]],
        colWidths=[LARGURA_UTIL / 2, LARGURA_UTIL / 2],
    )
    assin.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("SPAN", (0, 0), (1, 0))]))
    story.append(KeepTogether([secao("Assinaturas"), Spacer(0, 2 * mm), assin]))

    # ----- Página (cabeçalho, marca d'água, rodapé)
    logo, (lw, lh) = _logo_reader(logo_path)
    rodape_txt = "   |   ".join(x for x in [nome_emp, f"CNPJ {empresa.get('cnpj')}" if empresa.get("cnpj") else "",
                                           empresa.get("telefone") or ""] if x)

    def desenhar_fundo(c, doc):
        c.saveState()
        larg_pg, alt_pg = A4
        # marca d'água
        _floco(c, larg_pg / 2, alt_pg / 2 - 12 * mm, 62 * mm, colors.HexColor("#EEF6FD"), 1, 9)
        # faixa escura do cabeçalho
        alt_faixa = 33 * mm
        y_faixa = alt_pg - alt_faixa
        c.setFillColor(NOITE)
        c.rect(0, y_faixa, larg_pg, alt_faixa, stroke=0, fill=1)
        _linha_termica(c, 0, larg_pg, y_faixa - 1.4 * mm, 1.4 * mm)
        # logo
        lado = 28 * mm
        x_txt = MARGEM_X
        if logo:
            c.drawImage(logo, MARGEM_X, y_faixa + (alt_faixa - lado) / 2, lado, lado * lh / lw, mask="auto")
            x_txt = MARGEM_X + lado + 5 * mm
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(x_txt, y_faixa + 18 * mm, nome_emp.encode("cp1252", "ignore").decode("cp1252"))
        c.setFillColor(GELO_TXT)
        c.setFont("Helvetica", 8.6)
        c.drawString(x_txt, y_faixa + 12.6 * mm, "S O L U Ç Õ E S   E M   C O N F O R T O".encode("cp1252").decode("cp1252"))
        # número do orçamento
        c.setFillColor(colors.HexColor("#AFC3DA"))
        c.setFont("Helvetica", 8.5)
        c.drawRightString(larg_pg - MARGEM_X, y_faixa + 22 * mm, "Orçamento".encode("cp1252").decode("cp1252"))
        c.setFillColor(BRASA)
        c.setFont("Helvetica-Bold", 19)
        c.drawRightString(larg_pg - MARGEM_X, y_faixa + 14.5 * mm, f"Nº {numero}")
        c.setFillColor(colors.HexColor("#DCE6F2"))
        c.setFont("Helvetica", 9)
        c.drawRightString(larg_pg - MARGEM_X, y_faixa + 9 * mm, f"Emissão: {data_txt}".encode("cp1252", "ignore").decode("cp1252"))
        # rodapé
        c.setStrokeColor(CINZA_LINHA)
        c.setLineWidth(0.5)
        c.line(MARGEM_X, 13 * mm, larg_pg - MARGEM_X, 13 * mm)
        c.setFont("Helvetica", 7.8)
        c.setFillColor(CINZA_SUAVE)
        c.drawString(MARGEM_X, 9 * mm, rodape_txt.encode("cp1252", "ignore").decode("cp1252"))
        c.restoreState()

    class CanvasNumerado(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pg = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                self.setFont("Helvetica", 7.8)
                self.setFillColor(CINZA_SUAVE)
                self.drawRightString(A4[0] - MARGEM_X, 9 * mm, f"Página {self._pageNumber} de {total_pg}")
                super().showPage()
            super().save()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=MARGEM_X, rightMargin=MARGEM_X, topMargin=TOPO, bottomMargin=BASE,
        title=f"Orçamento {numero} - {orc.get('cliente', '')}", author=nome_emp,
    )
    doc.build(story, onFirstPage=desenhar_fundo, onLaterPages=desenhar_fundo, canvasmaker=CanvasNumerado)
    return buf.getvalue()
