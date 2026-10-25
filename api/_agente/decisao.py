"""O assistente do Mirá: o Jev interpreta, o código busca os dados, o Gemini redige.

Fluxo de uma pergunta
  1. Jev: intenção da pergunta e, se for um documento, para quem ele é.
  2. Código: identifica bairros citados (ou usa o bairro e o hexágono selecionados no site) e
     monta os FATOS com os mesmos dados e pesos que a pessoa está vendo no mapa.
  3. Gemini: redige a resposta curta ou o ofício usando SOMENTE os fatos.
  4. Servidor: confere cada número do texto contra os fatos; se algum não tiver fonte,
     o texto é descartado e vai a versão de modelo (template), que também é completa.
"""
import os
import re

from . import dados, gemini, jev

LIMIAR_CONFIANCA = float(os.getenv("LIMIAR_CONFIANCA", "0.5"))

PERGUNTAS = {
    "intencao": {
        "type": "choice",
        "instructions": ("Qual é a intenção principal da mensagem, enviada ao Mirá, um mapa de onde plantar árvores "
                         "em Manaus? O contexto diz qual bairro e qual ponto do mapa a pessoa está vendo."),
        "criteria": {
            "explicar_local": "Quer entender por que um ponto específico do mapa (o selecionado, 'aqui', 'minha rua') tem ou não prioridade.",
            "onde_plantar": "Quer saber onde plantar árvores num bairro, quais os melhores pontos ou por onde começar.",
            "comparar_bairros": "Quer comparar dois bairros.",
            "panorama_cidade": "Quer saber quais bairros ou regiões de Manaus são mais prioritários.",
            "como_funciona": "Pergunta como o índice, os pesos, as faixas, a robustez, as copas ou os dados funcionam.",
            "redigir_documento": "Pede para escrever um ofício, carta, e-mail, pedido ou justificativa para solicitar plantio.",
            "fora_do_tema": "Não é sobre árvores, plantio, vegetação, calor urbano ou o mapa do Mirá.",
        },
    },
    "destinatario": {
        "type": "choice",
        "instructions": "Se a mensagem pede um documento, a quem ele é dirigido?",
        "criteria": {
            "prefeitura": "Prefeitura, secretaria ou órgão ambiental do município.",
            "legislativo": "Vereador, Câmara Municipal ou outro parlamentar.",
            "escola": "Direção de escola ou creche.",
            "comunidade": "Associação de moradores, vizinhos ou comunidade.",
            "nao_especificado": "Não diz, ou não é um pedido de documento.",
        },
    },
}

DESTINATARIOS = {
    "prefeitura": "Prefeitura de Manaus, ao órgão municipal responsável pela arborização urbana",
    "legislativo": "Câmara Municipal de Manaus, a um(a) vereador(a)",
    "escola": "direção de uma escola ou creche do bairro",
    "comunidade": "associação de moradores ou lideranças comunitárias do bairro",
}

METODO = {
    "o_que_e": "Índice de 0 a 100 de prioridade de plantio de árvores para cada hexágono de cerca de 120 m de Manaus.",
    "fatores": "calor de superfície (Landsat, estação seca), falta de vegetação (NDVI do Sentinel-2) e moradores "
               "(Censo 2022, com 50% de peso extra para idosos e crianças de 0 a 4 anos)",
    "calculo": "cada fator vira um percentil entre os hexágonos elegíveis; o índice é a média ponderada dos percentis",
    "pesos_padrao": "40% calor, 30% falta de verde, 30% moradores; a pessoa pode mudar os pesos no site",
    "faixas": "muito alta = 10% mais prioritários da cidade; alta = do 10% ao 25%; média = do 25% ao 50%; baixa = metade de baixo",
    "robusto": "hexágono que fica entre os 10% mais prioritários com 5 conjuntos de pesos diferentes",
    "fora_do_indice": "água, aeroporto, vegetação densa a proteger (cobertura de 60% ou mais e copa de 30% ou mais) e falta de dado de satélite",
    "copas": "mapa de altura de copas CHMv2 (Meta e WRI) de imagens de 2010 a 2019; copa é vegetação com 3 m ou mais; "
             "provável perda de copa = 30 pontos ou mais de copa antiga acima da cobertura vegetal atual",
    "limites": "temperatura de superfície não é temperatura do ar; o OpenStreetMap é incompleto; espécies e viabilidade "
               "de cada local (calçada, fiação, tubulação) dependem de avaliação técnica em campo",
}

