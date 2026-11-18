"""Dia 3: o agente revisa o próprio trabalho e decide se precisa pesquisar mais.

    START -> planejar -> pesquisar -> responder -> revisar --(aprovado?)--> END
                             ^                                   |
                             └──────────── não: buscar lacunas ──┘

Conceitos novos do Dia 3:
- Aresta condicional: depois de `revisar`, uma função (`decidir`) escolhe o
  próximo nó. É isso que transforma um fluxo linear num AGENTE com loop.
- Saída estruturada: o revisor responde num formato fixo (Pydantic), e não em
  texto livre — assim o código consegue ler "aprovado: true/false" com segurança.
- Reducer: `fontes` ACUMULA a cada volta do loop em vez de ser sobrescrita.
- Limite de iterações: todo loop de agente precisa de um freio (custo e tempo).
"""

import operator
import os
from typing import Annotated, Literal, TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from tavily import TavilyClient

MAX_REVISOES = 2           # freio do loop: no máximo 2 rodadas extras de pesquisa
RESULTADOS_POR_PERGUNTA = 3
TAMANHO_MAX_TRECHO = 800   # caracteres por fonte: controla o custo em tokens


class Fonte(TypedDict):
    sub_pergunta: str
    titulo: str
    url: str
    trecho: str


class Revisao(BaseModel):
    """Formato fixo que o revisor é obrigado a devolver."""

    aprovado: bool = Field(description="true se o relatório está bom o suficiente para entregar")
    nota: int = Field(description="nota de 0 a 10 para a qualidade do relatório")
    problemas: list[str] = Field(description="problemas encontrados (afirmações sem fonte, lacunas, contradições)")
    novas_buscas: list[str] = Field(description="até 3 buscas na web que resolveriam as lacunas; vazio se aprovado")


class Estado(TypedDict, total=False):
    pergunta: str
    plano: list[str]                                # sub-perguntas originais
    buscas_pendentes: list[str]                     # o que o nó pesquisar vai buscar agora
    fontes: Annotated[list[Fonte], operator.add]    # reducer: cada volta SOMA fontes novas
    resposta: str
    revisao: dict
    revisoes_feitas: int


def _texto(msg) -> str:
    """Extrai o texto da resposta do modelo (às vezes vem como lista de blocos)."""
    conteudo = msg.content
    if isinstance(conteudo, str):
        return conteudo
    return "".join(b.get("text", "") for b in conteudo if isinstance(b, dict))


def _criar_llm(modelo: str) -> ChatGoogleGenerativeAI:
    # max_retries: tenta de novo em erros temporários (ex.: 503), com espera crescente.
    # timeout: se o modelo não responder em 60s, desiste dessa tentativa (nada fica "pendurado").
    return ChatGoogleGenerativeAI(model=modelo, temperature=0.2, max_retries=2, timeout=60)


_principal = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
_fallback = os.getenv("GEMINI_FALLBACK_MODEL")

llm = _criar_llm(_principal)
# Mesmo modelo, mas obrigado a responder no formato da classe Revisao.
llm_revisor = _criar_llm(_principal).with_structured_output(Revisao)
if _fallback:
    llm = llm.with_fallbacks([_criar_llm(_fallback)])
    llm_revisor = llm_revisor.with_fallbacks(
        [_criar_llm(_fallback).with_structured_output(Revisao)]
    )

busca = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ---------------------------------------------------------------- nós

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
    plano = [l.strip(" -•*0123456789.").strip() for l in linhas if l.strip()][:5]
    return {"plano": plano, "buscas_pendentes": plano, "revisoes_feitas": 0}


def pesquisar(estado: Estado) -> Estado:
    urls_vistas = {f["url"] for f in estado.get("fontes", [])}  # não repete fontes de voltas anteriores
    novas: list[Fonte] = []
    for sub in estado["buscas_pendentes"]:
        print(f"🌐 Pesquisando: {sub}", flush=True)
        try:
            resultado = busca.search(sub, max_results=RESULTADOS_POR_PERGUNTA)
        except Exception as erro:  # uma busca que falha não derruba o agente
            print(f"   ⚠️  falhou ({erro}); seguindo sem essa busca", flush=True)
            continue
        for r in resultado.get("results", []):
            if r["url"] in urls_vistas:
                continue
            urls_vistas.add(r["url"])
            novas.append(
                {
                    "sub_pergunta": sub,
                    "titulo": r.get("title", ""),
                    "url": r["url"],
                    "trecho": (r.get("content") or "")[:TAMANHO_MAX_TRECHO],
                }
            )
    total = len(estado.get("fontes", [])) + len(novas)
    print(f"   ✅ +{len(novas)} fontes novas ({total} no total)", flush=True)
    return {"fontes": novas}  # o reducer soma com as que já existiam


