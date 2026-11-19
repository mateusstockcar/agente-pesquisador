"""Dia 4: Evals — medir a qualidade do agente em várias perguntas de uma vez.

Como rodar (na raiz do projeto):
    uv run python -m evals.rodar            # todas as perguntas
    uv run python -m evals.rodar --limite 2 # só as 2 primeiras (teste rápido)

Para cada pergunta do arquivo evals/perguntas.json, o agente roda do começo ao fim
e o resultado passa por 2 tipos de avaliação:

1. Checagens determinísticas (código puro, sem IA — barato e 100% reproduzível):
   - números sem fonte: frases com número que não citam nenhuma fonte [n]
   - citações inválidas: [n] que aponta para uma fonte que não existe
   - fontes fracas: citações de redes sociais / fóruns
   - edge case: na pergunta-pegadinha, o agente admitiu que não encontrou dados?

2. Juiz independente (LLM-as-judge): outra chamada de IA, com uma rubrica fixa,
   que NÃO é o revisor do agente. Serve para responder: o revisor é exigente o
   suficiente? Se o revisor dá 10 e o juiz dá 6, o revisor está bonzinho demais.

No fim: tabela no terminal + CSV em evals/resultados/ para comparar versões.
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from langfuse.langchain import CallbackHandler  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agente.graph import _criar_llm, _fontes_numeradas, construir_grafo  # noqa: E402

PASTA = Path(__file__).parent
DOMINIOS_FRACOS = ("reddit.com", "quora.com", "facebook.com", "instagram.com", "tiktok.com",
                   "youtube.com", "twitter.com", "x.com", "linkedin.com")
PAUSA_ENTRE_PERGUNTAS = 5  # segundos: respeita os limites do plano gratuito do Gemini


# ------------------------------------------------------------- checagens determinísticas

def _corpo(relatorio: str) -> str:
    """Remove a seção final 'Fontes' para analisar só o texto do relatório."""
    partes = re.split(r"\n[#*\s]*Fontes[*:\s]*\n", relatorio, flags=re.IGNORECASE)
    return partes[0]


def _citacoes(texto: str) -> set[int]:
    numeros = set()
    for grupo in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", texto):
        numeros.update(int(n) for n in re.split(r"\s*,\s*", grupo))
    return numeros


def numeros_sem_fonte(relatorio: str) -> list[str]:
    frases = re.split(r"(?<=[.!?])\s+|\n", _corpo(relatorio))
    suspeitas = []
    for f in frases:
        f = f.strip()
        if not f or f.startswith("#"):
            continue
        sem_citacao = re.sub(r"\[[\d,\s]+\]", "", f)
        tem_numero = re.search(r"\d", sem_citacao)
        citou = re.search(r"\[\d", f)
        if tem_numero and not citou:
            suspeitas.append(f[:120])
    return suspeitas


def citacoes_invalidas(relatorio: str, total_fontes: int) -> list[int]:
    return sorted(n for n in _citacoes(_corpo(relatorio)) if n < 1 or n > total_fontes)


def fontes_fracas_citadas(relatorio: str, fontes: list[dict]) -> list[str]:
    usadas = _citacoes(_corpo(relatorio))
    return [fontes[n - 1]["url"] for n in usadas
            if 1 <= n <= len(fontes) and any(d in fontes[n - 1]["url"] for d in DOMINIOS_FRACOS)]


def admitiu_falta_de_dados(relatorio: str) -> bool:
    return bool(re.search(r"não encontrei|não foram encontrad|sem dados|não há dados|não há informaç",
                          relatorio, flags=re.IGNORECASE))


# ------------------------------------------------------------- juiz independente

class Avaliacao(BaseModel):
    fidelidade: int = Field(description="1 a 5: as afirmações são sustentadas pelas fontes citadas?")
    cobertura: int = Field(description="1 a 5: o relatório responde de fato à pergunta?")
    clareza: int = Field(description="1 a 5: está claro, organizado e útil para um gestor?")
    comentario: str = Field(description="uma frase com o principal problema ou ponto forte")


# O juiz deve ser um modelo DIFERENTE (de preferência mais forte) do que o agente usa.
# Sem fallback de propósito: se o juiz cair para o mesmo modelo do agente, ele vira
# "aluno corrigindo a própria prova" e a nota perde o valor. Melhor falhar e avisar.
_modelo_juiz = os.getenv("GEMINI_JUDGE_MODEL", "gemini-3.5-flash")
juiz = _criar_llm(_modelo_juiz).with_structured_output(Avaliacao)
print(f"⚖️  Juiz: {_modelo_juiz}", flush=True)


def julgar(pergunta: str, estado: dict) -> Avaliacao:
    prompt = (
        "Você é um avaliador rigoroso e imparcial de relatórios de pesquisa de mercado. "
        "Seja exigente: nota 5 só para trabalho realmente excelente.\n"
        "Rubrica (1 a 5 cada):\n"
        "- fidelidade: cada afirmação citada é de fato sustentada pela fonte indicada? Penalize "
        "números sem fonte, citações que dizem mais do que a fonte e fontes pouco confiáveis.\n"
        "- cobertura: o relatório responde à pergunta feita, sem lacunas importantes?\n"
        "- clareza: um gestor entenderia e conseguiria agir com base nele?\n\n"
        f"PERGUNTA: {pergunta}\n\nRELATÓRIO:\n{estado['resposta']}\n\n"
        f"FONTES:\n{_fontes_numeradas(estado)}"
    )
    return juiz.invoke(prompt)


# ------------------------------------------------------------- execução

def main() -> None:
    perguntas = json.loads((PASTA / "perguntas.json").read_text(encoding="utf-8"))
    if "--limite" in sys.argv:
        perguntas = perguntas[: int(sys.argv[sys.argv.index("--limite") + 1])]

    grafo = construir_grafo()
    langfuse = CallbackHandler()
    linhas = []

    for i, item in enumerate(perguntas, 1):
        print(f"\n{'=' * 70}\n[{i}/{len(perguntas)}] {item['pergunta']}\n{'=' * 70}", flush=True)
        inicio = time.time()
        try:
            estado = grafo.invoke({"pergunta": item["pergunta"]}, config={"callbacks": [langfuse]})
            segundos = round(time.time() - inicio, 1)
            fontes = estado.get("fontes", [])
            relatorio = estado["resposta"]
            sem_fonte = numeros_sem_fonte(relatorio)
            try:  # se o juiz falhar, as checagens automáticas continuam valendo
                av = julgar(item["pergunta"], estado)
            except Exception as erro_juiz:
                print(f"   ⚠️  juiz indisponível: {str(erro_juiz)[:100]}", flush=True)
                av = None
            linha = {
                "id": item["id"],
                "segundos": segundos,
                "revisoes": estado.get("revisoes_feitas", 0),
                "fontes": len(fontes),
                "nota_revisor": estado.get("revisao", {}).get("nota"),
                "nota_juiz": round((av.fidelidade + av.cobertura + av.clareza) / 15 * 10, 1) if av else None,
                "fidelidade": av.fidelidade if av else None,
                "cobertura": av.cobertura if av else None,
                "clareza": av.clareza if av else None,
                "numeros_sem_fonte": len(sem_fonte),
                "citacoes_invalidas": len(citacoes_invalidas(relatorio, len(fontes))),
                "fontes_fracas": len(fontes_fracas_citadas(relatorio, fontes)),
                "edge_case_ok": (admitiu_falta_de_dados(relatorio) if item.get("esperado_sem_dados") else ""),
                "comentario_juiz": av.comentario if av else "juiz indisponível",
                "modelo_juiz": _modelo_juiz,
                "erro": "",
            }
            for f in sem_fonte[:3]:
                print(f"   ⚠️  número sem fonte: {f}", flush=True)
        except Exception as erro:  # uma pergunta que falha não derruba a bateria inteira
            linha = {"id": item["id"], "segundos": round(time.time() - inicio, 1), "erro": str(erro)[:200]}
            print(f"   ❌ erro: {erro}", flush=True)
        linhas.append(linha)
        if i < len(perguntas):
            time.sleep(PAUSA_ENTRE_PERGUNTAS)

    _imprimir_resumo(linhas)
    _salvar_csv(linhas)


def _imprimir_resumo(linhas: list[dict]) -> None:
    ok = [l for l in linhas if not l.get("erro")]
    print(f"\n\n{'=' * 70}\nRESULTADO DOS EVALS\n{'=' * 70}")
    print(f"{'pergunta':<18}{'revisor':>8}{'juiz':>7}{'s/fonte':>9}{'inválid.':>9}{'fracas':>8}{'tempo':>8}")
    for l in linhas:
        if l.get("erro"):
            print(f"{l['id']:<18}  ERRO: {l['erro'][:45]}")
            continue
        print(f"{l['id']:<18}{str(l['nota_revisor']):>8}{str(l['nota_juiz'] if l['nota_juiz'] is not None else '-'):>7}{l['numeros_sem_fonte']:>9}"
              f"{l['citacoes_invalidas']:>9}{l['fontes_fracas']:>8}{l['segundos']:>7}s")
    if ok:
        media = lambda campo: round(sum(l[campo] for l in ok if l[campo] is not None) / len(ok), 1)
        print("-" * 70)
        print(f"{'MÉDIA':<18}{media('nota_revisor'):>8}{media('nota_juiz'):>7}{media('numeros_sem_fonte'):>9}"
              f"{media('citacoes_invalidas'):>9}{media('fontes_fracas'):>8}{media('segundos'):>7}s")
        if any(l.get("nota_juiz") is not None for l in ok):
            diferenca = round(media("nota_revisor") - media("nota_juiz"), 1)
            print(f"\nRevisor − Juiz = {diferenca:+} pontos", end="  ")
            if diferenca >= 2:
                print("→ o revisor está bonzinho demais 😬")
            elif diferenca <= -2:
                print("→ o revisor está exigente demais")
            else:
                print("→ revisor e juiz concordam ✅")
        edge = [l for l in ok if l["edge_case_ok"] != ""]
        for l in edge:
            print(f"Pegadinha ({l['id']}): {'admitiu que não achou dados ✅' if l['edge_case_ok'] else 'INVENTOU uma resposta ❌'}")


def _salvar_csv(linhas: list[dict]) -> None:
    pasta = PASTA / "resultados"
    pasta.mkdir(exist_ok=True)
    arquivo = pasta / f"evals_{datetime.now():%Y%m%d_%H%M}.csv"
    colunas = sorted({c for l in linhas for c in l}, key=lambda c: list(linhas[0]).index(c) if c in linhas[0] else 99)
    with open(arquivo, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)
    print(f"\n💾 Resultados salvos em {arquivo.relative_to(PASTA.parent)}")


if __name__ == "__main__":
    main()
