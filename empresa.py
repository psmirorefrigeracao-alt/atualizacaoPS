# empresa.py — Dados da empresa usados no PDF e na página de aceite do cliente.
# Edite os valores entre aspas. Campos vazios ("") simplesmente não aparecem no PDF.

EMPRESA = {
    "nome_fantasia": "P&S Refrigeração e Climatização",
    "razao_social": "",                # ex.: "12.345.678 Fulano de Tal"
    "cnpj": "",                        # ex.: "12.345.678/0001-90"
    "atividade": "Instalação e manutenção de sistemas de ar condicionado, climatização e refrigeração.",
    "endereco": "",                    # ex.: "Rua Exemplo, 123 – Centro"
    "cep_cidade": "",                  # ex.: "84000-000 - Cidade-PR"
    "telefone": "",                    # ex.: "(43) 99999-9999"  (também recebe o aviso de aprovação)
    "email": "",
    "site": "",
    "tecnico": "",                     # técnico responsável
    "cpf_tecnico": "",                 # deixe vazio para não mostrar
    "validade_dias": 5,

    "objetivo": (
        "A {nome} tem como missão superar as expectativas do cliente por meio do compromisso "
        "com resultados, excelência no atendimento e qualidade nos serviços executados, evitando "
        "contratempos, falhas operacionais ou danos aos equipamentos."
    ),
    "pagamento_padrao": (
        "A forma de pagamento será definida em comum acordo entre as partes no momento da "
        "contratação dos serviços."
    ),
    "observacoes": [
        "Todos os serviços serão executados por profissionais qualificados e devidamente capacitados;",
        "As instalações seguirão os padrões técnicos, normas vigentes e recomendações do fabricante;",
        "A garantia dos equipamentos será de acordo com o fabricante;",
        "Qualquer serviço ou material não descrito neste orçamento será previamente comunicado ao "
        "cliente para análise e aprovação;",
    ],
    "fechamento": "Colocamo-nos à inteira disposição para quaisquer esclarecimentos ou dúvidas.",
}