def _fontes_numeradas(estado: Estado) -> str:
    return "\n\n".join(
        f"[{i}] {f['titulo']} ({f['url']})\n{f['trecho']}"
        for i, f in enumerate(estado.get("fontes", []), 1)
    )


def responder(estado: Estado) -> Estado:
    print("✍️  Escrevendo o relatório com fontes...", flush=True)
    topicos = "\n".join(f"- {p}" for p in estado["plano"])
    correcoes = ""
    if estado.get("revisao"):
        correcoes = (
            "\nUma versão anterior foi reprovada pelo revisor por estes problemas. "
            "Corrija-os usando as fontes:\n"
            + "\n".join(f"- {p}" for p in estado["revisao"]["problemas"])
            + "\n"
        )
    prompt = (
        "Escreva um relatório curto (máx. 350 palavras) respondendo à pergunta, "
        "organizado pelas sub-perguntas.\n"
        "REGRAS:\n"
        "- Use SOMENTE informações das fontes abaixo.\n"
        "- Toda afirmação com número ou fato deve citar a fonte no formato [n].\n"
        "- Se as fontes não responderem a uma sub-pergunta, escreva "
        "'Não encontrei dados confiáveis nas fontes'. Nunca invente.\n"
        "- Termine com uma seção 'Fontes' listando [n] título – url das fontes usadas.\n"
        f"{correcoes}\n"
        f"Pergunta: {estado['pergunta']}\n\nSub-perguntas:\n{topicos}\n\n"
        f"FONTES:\n{_fontes_numeradas(estado) or '(nenhuma fonte encontrada)'}"
    )
    return {"resposta": _texto(llm.invoke(prompt))}


def revisar(estado: Estado) -> Estado:
    print("🧐 Revisando o relatório...", flush=True)
    prompt = (
        "Você é um revisor exigente de relatórios de mercado. Avalie o relatório abaixo "
        "comparando com as fontes.\n"
        "Reprove (aprovado=false) se: alguma afirmação com número não tiver [n]; "
        "alguma citação [n] não for sustentada pela fonte; ou alguma sub-pergunta "
        "importante ficou sem resposta.\n"
        "Se reprovar, sugira até 3 novas buscas curtas que resolveriam as lacunas.\n\n"
        f"Pergunta: {estado['pergunta']}\n\n"
        f"RELATÓRIO:\n{estado['resposta']}\n\n"
        f"FONTES:\n{_fontes_numeradas(estado)}"
    )
    rev: Revisao = llm_revisor.invoke(prompt)
    status = "aprovado ✅" if rev.aprovado else "reprovado ❌"
    print(f"   Nota {rev.nota}/10 — {status}", flush=True)
    for p in rev.problemas:
        print(f"   • {p}", flush=True)
    return {
        "revisao": rev.model_dump(),
        "buscas_pendentes": rev.novas_buscas[:3],
        "revisoes_feitas": estado.get("revisoes_feitas", 0) + 1,
    }


# ---------------------------------------------------------------- decisão

def decidir(estado: Estado) -> Literal["pesquisar", "fim"]:
    """A aresta condicional: olha o estado e escolhe o próximo nó."""
    rev = estado["revisao"]
    if rev["aprovado"] or not estado["buscas_pendentes"]:
        return "fim"
    if estado["revisoes_feitas"] > MAX_REVISOES:
        print("   ⏹️  Limite de revisões atingido — entregando a melhor versão.", flush=True)
        return "fim"
    print("   🔁 Voltando a pesquisar as lacunas...", flush=True)
    return "pesquisar"


def construir_grafo():
    g = StateGraph(Estado)
    g.add_node("planejar", planejar)
    g.add_node("pesquisar", pesquisar)
    g.add_node("responder", responder)
    g.add_node("revisar", revisar)
    g.add_edge(START, "planejar")
    g.add_edge("planejar", "pesquisar")
    g.add_edge("pesquisar", "responder")
    g.add_edge("responder", "revisar")
    g.add_conditional_edges("revisar", decidir, {"pesquisar": "pesquisar", "fim": END})
    return g.compile()
