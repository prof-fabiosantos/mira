# Mirá — onde plantar árvores em Manaus

Índice de prioridade de plantio de árvores para cada hexágono de ~120 m da área urbana de Manaus,
combinando calor de superfície, falta de vegetação e moradores expostos, com as restrições de espaço.
**Mirá** quer dizer “árvore” em nheengatu (Navarro, *Curso de Língua Geral*, USP).

O índice indica **onde priorizar**. A escolha de espécies (de preferência nativas) e a viabilidade de
cada local (fiação, calçada, tubulação, solo) dependem de avaliação técnica em campo.

## Rodar

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python pipeline/rodar_tudo.py     # todas as etapas
```

Antes, copie o `bairros.geojson` do Akub (`public/data/bairros.geojson`) para `dados/entrada/`
(o pacote já vem com uma cópia).

| Etapa | Script | Tempo aproximado | Baixa |
|---|---|---|---|
| 1 | `01_grade.py` | segundos | nada |
| 2 | `02_calor.py` | 10–30 min | ~25 cenas Landsat (só o recorte de Manaus) |
| 3 | `03_vegetacao.py` | 15–40 min | até 16 cenas Sentinel-2 (recorte) |
| 4 | `04_populacao.py` | 2–5 min | ~135 MB do IBGE (fica em cache) |
| 5 | `05_viabilidade.py` | 1–5 min | OpenStreetMap (Overpass) |
| 6 | `06_indice.py` | segundos | nada |
| 7 | `07_web.py` | segundos | nada (gera o `web.json` do site) |

Para retomar de uma etapa: `python pipeline/rodar_tudo.py --a-partir-de 4`.
Para testar outros pesos: `python pipeline/rodar_tudo.py --so 6 -- --peso-calor 0.5 --peso-verde 0.3 --peso-pessoas 0.2`.

## Site (fase 2)

O site é estático e fica em `public/`: `index.html`, `app.js` e `data/web.json`, gerado pela etapa 7. Para ver localmente:

```bash
cd public
python -m http.server 8000      # abra http://localhost:8000
```

Tem o mapa dos hexágonos (prioridade, calor, vegetação e moradores), a ficha de cada hexágono com a explicação do porquê, os pesos ajustáveis, o ranking dos bairros, o relatório por bairro para imprimir ou salvar em PDF, a relação vegetação–calor e o download dos dados.

**Publicar no Vercel:** suba o projeto para um repositório no GitHub e importe no Vercel com o Framework Preset "Other". O Vercel serve a pasta `public` automaticamente. Lembre de configurar o Git com a identidade da conta dona do projeto (`git config --local user.email`), como no Akub.

## Saídas (`public/data/`)

| Arquivo | Conteúdo |
|---|---|
| `hexagonos.csv` | Uma linha por hexágono: índice, faixa, posição na cidade e no bairro, robustez, motivo de exclusão, os três percentis e os dados brutos de cada fator |
| `bairros.csv` | Por bairro: população, cobertura vegetal média, anomalia de superfície média, quantos hexágonos e moradores estão em prioridade muito alta, a divisão desses hexágonos por tipo de oportunidade, e os 5 hexágonos mais prioritários |
| `relacao.json` | Relação observada entre cobertura vegetal e temperatura de superfície em Manaus, com intervalo de confiança |
| `meta.json` | Pesos, fontes, cenas de satélite usadas e data de geração |

## Metodologia

**Grade.** Hexágonos H3 na resolução 10 (~15 mil m², ~120 m de diâmetro) cujo centro está dentro de um dos 63 bairros da malha do IBGE (Censo 2022). O tamanho acompanha a resolução nativa da banda térmica do Landsat (100 m): uma grade menor sugeriria um detalhe que o dado não tem.

**Calor.** Landsat 8/9, Coleção 2, Nível 2, banda ST_B10, cenas de julho a outubro (estação seca) com menos de 40% de nuvem. Nuvem, sombra, cirro e água são mascarados pela banda QA_PIXEL. Em cada cena, a anomalia do hexágono é a diferença entre a mediana dos seus pixels e a mediana de todos os hexágonos; o valor final é a mediana entre cenas. Entram só cenas que cobrem ao menos 80% da grade, para que a referência de cada cena seja a cidade inteira. É temperatura da superfície por volta das 10h, e não do ar.

**Vegetação.** Sentinel-2 L2A (10 m), julho a outubro, até 16 cenas com menos de 20% de nuvem. Para cenas com processamento 04.00 ou posterior, desconta-se o deslocamento de 1000 nas reflectâncias. Pixels válidos pela banda SCL (vegetação, solo, água, não classificado). Por hexágono: NDVI mediano, cobertura vegetal (fração de pixels de terra com NDVI ≥ 0,5) e fração de água. Valor final: mediana entre cenas.

**Pessoas.** Censo 2022, agregados por setor censitário: total de pessoas (V0001), idosos de 60 anos ou mais (V01018, V01019, V01029, V01030) e crianças de 0 a 4 anos (V01009, V01020). Valores suprimidos por sigilo contam como ausentes. A população de cada setor é distribuída entre os hexágonos que ele toca, proporcionalmente à área sobreposta multiplicada pela habitabilidade do hexágono, (1 − fração de água) × (1 − 0,8 × cobertura vegetal), para que moradores não sejam postos em rio ou mata. A parte de um setor fora da área urbana dos bairros fica com a sua parcela.

**Tipo de oportunidade.** OpenStreetMap: praças, parques, jardins e gramados (área), escolas e creches (contagem), avenidas e vias principais (comprimento), ruas locais (comprimento) e área aeroportuária. Cada hexágono recebe o primeiro tipo que se aplica: **praça ou área pública** (≥ 500 m²; plantio em terreno público), **escola ou creche** (parceria e educação ambiental), **avenida com canteiro** (≥ 100 m de via principal; arborização viária), **ruas locais** (≥ 100 m; depende da largura da calçada, a confirmar em campo) ou **sem registro no OpenStreetMap**. O tipo não entra no índice: o OpenStreetMap é incompleto e não informa a largura das calçadas, então a falta de registro não significa falta de espaço.

**Exclusões.** Não recebem índice os hexágonos com mais de 50% de água, em área aeroportuária, com vegetação já densa (cobertura ≥ 60%, áreas a proteger, e não a plantar) ou sem dado de satélite.

**Índice.** Entre os hexágonos elegíveis, cada fator vira um percentil de 0 a 1: calor (anomalia de superfície), verde (NDVI, invertido) e pessoas (moradores + 50% a mais para idosos e crianças). Índice = 100 × média ponderada, com pesos padrão de 0,4, 0,3 e 0,3. Faixas: muito alta (10% mais prioritários), alta (75–90), média (50–75) e baixa (metade de baixo).

**Robustez.** O índice é recalculado com quatro outros conjuntos de pesos (iguais; ênfase em calor; em verde; em pessoas). É "robusto" o hexágono que fica entre os 10% mais prioritários em todos eles. Esses são os locais que nenhuma escolha razoável de pesos tiraria do topo.

**Relação vegetação–calor.** Regressão linear da anomalia de superfície sobre a cobertura vegetal, nos hexágonos sem água nem aeroporto. O intervalo de confiança de 95% vem de 500 reamostragens de **bairros inteiros**: hexágonos vizinhos não são observações independentes, e reamostrá-los um a um daria uma confiança falsa. É uma associação observada em Manaus, e não uma garantia de quanto cada árvore resfria.

## Limites

- Temperatura de superfície não é temperatura do ar, e as cenas são de ~10h.
- O NDVI de 10 m não separa árvores de gramado; a segmentação de copas fica para a fase 3.
- A distribuição da população dentro do setor é uma estimativa.
- O OpenStreetMap não registra todas as praças e calçadas de Manaus.
- A malha de bairros é a do Censo 2022 (63 bairros); a Colônia Japonesa (Lei 3.592/2025) ainda não está nela.

## Testes

`python testes/rodar_testes.py` roda o pipeline inteiro com dados sintéticos de cada etapa (satélite, população no formato do IBGE e OpenStreetMap), sem rede e numa pasta temporária: não toca em `dados/` nem em `public/`.