INSTRUCOES_RESPOSTA = """Você é o assistente do Mirá, um mapa que mostra onde plantar árvores em Manaus.
Regras:
- Responda em português do Brasil, em no máximo 6 frases, tom claro e direto, sem saudação.
- Use SOMENTE os FATOS fornecidos. Não invente números, lugares, leis, programas nem nomes de pessoas.
- Escreva números como nos fatos (vírgula decimal; milhar com ponto). Pode arredondar para inteiro.
- Explique o "porquê" com os três fatores (calor, falta de verde, moradores). A posição no ranking e a
  faixa são CONSEQUÊNCIA dos fatores, nunca a causa: não escreva "é prioridade porque é o 1º".
- Para dizer "entre os X% mais quentes / menos verdes / com mais moradores", use os campos
  "entre_os_pct_..." dos fatos. Não faça contas com os números.
- Ao explicar um ponto, cite o índice (de 0 a 100), a faixa e, se ele for robusto, diga que ele fica
  entre os mais prioritários com qualquer conjunto de pesos.
- O tipo de oportunidade só indica que há espaço registrado no OpenStreetMap (praça, escola, avenida
  ou ruas). Escreva, por exemplo, "há ruas locais registradas no OpenStreetMap; a viabilidade depende
  de vistoria". Nunca use as expressões "oportunidade de plantio" ou "local ideal", e não diga que o
  tipo aumenta a prioridade.
- Ao falar de bairros, a causa é a soma de muitos hexágonos que combinam calor, pouco verde e moradores.
  Não atribua a um único fator (por exemplo, "devido à alta concentração populacional"). O ranking
  de bairros ordena pelo número de moradores em hexágonos de prioridade muito alta: diga isso como
  critério, não como causa.
- Não recomende espécies de árvore: diga que isso depende de avaliação técnica.
- Se os fatos trazem uma observação ou limite, respeite-o."""

INSTRUCOES_DOCUMENTO = """Você redige ofícios para pedir o plantio de árvores em bairros de Manaus, com os dados do Mirá.
Regras:
- Português do Brasil, registro formal e cordial, no máximo 380 palavras.
- Estrutura: "[Local], [data]"; linha de destinatário com colchetes para o que não se sabe
  (por exemplo "À [Nome e cargo]"); "Assunto:"; 3 a 5 parágrafos; "Atenciosamente,"; e as linhas
  "[Nome]", "[Entidade ou cargo]" e "[Contato]".
- Parágrafos: quem escreve (use "[Nome da entidade ou do morador]"); a situação do bairro com os
  dados; os pontos mais prioritários (até 3, com as coordenadas e o tipo de oportunidade); o pedido
  concreto (avaliação técnica e plantio priorizando esses pontos, com espécies definidas por técnico,
  de preferência nativas); a fonte dos dados (Mirá, dados abertos) e o limite de que a viabilidade
  de cada local depende de vistoria.
- Use SOMENTE os FATOS. Não invente números, leis, programas, nomes, cargos nem prazos.
- Escreva números como nos fatos (vírgula decimal; milhar com ponto). Texto puro, sem markdown."""


def _conf(a):
    return (a or {}).get("confidence") or 0


def _pesos_txt(p):
    return f"calor {round(100 * p[0])}%, falta de verde {round(100 * p[1])}%, moradores {round(100 * p[2])}%"


