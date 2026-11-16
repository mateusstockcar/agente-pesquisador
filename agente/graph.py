"""Dia 1: o grafo mais simples possível.

    START -> planejar -> responder -> END

- `Estado` é a "memória" que passa de um nó para o outro.
- Cada nó é uma função que recebe o estado e devolve SÓ os campos que mudou.
- Nos próximos dias vamos adicionar: busca na web, leitura de páginas,
  um nó de revisão e uma aresta condicional (loop de melhoria).
"""

import os
from typing import TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph


class Estado(TypedDict, total=False):
    pergunta: str      # o que o usuário quer saber
    plano: list[str]   # sub-perguntas geradas pelo nó "planejar"
    resposta: str      # relatório final


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


def planejar(estado: Estado) -> Estado:
    print("🧭 Planejando sub-perguntas...", flush=True)
    prompt = (
        "Você é um analista de mercado. Quebre a pergunta abaixo em 3 a 5 "
        "sub-perguntas objetivas que precisam ser respondidas para uma boa análise.\n"
        "Responda APENAS com uma sub-pergunta por linha, sem numeração.\n\n"
        f"Pergunta: {estado['pergunta']}"
    )
    linhas = _texto(llm.invoke(prompt)).splitlines()
    plano = [l.strip(" -•*0123456789.").strip() for l in linhas if l.strip()]
    return {"plano": plano[:5]}


def responder(estado: Estado) -> Estado:
    print("✍️  Escrevendo o relatório...", flush=True)
    topicos = "\n".join(f"- {p}" for p in estado["plano"])
    prompt = (
        "Escreva um relatório curto (máx. 300 palavras) respondendo à pergunta, "
        "organizado pelas sub-perguntas abaixo. Seja direto e deixe claro o que "
        "é conhecimento geral e o que precisaria ser verificado em fontes atuais.\n\n"
        f"Pergunta: {estado['pergunta']}\n\nSub-perguntas:\n{topicos}"
    )
    return {"resposta": _texto(llm.invoke(prompt))}


def construir_grafo():
    g = StateGraph(Estado)
    g.add_node("planejar", planejar)
    g.add_node("responder", responder)
    g.add_edge(START, "planejar")
    g.add_edge("planejar", "responder")
    g.add_edge("responder", END)
    return g.compile()
