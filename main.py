import sys

from dotenv import load_dotenv

load_dotenv(override=True)  # carrega o .env antes de importar o grafo (ele lê as chaves)

from langfuse.langchain import CallbackHandler  # noqa: E402

from agente.graph import construir_grafo  # noqa: E402


def main() -> None:
    if "--grafo" in sys.argv:  # mostra o desenho do grafo (cole em mermaid.live)
        print(construir_grafo().get_graph().draw_mermaid())
        return

    pergunta = " ".join(sys.argv[1:]) or "Como está o mercado de clínicas odontológicas em Curitiba?"

    print(f"🔎 Pergunta: {pergunta}\n(se a API estiver sobrecarregada, ele tenta de novo automaticamente)\n", flush=True)
    grafo = construir_grafo()
    langfuse = CallbackHandler()  # envia cada passo do grafo para o Langfuse

    resultado = grafo.invoke(
        {"pergunta": pergunta},
        config={"callbacks": [langfuse]},
    )

    rev = resultado.get("revisao", {})
    print("\n=== RESUMO ===")
    print(f"Revisões: {resultado.get('revisoes_feitas', 0)} | Fontes: {len(resultado.get('fontes', []))} "
          f"| Nota final: {rev.get('nota', '-')}/10 | Aprovado: {rev.get('aprovado')}")
    print("\n=== RELATÓRIO ===")
    print(resultado["resposta"])
    print("\n(Abra o Langfuse em Tracing para ver cada volta do loop, tokens e custo.)")


if __name__ == "__main__":
    main()