def _br(v, casas=0):
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def responder(pergunta: str, contexto: dict | None = None) -> dict:
    contexto = contexto or {}
    d = dados.D()
    pesos = dados.pesos_validos(contexto.get("pesos") or {})
    calc = dados.calcular(pesos)
    hexi = d["_idx"].get(contexto.get("hexagono") or "")
    k_ctx = dados.indice_bairro(contexto.get("bairro"))
    if k_ctx is None and hexi is not None:
        k_ctx = d["bairro"][hexi]
    trilha = []

    estado = pergunta
    if hexi is not None or k_ctx is not None:
        partes = []
        if k_ctx is not None:
            partes.append(f"bairro em foco no mapa: {d['bairros'][k_ctx]}")
        if hexi is not None:
            partes.append("há um ponto (hexágono) selecionado no mapa")
        estado = f"MENSAGEM: {pergunta}\nCONTEXTO: " + "; ".join(partes)

    # 1. Jev interpreta
    j = jev.perguntar(estado, PERGUNTAS)
    a = j["answers"]
    intencao, destinatario = a["intencao"]["choice"], a["destinatario"]["choice"]
    trilha.append({"etapa": "Jev interpreta a pergunta", "modelo": j["model"], "simulado": j["simulado"],
                   "intencao": intencao, "confianca_intencao": _conf(a["intencao"]),
                   "destinatario": destinatario, "confianca_destinatario": _conf(a["destinatario"])})

    if intencao == "fora_do_tema" and _conf(a["intencao"]) >= LIMIAR_CONFIANCA:
        trilha.append({"etapa": "Código", "decisao": "fora do tema: resposta fixa"})
        return _fixo("Eu só respondo sobre onde plantar árvores em Manaus e sobre os dados do Mirá. "
                     "Pergunte, por exemplo, onde vale plantar no seu bairro ou peça um ofício para a prefeitura.", trilha)

    # 2. Código escolhe os dados
    citados = dados.bairros_no_texto(pergunta)
    alvo = citados[0] if citados else k_ctx
    trilha.append({"etapa": "Código escolhe os dados",
                   "bairros_citados": [d["bairros"][k] for k in citados],
                   "bairro_do_mapa": d["bairros"][k_ctx] if k_ctx is not None else None,
                   "ponto_selecionado": d["h3"][hexi] if hexi is not None else None,
                   "pesos": _pesos_txt(pesos)})
    fatos = {"pesos_em_uso": _pesos_txt(pesos),
             "escala_do_indice": "0 a 100",
             "tamanho_do_hexagono": "cerca de 120 m",
             "faixas": METODO["faixas"],
             "oportunidade_significa": "espaço registrado no OpenStreetMap (praça, escola, avenida ou ruas); "
                                       "não entra no índice e não garante viabilidade"}
    locais = []
    tipo = "texto"

    if intencao == "explicar_local":
        if hexi is None:
            return _fixo("Clique num hexágono do mapa e pergunte de novo: assim eu explico aquele ponto.", trilha)
        fatos["ponto"] = dados.ficha(hexi, calc)
        locais.append({"hexagono": d["h3"][hexi], "rotulo": "Ver o ponto no mapa"})
    elif intencao == "comparar_bairros":
        ks = citados[:2]
        if len(ks) == 1 and k_ctx is not None and k_ctx != ks[0]:
            ks = [k_ctx, ks[0]]
        if len(ks) < 2:
            return _fixo("Diga os dois bairros que quer comparar, por exemplo: \"Compare Japiim e Coroado\".", trilha)
        fatos["bairros"] = [dados.resumo_bairro(k, calc, n_top=2) for k in ks]
        fatos["cobertura_vegetal_media_da_cidade_pct"] = dados.media_cidade()
    elif intencao == "panorama_cidade":
        fatos["bairros_com_mais_moradores_em_prioridade_muito_alta"] = dados.ranking_bairros(calc)
        fatos["hexagonos_elegiveis"] = calc["n_eleg"]
        fatos["definicao_muito_alta"] = METODO["faixas"]
    elif intencao == "como_funciona":
        fatos["metodo"] = METODO
    else:  # onde_plantar, redigir_documento ou intenção incerta
        if alvo is None:
            return _fixo("Escolha um bairro no mapa (ou diga o nome dele na pergunta) e eu mostro os pontos mais prioritários.", trilha)
        fatos["bairro"] = dados.resumo_bairro(alvo, calc, n_top=5 if intencao != "redigir_documento" else 3)
        fatos["cobertura_vegetal_media_da_cidade_pct"] = dados.media_cidade()
        fatos["definicao_muito_alta"] = "entre os 10% mais prioritários de Manaus"
        fatos["observacao"] = METODO["limites"]
        for p in fatos["bairro"]["pontos_mais_prioritarios"][:5]:
            locais.append({"hexagono": p["hexagono"], "rotulo": f"{p['posicao_no_bairro']}º ponto"})
        if intencao == "redigir_documento":
            tipo = "documento"
            fatos["destinatario"] = DESTINATARIOS.get(destinatario, DESTINATARIOS["prefeitura"])
            fatos["fonte"] = "Mirá (mira-sandy-kappa.vercel.app): Landsat, Sentinel-2, Censo 2022 do IBGE, OpenStreetMap e CHMv2"
    trilha.append({"etapa": "Código monta os fatos", "tipo": tipo, "n_fatos": _contar(fatos)})

    # 3. e 4. Gemini redige; servidor confere os números
    texto, fonte = None, "template"
    if gemini.ativo():
        try:
            instr = INSTRUCOES_DOCUMENTO if tipo == "documento" else INSTRUCOES_RESPOSTA
            bruto = gemini.redigir(instr, f"PEDIDO: {pergunta}\n\nFATOS: {fatos}", max_tokens=1100 if tipo == "documento" else 450)
            invalidos = numeros_nao_verificados(bruto, fatos)
            if invalidos:
                trilha.append({"etapa": "Servidor confere o texto", "decisao": "descartado", "numeros_sem_fonte": invalidos})
            else:
                texto, fonte = _limpar(bruto), "gemini"
                trilha.append({"etapa": "Gemini redige; servidor confere", "decisao": "aprovado"})
        except Exception as e:  # rede, cota, chave
            trilha.append({"etapa": "Gemini", "erro": str(e)[:200]})
    if texto is None:
        texto = TEMPLATES[intencao if intencao in TEMPLATES else "onde_plantar"](fatos)
    return {"resposta": texto, "tipo": tipo, "trilha": trilha, "fonte_texto": fonte, "locais": locais}


