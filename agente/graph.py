"""Dia 2: o agente passa a pesquisar na web e citar fontes.

    START -> planejar -> pesquisar -> responder -> END

- `Estado` é a "memória" que passa de um nó para o outro.
- Cada nó é uma função que recebe o estado e devolve SÓ os campos que mudou.
- Novidade do Dia 2: o nó `pesquisar` busca cada sub-pergunta na web (Tavily)
  e o `responder` só pode afirmar o que estiver nas fontes, citando [n].
- Próximo passo: nó de revisão + aresta condicional (loop de melhoria).
"""

import os
from typing import TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from tavily import TavilyClient


class Fonte(TypedDict):
    sub_pergunta: str
    titulo: str
    url: str
    trecho: str


class Estado(TypedDict, total=False):
    pergunta: str        # o que o usuário quer saber
    plano: list[str]     # sub-perguntas geradas pelo nó "planejar"
    fontes: list[Fonte]  # resultados da web, gerados pelo nó "pesquisar"
    resposta: str        # relatório final


def _texto(msg) -> str:
    """Extrai o texto da resposta do modelo (às vezes vem como lista de blocos)."""
    conteudo = msg.content
    if isinstance(conteudo, str):
        return conteudo
    return "".join(b.get("text", "") for b in conteudo if isinstance(b, dict))


def _criar_llm(modelo: str) -> ChatGoogleGenerativeAI:
    # max_retries: se a API devolver erro temporário (ex.: 503 sobrecarga),
    # tenta de novo esperando um pouco mais a cada vez (backoff exponencial).
    return ChatGoogleGenerativeAI(model=modelo, temperature=0.2, max_retries=2)


llm = _criar_llm(os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

# Modelo reserva (opcional): se o principal falhar mesmo após as tentativas,
# o LangChain chama automaticamente o modelo de fallback.
_fallback = os.getenv("GEMINI_FALLBACK_MODEL")
if _fallback:
    llm = llm.with_fallbacks([_criar_llm(_fallback)])

# Busca na web feita para agentes de IA (plano gratuito: 1.000 buscas/mês)
busca = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

RESULTADOS_POR_PERGUNTA = 3
TAMANHO_MAX_TRECHO = 800  # caracteres por fonte: controla o custo em tokens


def planejar(estado: Estado) -> Estado:
    print("🧭 Planejando sub-perguntas...", flush=True)
    prompt = (
        "Você é um analista de mercado. Quebre a pergunta abaixo em 3 a 5 "
        "sub-perguntas objetivas que precisam ser respondidas para uma boa análise.\n"
        "Escreva cada sub-pergunta como uma boa busca no Google (curta e específica).\n"
        "Responda APENAS com uma sub-pergunta por linha, sem numeração.\n\n"
        f"Pergunta: {estado['pergunta']}"
    )
    linhas = _texto(llm.invoke(prompt)).splitlines()
    plano = [l.strip(" -•*0123456789.").strip() for l in linhas if l.strip()]
    return {"plano": plano[:5]}


def pesquisar(estado: Estado) -> Estado:
    fontes: list[Fonte] = []
    urls_vistas: set[str] = set()
    for sub in estado["plano"]:
        print(f"🌐 Pesquisando: {sub}", flush=True)
        try:
            resultado = busca.search(sub, max_results=RESULTADOS_POR_PERGUNTA)
        except Exception as erro:  # uma busca que falha não derruba o agente
            print(f"   ⚠️  falhou ({erro}); seguindo sem essa busca", flush=True)
            continue
        for r in resultado.get("results", []):
            if r["url"] in urls_vistas:
                continue  # evita a mesma fonte repetida
            urls_vistas.add(r["url"])
            fontes.append(
                {
                    "sub_pergunta": sub,
                    "titulo": r.get("title", ""),
                    "url": r["url"],
                    "trecho": (r.get("content") or "")[:TAMANHO_MAX_TRECHO],
                }
            )
    print(f"   ✅ {len(fontes)} fontes encontradas", flush=True)
    return {"fontes": fontes}


def responder(estado: Estado) -> Estado:
    print("✍️  Escrevendo o relatório com fontes...", flush=True)
    topicos = "\n".join(f"- {p}" for p in estado["plano"])
    fontes_txt = "\n\n".join(
        f"[{i}] {f['titulo']} ({f['url']})\n{f['trecho']}"
        for i, f in enumerate(estado.get("fontes", []), 1)
    )
    prompt = (
        "Escreva um relatório curto (máx. 350 palavras) respondendo à pergunta, "
        "organizado pelas sub-perguntas.\n"
        "REGRAS:\n"
        "- Use SOMENTE informações das fontes abaixo.\n"
        "- Toda afirmação com número ou fato deve citar a fonte no formato [n].\n"
        "- Se as fontes não responderem a uma sub-pergunta, escreva "
        "'Não encontrei dados confiáveis nas fontes'. Nunca invente.\n"
        "- Termine com uma seção 'Fontes' listando [n] título – url das fontes usadas.\n\n"
        f"Pergunta: {estado['pergunta']}\n\nSub-perguntas:\n{topicos}\n\n"
        f"FONTES:\n{fontes_txt or '(nenhuma fonte encontrada)'}"
    )
    return {"resposta": _texto(llm.invoke(prompt))}


def construir_grafo():
    g = StateGraph(Estado)
    g.add_node("planejar", planejar)
    g.add_node("pesquisar", pesquisar)
    g.add_node("responder", responder)
    g.add_edge(START, "planejar")
    g.add_edge("planejar", "pesquisar")
    g.add_edge("pesquisar", "responder")
    g.add_edge("responder", END)
    return g.compile()
