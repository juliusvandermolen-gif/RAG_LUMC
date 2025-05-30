![Project Logo](./data/PNG/LUMC_logo.png)

## Table of Contents

1. [Introduction](#introduction)
2. [Features](#features)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Input and Output](#input-and-output)
6. [Running the Pipeline](#running-the-pipeline)
7. [Fine-Tuning the Model](#fine-tuning-the-model)
8. [Troubleshooting](#troubleshooting)
9. [Contact](#contact)

---

## Introduction

The **Modular RAG pipeline for LUMC** streamlines Retrieval-Augmented Generation (RAG) for biomedical literature queries. Leveraging embedding models, hybrid search (FAISS + BM25), and large language models, it delivers accurate, context-aware answers based on system instructions and user queries.

An overview of the workflow:

![Pipeline overview](./data/PNG/pipeline.drawio.png)

---

## Features

* **Query Expansion:** Generate synonyms and related terms via GPT-4o.
* **Indexing:** Chunk and index biomedical texts & gene data.
* **Hybrid Retrieval:** Rank documents using combined FAISS and BM25 scores.
* **Response Generation:** Produce answers with GPT-3.5/4 guided by system instructions.

---

## Installation

You can set up the environment via **Conda** or **pip**:

**Conda (recommended)**

```bash
conda env create -f environment.yaml
conda activate rag_lumc
```

**pip**

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\\Scripts\\activate
pip install -r requirements.txt
```

> For GPU support, see the [PyTorch guide](https://pytorch.org/get-started/locally/) and ensure CUDA is configured.

---

## Configuration

All settings live in **JSON** files under `./configs_system_instruction/`.

* **config\_template.json**: Base template
* **GSEA.json**: GSEA-specific example

Key parameters (as used in GSEA.json):

| Parameter                     | Description                                        |
| ----------------------------- | -------------------------------------------------- |
| `query`                       | User's question                                    |
| `number_of_expansions`        | How many query variations to generate              |
| `batch_size`                  | Documents processed per batch                      |
| `embeddings_model_name`       | Hugging Face repo path for embeddings              |
| `generation_model`            | LLM name for response generation (e.g. o4-mini)    |
| `validation_model`            | Model for validation or scoring (e.g. grok-3-mini) |
| `amount_docs`                 | Top-K docs to retrieve                             |
| `weight_faiss`, `weight_bm25` | Scoring weights for ranking                        |
| `max_genes`                   | Maximum genes for enrichment (e.g. \[250])         |
| `fdr_threshold`               | FDR cutoff for pathway significance                |
| `query_range`                 | Neighborhood depth for related terms               |
| `system_instruction_response` | LLM instruction template                           |

### Environment Variables

Only keys for the models you actually use are required. If you're calling multiple LLMs or embedding providers, set the ones you need. Hugging Face access is **highly recommended**, while others are optional.

| Variable                | Description                                   |
| ----------------------- | --------------------------------------------- |
| `HUGGINGFACE_API_TOKEN` | **Required** for loading HF models/embeddings |
| `OPENAI_API_KEY`        | Optional: for OpenAI models (GPT-4, GPT-3.5)  |
| `GROK_API_KEY`          | Optional: for Grok endpoints                  |
| `ANTHROPIC_API_KEY`     | Optional: for Anthropic Claude                |
| `GEMINI_API_KEY`        | Optional: for Gemini API access               |

Store them in a `.env` file at the repo root:

```bash
HUGGINGFACE_API_TOKEN=your_token
# only if used:
# OPENAI_API_KEY=...
# GROK_API_KEY=...
# ANTHROPIC_API_KEY=...
# GEMINI_API_KEY=...
```

---

## Input and Output

### Input Directories

* `./configs_system_instruction/` - JSON configs
* `./data/GSEA/external_gene_data/` - `.gmt.gz`, `.txt.gz`
* `./data/PDF/` - PDF docs

### Output Directories

* `./database/reference_chunks.db` - SQLite chunks
* `./database/faiss_index.bin` - FAISS index
* `./logs/` - pipeline logs and timing info
* `./output/text_file/` - `tanswer.txt`, `documents.txt`, `scores.xlsx`

---

## Running the Pipeline

The end-to-end pipeline is executed via **run\_all\_files.py**, which handles data processing, indexing, retrieval, and response generation.

* To run any configuration by its filename (without path or extension):

  ```bash
  python run_all_files.py --<config_name>
  ```

  For example, for the GSEA config:

  ```bash
  python run_all_files.py --GSEA
  ```

Alternatively, invoke individual steps:

* **RAG\_workflow\.py**: Core retrieval and generation logic

  ```bash
  python RAG_workflow.py --config configs_system_instruction/<config_name>.json
  ```
* **automated\_validation.py**: Validation routines
* **plotting.py**: Custom result plots
* **gprofiler.py**: Gene enrichment analysis

---

## Fine-Tuning the Model

To adapt the LLM to domain specifics, use the **fine\_tune.ipynb** notebook:

1. Launch Jupyter:

   ```bash
   jupyter lab fine_tune.ipynb
   ```
2. Prepare your training dataset and run the cells.
3. Save the new checkpoint and update your JSON config `generation_model` field to point to it.

---

## Troubleshooting

* **Dependency issues:** Re-run the install steps (Conda or pip).
* **API errors:** Check that your `.env` keys match the services you’re calling.
* **Memory errors:** Lower `batch_size` in your config.
* **Index load failures:** Verify `faiss_index.bin` exists and matches embedding dims.


---

## Contact

Mathieu Huibregtse • [mghuibregtse@gmail.com](mailto:mghuibregtse@gmail.com)
[LinkedIn](https://www.linkedin.com/in/mghuibregtse/) • [GitHub](https://github.com/mghuibregtse)