def _fixo(texto, trilha):
    return {"resposta": texto, "tipo": "texto", "trilha": trilha, "fonte_texto": "fixo", "locais": []}


def _contar(o):
    if isinstance(o, dict):
        return sum(_contar(v) for v in o.values())
    if isinstance(o, list):
        return sum(_contar(v) for v in o)
    return 1


def _limpar(t):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", t).strip()


# ---------- conferência dos números ----------
def _numeros(s: str):
    """Números do texto, nas leituras possíveis: '45.187' (milhar) vale 45187 ou 45.187."""
    out = []
    for m in re.finditer(r"\d+(?:[.,]\d+)*", s):
        bruto = m.group(0).rstrip(".,")
        leituras = set()
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", bruto):
            leituras.add(bruto.replace(".", ""))
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", bruto):
            leituras.add(bruto.replace(".", "").replace(",", "."))
        if bruto.count(",") + bruto.count(".") <= 1:
            leituras.add(bruto.replace(",", "."))
        out.append((bruto, leituras or {bruto}))
    return out


def _canon(n):
    try:
        f = float(n)
    except ValueError:
        return n
    return str(int(f)) if f == int(f) else repr(round(f, 5))


def numeros_nao_verificados(texto: str, fatos: dict):
    """Cada número do texto tem de estar nos fatos (exato ou arredondado). Nenhum número é liberado
    por padrão: complementos, escalas e definições vão nos fatos, calculados pelo servidor."""
    permitidos = set()
    for _, leituras in _numeros(str(fatos)):
        for n in leituras:
            permitidos.add(_canon(n))
            try:
                f = float(n)
                permitidos.add(str(round(f)))
                permitidos.add(_canon(round(f, 1)))
            except ValueError:
                pass
    ruins = []
    for bruto, leituras in _numeros(texto):
        if not any(_canon(n) in permitidos for n in leituras):
            ruins.append(bruto)
    return sorted(set(ruins))


# ---------- textos de modelo (sem LLM) ----------
def _pct(v):
    return "—" if v is None else f"{_br(v)}%"


def _graus(v):
    return "—" if v is None else ("+" if v >= 0 else "−") + _br(abs(v), 1) + " °C"


def t_local(f):
    p = f["ponto"]
    if p.get("fora_do_indice_motivo"):
        txt = (f"Este ponto, em {p['bairro']}, está fora do índice: {p['fora_do_indice_motivo']}. "
               f"A cobertura vegetal é de {_pct(p['cobertura_vegetal_pct'])}.")
    else:
        txt = (f"Este ponto, em {p['bairro']}, tem índice {p['indice_0_100']} de 100, prioridade {p['faixa_prioridade']} "
               f"({p['posicao_na_cidade']} na cidade, {p['posicao_no_bairro']} no bairro). "
               f"É mais quente que {p['mais_quente_que_pct_dos_hexagonos']}% dos hexágonos (superfície {_graus(p['superficie_vs_mediana_c'])}), "
               f"tem {_pct(p['cobertura_vegetal_pct'])} de cobertura vegetal, menos verde que {p['menos_verde_que_pct_dos_hexagonos']}% deles, "
               f"e cerca de {_br(p['moradores'])} moradores, mais que em {p['mais_moradores_que_pct_dos_hexagonos']}% dos hexágonos. "
               f"Espaço registrado no OpenStreetMap: {p['oportunidade']} (não entra no índice).")
        if p.get("robusto_a_qualquer_peso"):
            txt += " É robusto: fica no topo com qualquer conjunto de pesos."
    if p.get("provavel_perda_de_copa"):
        txt += (f" Há indício de perda de copa: tinha {p['copa_arvores_pct_2010_2019']}% de copa nas imagens de 2010 a 2019.")
    return txt + f" Pesos em uso: {f['pesos_em_uso']}."


