# Agente Pesquisador 🔎

Agente de pesquisa de mercado construído com **LangGraph**, **Gemini** e observabilidade com **Langfuse**.
Projeto de estudo em público: a cada etapa eu documento o que aprendi, o que quebrou e os números.

## Problema de negócio
Analisar um concorrente ou mercado leva horas de um analista: buscar, ler, cruzar e resumir.
O objetivo é um agente que entregue um relatório com fontes em minutos — e que **meça a própria qualidade**.

## Arquitetura (dia 1)
```
START → planejar → responder → END
```

## Roadmap
- [x] Dia 1 — grafo simples com estado + tracing no Langfuse
- [ ] Busca na web e leitura de páginas (tools)
- [ ] Nó de revisão com aresta condicional (loop de melhoria)
- [ ] Relatório com fontes citadas
- [ ] Evals: dataset de perguntas + LLM-as-judge
- [ ] API com FastAPI + Docker

## Como rodar
```bash
cp .env.example .env    # preencha as chaves
uv sync
uv run main.py "Como está o mercado de energia solar residencial no Brasil?"
```

## Aprendizados
_(atualizado a cada etapa)_
