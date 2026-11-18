import sys

from dotenv import load_dotenv

load_dotenv()  # carrega o .env antes de importar o grafo (ele lê as chaves)

from langfuse.langchain import CallbackHandler  # noqa: E402

from agente.graph import construir_grafo  # noqa: E402


def main() -> None:
    pergunta = " ".join(sys.argv[1:]) or "Como está o mercado de clínicas odontológicas em Curitiba?"

    grafo = construir_grafo()
    langfuse = CallbackHandler()  # envia cada passo do grafo para o Langfuse

    resultado = grafo.invoke(
        {"pergunta": pergunta},
        config={"callbacks": [langfuse]},
    )

    print("\n=== PLANO ===")
    for i, p in enumerate(resultado["plano"], 1):
        print(f"{i}. {p}")
    print("\n=== RELATÓRIO ===")
    print(resultado["resposta"])
    print("\n(Abra o Langfuse em Tracing para ver cada passo, tokens e custo.)")


if __name__ == "__main__":
    main()