def t_onde(f):
    b = f["bairro"]
    txt = (f"Em {b['bairro']}, {b['hexagonos_prioridade_muito_alta']} hexágonos estão entre os 10% mais prioritários de Manaus, "
           f"com cerca de {_br(b['moradores_em_muito_alta'])} moradores. ")
    pts = b["pontos_mais_prioritarios"][:3]
    if pts:
        txt += "Os pontos mais prioritários: " + "; ".join(
            f"{p['posicao_no_bairro']}º, índice {p['indice']} ({p['oportunidade']}, {p['coordenadas']})" for p in pts) + ". "
    txt += "Espécies e viabilidade de cada local dependem de avaliação técnica em campo."
    return txt


def t_comparar(f):
    a, b = f["bairros"]
    def lin(x):
        return (f"{x['bairro']}: {x['hexagonos_prioridade_muito_alta']} hexágonos de prioridade muito alta "
                f"({_br(x['pct_do_bairro_em_muito_alta'], 1)}% do bairro), {_br(x['moradores_em_muito_alta'])} moradores neles, "
                f"cobertura vegetal média de {_pct(x['cobertura_vegetal_media_pct'])} e superfície {_graus(x['superficie_media_vs_cidade_c'])}")
    return f"{lin(a)}. {lin(b)}. Média de cobertura vegetal da cidade: {f['cobertura_vegetal_media_da_cidade_pct']}%."


def t_panorama(f):
    r = f["bairros_com_mais_moradores_em_prioridade_muito_alta"]
    return ("Bairros com mais moradores em hexágonos de prioridade muito alta: " +
            "; ".join(f"{x['bairro']} ({_br(x['moradores_em_muito_alta'])} pessoas, {x['hexagonos_muito_alta']} hexágonos)" for x in r) +
            f". Pesos em uso: {f['pesos_em_uso']}.")


def t_metodo(f):
    m = f["metodo"]
    return (f"{m['o_que_e']} Combina {m['fatores']}: {m['calculo']}. Pesos padrão: {m['pesos_padrao']}. "
            f"Faixas: {m['faixas']}. Ficam fora: {m['fora_do_indice']}.")


def t_documento(f):
    b = f["bairro"]
    pts = b["pontos_mais_prioritarios"][:3]
    lista = "\n".join(f"  {p['posicao_no_bairro']}. {p['coordenadas']} — índice {p['indice']} de 100, {p['oportunidade']}"
                      for p in pts)
    return f"""[Local], [data]

À [Nome e cargo] — {f['destinatario']}

Assunto: Solicitação de plantio de árvores no bairro {b['bairro']}

[Nome da entidade ou do morador] vem, respeitosamente, solicitar a avaliação técnica e o plantio de árvores no bairro {b['bairro']}, com base nos dados abertos do Mirá.

Segundo o Mirá, {b['hexagonos_prioridade_muito_alta']} áreas de cerca de 120 m do bairro estão entre as 10% mais prioritárias de Manaus para o plantio, e nelas moram cerca de {_br(b['moradores_em_muito_alta'])} pessoas. A cobertura vegetal média do bairro é de {_pct(b['cobertura_vegetal_media_pct'])} (média da cidade: {f['cobertura_vegetal_media_da_cidade_pct']}%), e a superfície fica, em média, {_graus(b['superficie_media_vs_cidade_c'])} em relação à mediana da cidade na estação seca.

Os pontos mais prioritários do bairro são:
{lista}

Solicitamos que esses pontos sejam vistoriados e considerados num plano de arborização, com espécies definidas por técnico habilitado, de preferência nativas da região. A viabilidade de cada local (calçada, fiação, tubulação) depende dessa vistoria.

Fonte: {f['fonte']}.

Atenciosamente,

[Nome]
[Entidade ou cargo]
[Contato]"""


TEMPLATES = {"explicar_local": t_local, "onde_plantar": t_onde, "comparar_bairros": t_comparar,
             "panorama_cidade": t_panorama, "como_funciona": t_metodo, "redigir_documento": t_documento}
