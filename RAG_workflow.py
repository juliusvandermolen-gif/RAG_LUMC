import os
import json
import hashlib
import re
import pandas as pd
import requests
import gzip
import sqlite3
from tqdm import tqdm
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from transformers import AutoModel, AutoTokenizer
import faiss
import warnings
from rank_bm25 import BM25Okapi, BM25Plus
import time
import functools
import nltk
from nltk.corpus import stopwords
import string

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import torch

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Suppress Warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*symlinks.*")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*resume_download.*")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*torch.load.*")

with open('config.json', 'r', encoding='utf-8') as config_file:
    config = json.load(config_file)

query = config["query"]
number_of_expansions = config["number_of_expansions"]
batch_size = config["batch_size"]
model_name = config["model"]

try:
    amount_docs = query.split(":")[1].count(" ")
    raw_query_stop_words = query.split(":")[0].split(" ")
    query_stop_words = [word.strip(string.punctuation).lower() for word in raw_query_stop_words]

except (IndexError, AttributeError):
    amount_docs = 10
    query_stop_words = ['involved', 'genes']

weight_faiss = config["weight_faiss"]
weight_bm25 = config["weight_bm25"]
system_instruction_response = config["system_instruction_response"]

nltk.download('stopwords')
stop_words = set(stopwords.words('english')).union(query_stop_words)


def timer(func):
    @functools.wraps(func)
    def wrapper_timer(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time
        with open("time.txt", "a") as f:
            f.write(f"Function '{func.__name__}' executed in {elapsed_time:.4f} seconds\n")
        return result

    return wrapper_timer


@timer
def compute_file_hash(file_path, block_size=65536):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            hasher.update(block)
    return hasher.hexdigest()


@timer
def load_faiss_index(embedding_dim, index_path='faiss_index.bin'):
    if os.path.exists(index_path):
        index = faiss.read_index(index_path)
        if not isinstance(index, faiss.IndexIDMap):
            raise ValueError(
                f"Loaded FAISS index is of type {type(index)}, but IndexIDMap is required.")
    else:
        faiss_index = faiss.IndexFlatL2(embedding_dim)
        index = faiss.IndexIDMap(faiss_index)
        print("New FAISS IndexIDMap created.")
    return index


@timer
def save_faiss_index(index, index_path='faiss_index.bin'):
    faiss.write_index(index, index_path)


@timer
def load_file_log(log_path='./file_log/file_log.json'):
    log_dir = os.path.dirname(log_path)
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            print(f"Created log directory at '{log_dir}'.")
        except Exception as e:
            print(f"Failed to create log directory '{log_dir}': {e}")
            return {}

    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                file_log = json.load(f)
            print(f"File log loaded from '{log_path}'.")
        except json.JSONDecodeError:
            file_log = {}
            print(f"File log at '{log_path}' is corrupted or empty. "
                  f"Initialized with an empty log.")
            save_file_log(file_log, log_path)
        except Exception as e:
            print(f"An error occurred while loading the file log: {e}")
            file_log = {}
    else:
        file_log = {}
        print(
            f"No existing file log found. Creating a new file log at '{log_path}'.")
        save_file_log(file_log, log_path)

    return file_log


@timer
def save_file_log(file_log, log_path='./file_log/file_log.json'):
    log_dir = os.path.dirname(log_path)
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            print(f"Created log directory at '{log_dir}'.")
        except Exception as e:
            print(f"Failed to create log directory '{log_dir}': {e}")
            return

    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(file_log, f, indent=4)
        print(f"File log successfully saved to '{log_path}'.")
    except Exception as e:
        print(f"An error occurred while saving the file log: {e}")


@timer
def initialize_database(db_path='chunks_embeddings.db'):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            file_name TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL
        )
    ''')
    conn.commit()
    return conn


@timer
def insert_chunk(conn, file_name, chunk_index, text):
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chunks (file_name, chunk_index, text)
        VALUES (?, ?, ?)
    ''', (file_name, chunk_index, text))
    conn.commit()
    return cursor.lastrowid


@timer
def fetch_chunks(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    return cursor.fetchall()


@timer
def load_model_and_tokenizer(force_download=False):
    tokenizer = AutoTokenizer.from_pretrained(model_name,
                                              force_download=force_download)

    model = AutoModel.from_pretrained(model_name,
                                      force_download=force_download)
    return tokenizer, model


@timer
def load_gz_files(data_dir='./Data/biomart'):
    files = [
        f for f in os.listdir(data_dir)
        if
        f.endswith('.gz') or (f.startswith('converted') and f.endswith('.gmt'))
    ]

    documents = []
    print(f"Found {len(files)} files in '{data_dir}'.")

    for file in files:
        file_path = os.path.join(data_dir, file)
        file_hash = compute_file_hash(file_path)

        if file.endswith('.gz'):
            with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                content = f.read().strip()
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

        if content:
            lines = content.splitlines()
            document = "\n".join(lines)
            documents.append((file, document, file_hash))
            print(
                f"Loaded '{file}' with {len(lines)} lines. Document length: {len(document)} characters.")
        else:
            print(f"Skipped empty file '{file}'.")

    return documents


@timer
def chunk_documents(documents):
    if not documents:
        print("No documents to chunk. Returning an empty list.")
        return []

    chunked_docs = []
    for idx, document in enumerate(documents):
        print(
            f"Processing Document {idx + 1} with length {len(document)} characters.")

        lines = document.split("\n")
        chunked_docs.extend(line for line in lines if
                            line.strip())

    print(
        f"Total number of chunks: {len(chunked_docs)} (should match line count)")
    return chunked_docs


@timer
def embed_documents(conn, index, tokenizer, model, data_dir='./Data/biomart',
                    batch_size=batch_size, log_path='./file_log/file_log.json'):
    file_log = load_file_log(log_path=log_path)
    files_documents = load_gz_files(data_dir=data_dir)
    print(f"Loaded {len(files_documents)} documents from '{data_dir}'.")

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device)
    model.eval()
    for file, document, file_hash in files_documents:
        if file in file_log and file_log[file]['hash'] == file_hash:
            continue
        else:
            print(f"Processing file: {file}")

        lines = document.splitlines()
        if not lines:
            print(f"No lines found in file '{file}'. Skipping embedding.")
            continue

        data_lines = lines[1:] if file.endswith('txt.gz') else lines

        document_content = "\n".join(data_lines)

        chunks = chunk_documents([document_content])
        if not chunks:
            print(f"No chunks created for file '{file}'. Skipping embedding.")
            continue

        print(f"Embedding chunks of '{file}' on device: {device}")
        embeddings = []

        with torch.no_grad():
            for i in tqdm(range(0, len(chunks), batch_size),
                          desc="Processing chunks in batches"):
                batch = chunks[i:i + batch_size]
                inputs = tokenizer(batch, return_tensors="pt", truncation=True,
                                   padding=True, max_length=512).to(device)
                outputs = model(**inputs)
                attention_mask = inputs['attention_mask']
                token_embeddings = outputs.last_hidden_state
                input_mask_expanded = attention_mask.unsqueeze(-1).expand(
                    token_embeddings.size()).float()
                sum_embeddings = torch.sum(
                    token_embeddings * input_mask_expanded, dim=1)
                sum_mask = input_mask_expanded.sum(dim=1)
                batch_embeddings = (sum_embeddings / sum_mask).cpu().numpy()
                embeddings.extend(batch_embeddings)

        if not embeddings:
            print(f"No embeddings generated for file '{file}'.")
            continue

        embeddings_np = np.vstack(embeddings).astype('float32')

        chunk_ids = []
        for idx, chunk_text in enumerate(chunks):
            chunk_id = insert_chunk(conn, file, idx, chunk_text)
            chunk_ids.append(chunk_id)

        faiss_ids = np.array(chunk_ids).astype('int64')
        index.add_with_ids(embeddings_np, faiss_ids)

        file_log[file] = {
            'hash': file_hash,
            'num_embeddings': embeddings_np.shape[0]
        }

    save_faiss_index(index, index_path='faiss_index.bin')
    save_file_log(file_log, log_path=log_path)


@timer
def query_faiss_index(query_text, index, tokenizer, model, top_k=20,
                      force_download=False):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    loaded_model = AutoModel.from_pretrained(model_name,
                                             force_download=force_download)
    loaded_tokenizer = AutoTokenizer.from_pretrained(model_name,
                                                     force_download=force_download)

    if model.config != loaded_model.config:
        print("The models have different configurations.")
        model = AutoModel.from_pretrained(model_name,
                                          force_download=force_download)

    if str(tokenizer) != str(loaded_tokenizer):
        print("The tokenizers have different configurations.")
        tokenizer = AutoTokenizer.from_pretrained(model_name,
                                                  force_download=force_download)

    model.to(device)
    model.eval()
    with torch.no_grad():
        inputs = tokenizer([query_text], return_tensors="pt", truncation=True,
                           padding=True, max_length=512).to(device)
        outputs = model(**inputs)
        query_embedding = outputs.last_hidden_state.mean(dim=1).cpu().numpy()
    distances, indices = index.search(query_embedding, top_k)
    top_ids = [int(id_) for id_ in indices[0] if id_ != -1]
    top_distances = [float(dist) for dist in distances[0] if dist != -1]
    return top_ids, top_distances


@timer
def fetch_chunks_by_ids(conn, ids):
    cursor = conn.cursor()
    placeholder = ','.join(['?'] * len(ids))
    query = f"SELECT text FROM chunks WHERE id IN ({placeholder})"
    cursor.execute(query, ids)
    results = cursor.fetchall()
    return [row[0] for row in results]


def tokenize(text):
    return [word for word in re.findall(r'\b[\w-]+\b', text.lower()) if word not in stop_words]


@timer
def build_bm25_index(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    data = cursor.fetchall()
    chunk_ids = []
    chunk_texts = []
    for row in data:
        chunk_ids.append(row[0])
        chunk_texts.append(row[1])
    tokenized_corpus = [tokenize(doc) for doc in chunk_texts]  # original: re.findall(r'\b\w+\b', doc.lower())
    bm25 = BM25Plus(tokenized_corpus)
    #bm25 = BM25Okapi(tokenized_corpus)
    return bm25, chunk_ids, chunk_texts


@timer
def query_bm25_index(query_text, bm25, chunk_ids, chunk_texts, top_k=1000):
    query_tokens = tokenize(query_text)
    print("Query tokens:", query_tokens)

    # print("\nTokens for each line in the database:")
    # for chunk_id, chunk_text in zip(chunk_ids, chunk_texts):
    #     line_tokens = re.findall(r'\b[\w-]+\b', chunk_text.lower())
    #     print(f"Chunk ID {chunk_id}: {line_tokens}")  # Check how everything is chunked
    scores = bm25.get_scores(query_tokens)
    top_n = np.argsort(scores)[::-1][:top_k]
    top_ids = [chunk_ids[i] for i in top_n]
    top_scores = [scores[i] for i in top_n]

    detailed_scores = {}
    for idx, score in zip(top_n, top_scores):
        chunk_id = chunk_ids[idx]
        relevant_tokens = []
        for token in query_tokens:
            if token in bm25.idf:
                tf = bm25.doc_freqs[idx].get(token, 0)
                if tf > 0:
                    idf = bm25.idf[token]
                    k1 = bm25.k1
                    b = bm25.b
                    doc_len = bm25.doc_len[idx]
                    avgdl = bm25.avgdl
                    numerator = (k1 + 1) * tf
                    denominator = k1 * ((1 - b) + b * (doc_len / avgdl)) + tf
                    token_score = idf * (numerator / denominator)
                    relevant_tokens.append(f"{token} with score {token_score:.4f}")
            else:
                pass
        detailed_scores[chunk_id] = {
            'score': round(score, 4),
            'tokens': relevant_tokens if relevant_tokens else None
        }

    print("\nIDF values for query tokens:")
    # for token in query_tokens:
    #     if token in bm25.idf:
    #         print(f"Token: {token}, IDF: {bm25.idf[token]:.4f}")
    #     else:
    #         pass

    return top_ids, top_scores, detailed_scores


import os
from openai import OpenAI


def generate_gpt4_turbo_response_with_instructions(query_text, document_references):
    system_instruction = system_instruction_response  # Ensure this variable is defined elsewhere
    testing = True
    combined_documents = "\n\n".join(document_references)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = "gpt-4o"  # Verify if "gpt-4o" is the correct model name

    if testing:
        test_list = [True, False]
        range_limit = 1
    else:
        test_list = [True]
        range_limit = 1

    for test_status in test_list:
        for i in range(1, range_limit + 1):
            if test_status:
                prompt = (
                    f"Based on the following documents, answer the question using both your knowledge and the "
                    f"provided documents: {query_text}\n\nDocuments:\n"
                    f"{combined_documents}\n"
                )
            else:
                prompt = f"Answer the question based on your knowledge: {query_text}"

            if model in ["o1-preview", "o1-mini"]:
                adjusted_prompt = system_instruction + prompt
                messages = [
                    {"role": "user", "content": adjusted_prompt}
                ]
            else:
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ]

            # Print the exact message being sent to the LLM
            # print("=== Sending the following message to the LLM ===")
            # for message in messages:
            #     print(f"Role: {message['role']}\nContent: {message['content']}\n")
            # print("=== End of message ===\n")

            try:
                if model in ["o1-preview", "o1-mini"]:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_completion_tokens=5000
                    )
                else:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=5000,
                        temperature=0  # 0 - 2
                    )
            except Exception as e:
                # It's better to print the exception instead of response here
                print(f"An error occurred while generating the GPT-4 response: {e}")
                continue

            # Ensure that the response has the expected structure
            try:
                answer = response.choices[0].message.content
            except (AttributeError, IndexError) as e:
                print(f"Unexpected response structure: {response}")
                print(f"Error: {e}")
                continue

            test_label = "With_ref" if test_status else "Without_ref"
            filename = f"test_files/answer_{test_label}_{i}.txt"

            with open(filename, "w", encoding="utf-8") as file:
                file.write(answer)

            print(f"Answer saved to {filename}")

    # Optionally, return all responses or handle accordingly
    return answer  # Returns the last answer generated


@timer
def save_answer_to_file(prompt, answer, document_references,
                        file_name="answer.txt"):
    with open(file_name, "w", encoding='utf-8') as f:
        #f.write(f"Prompt:\n{prompt}\n\n")
        f.write(f"Answer:\n{answer}\n\n")
        # for idx, doc in enumerate(document_references, start=1):
        #     f.write(f"Reference {idx}:\n{doc}\n\n")
    print(f"Answer saved to {file_name}")
    with open("documents.txt", "w", encoding='utf-8') as f:
        for idx, doc in enumerate(document_references, start=1):
            f.write(f"Reference {idx}:\n{doc} \n\n")


@timer
def query_expansion(query_text, number):
    system_instruction = """You are an expert in query expansion for biomedical literature search. Given a user's 
query, generate a list of alternative queries that capture related concepts, synonyms, and relevant expansions. 
The number of alternative queries should be appropriate to cover the topic comprehensively but remain focused. 
However, there is a maximum of {number} alternative queries that are given.

Example:
User query: "GGT5 in glutathione metabolism"

Possible expanded queries:
- "Gamma-glutamyltransferase 5 role in glutathione metabolism"
- "GGT5 enzyme function in glutathione pathway"
- "GGT5 and its involvement in glutathione homeostasis"
- "Impact of GGT5 on glutathione biosynthesis and metabolism"

Also, consider the context of the user query and ensure that the expanded queries are relevant to the topic.
    """.format(number=number)
    print("The amount of queries that are going to be expanded:{number}".format(number=number))
    if number > 0:
        prompt = (f"Based on the user's query, provide expanded queries that include related terms, synonyms, "
                  f"and relevant"
                  f"expansions.\n\nUser query: \"{query_text}\"")

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7,

            )

            reply = response.choices[0].message.content
            expanded_queries = reply.strip().split('\n')
            expanded_queries = [q.lstrip("-•*").lstrip("0123456789. ").strip() for
                                q in expanded_queries if q.strip()]

            return expanded_queries

        except Exception as e:
            print(f"An error occurred while expanding the query: {e}")
            return [query_text]
    else:
        return [query_text]


@timer
def load_gene_id_cache(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON file: {e}")
                return {}
    else:
        return {}


@timer
def save_gene_id_cache(cache, file_path):
    print(f"\n\n\nSaving cache to {file_path}\n\n")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)


@timer
def search_genes(unknown_genes, gene_cache, cache_file):
    url = "https://mygene.info/v3/gene/"
    resolved_genes = {}

    for gene_id in unknown_genes:
        print(f"Searching for gene symbol for {gene_id}...")
        try:
            response = requests.get(url + gene_id)
            response.raise_for_status()
            data = response.json()

            if 'symbol' in data:
                gene_symbol = data['symbol']
                resolved_genes[gene_id] = gene_symbol
                print(f"Found gene symbol for {gene_id}: {gene_symbol}")
            else:
                resolved_genes[gene_id] = "Unknown"
        except requests.exceptions.RequestException as e:
            resolved_genes[gene_id] = "Error"

    gene_cache.update(resolved_genes)
    save_gene_id_cache(gene_cache, cache_file)

    return resolved_genes


@timer
def convert_gene_id_to_symbols(file, data_dir, ncbi_json_dir):
    cache_file = os.path.join(ncbi_json_dir, 'ncbi_id_to_symbol.json')
    gene_cache = load_gene_id_cache(cache_file)
    output_lines = []
    unknown_genes = set()
    gene_id_map = {}

    # If the file is compressed, decompress it
    if file.endswith('.gz'):
        decompressed_file = file[:-3]  # Remove '.gz' extension
        with gzip.open(os.path.join(data_dir, file), 'rt') as gzfile:
            with open(os.path.join(data_dir, decompressed_file), 'w') as outfile:
                for line in gzfile:
                    outfile.write(line)
        file = decompressed_file  # Update file to point to the decompressed version

    # Process the file (decompressed or not)
    with open(os.path.join(data_dir, file), 'r') as infile:
        for line_index, line in enumerate(infile):
            parts = line.strip().split('\t')
            pathway_info = parts[:2]
            gene_ids = parts[2:]
            gene_symbols = []

            for i, gene_id in enumerate(gene_ids):
                if gene_id in gene_cache:
                    gene_symbols.append(gene_cache[gene_id])
                else:
                    gene_symbols.append("Unknown")
                    unknown_genes.add(gene_id)

                    if line_index not in gene_id_map:
                        gene_id_map[line_index] = []
                    gene_id_map[line_index].append((i, gene_id))

            new_line = '\t'.join(pathway_info + gene_symbols)
            output_lines.append(new_line)
            print(unknown_genes)

    if unknown_genes:
        resolved_genes = search_genes(unknown_genes, gene_cache, cache_file)

        for line_index, unknown_gene_positions in gene_id_map.items():
            line_parts = output_lines[line_index].split('\t')
            pathway_info = line_parts[:2]
            gene_symbols = line_parts[2:]

            for position, gene_id in unknown_gene_positions:
                if gene_id in resolved_genes:
                    gene_symbols[position] = resolved_genes[gene_id]

            output_lines[line_index] = '\t'.join(pathway_info + gene_symbols)

        final_unknown_genes = {gene_id for gene_id, symbol in
                               resolved_genes.items() if symbol == "Unknown"}
    else:
        final_unknown_genes = set()

    output_file = os.path.join(data_dir, f"converted_{file}")
    with open(output_file, 'w') as outfile:
        outfile.write('\n'.join(output_lines))

    if final_unknown_genes:
        unknown_genes_file = os.path.join(data_dir, 'unknown_genes.txt')
        with open(unknown_genes_file, 'w') as unknown_file:
            unknown_file.write('\n'.join(final_unknown_genes))

        print(f"These genes are still unknown: {final_unknown_genes}")

    return output_lines, list(final_unknown_genes)


@timer
def create_top_faiss_docs(expanded_queries, index, tokenizer, model, top_k=20):
    faiss_doc_distance_dict = {}
    for eq in expanded_queries:
        top_ids, distances = query_faiss_index(eq, index, tokenizer, model,
                                               top_k=top_k)
        for doc_id, distance in zip(top_ids, distances):
            if doc_id in faiss_doc_distance_dict:
                if distance < faiss_doc_distance_dict[doc_id][0]:
                    faiss_doc_distance_dict[doc_id] = (distance, eq)
            else:
                faiss_doc_distance_dict[doc_id] = (distance, eq)

    sorted_faiss_docs = sorted(faiss_doc_distance_dict.items(),
                               key=lambda item: item[1][0])
    top_faiss_docs = sorted_faiss_docs[:top_k]
    return top_faiss_docs


@timer
def create_top_bm25_docs(expanded_queries, bm25, chunk_ids, chunk_texts, top_k=20):
    """
    Aggregate BM25 scores from expanded queries and return the top_k documents.

    Args:
        expanded_queries (list): List of expanded query strings.
        bm25 (BM25+): BM25 index object.
        chunk_ids (list): List of document chunk IDs.
        chunk_texts (list): List of document chunk texts.
        top_k (int): Number of top documents to return.

    Returns:
        list of tuples: Each tuple contains (doc_id, details), where details is a dictionary
                        with 'score', 'tokens', and 'source_queries'.
    """
    bm25_doc_scores = {}

    for eq in expanded_queries:
        top_ids, top_scores, detailed_scores = query_bm25_index(eq, bm25, chunk_ids, chunk_texts, top_k=top_k)
        for doc_id, details in detailed_scores.items():
            if doc_id in bm25_doc_scores:
                if details['score'] > bm25_doc_scores[doc_id]['score']:
                    bm25_doc_scores[doc_id]['score'] = details['score']
                    bm25_doc_scores[doc_id]['tokens'] = details['tokens']
                bm25_doc_scores[doc_id]['source_queries'].append(eq)
            else:
                bm25_doc_scores[doc_id] = {
                    'score': details['score'],
                    'tokens': details['tokens'],
                    'source_queries': [eq]
                }

    sorted_bm25_docs = sorted(bm25_doc_scores.items(),
                              key=lambda item: item[1]['score'],
                              reverse=True)
    top_bm25_docs = sorted_bm25_docs[:top_k]

    return top_bm25_docs


@timer
def weighted_rrf(top_bm25_docs, top_faiss_docs, weight_faiss, weight_bm25):
    """
    Combine BM25 and FAISS scores using weights instead of RRF.
    """
    total_weight = weight_faiss + weight_bm25
    weight_faiss /= total_weight
    weight_bm25 /= total_weight
    combined_scores = {}

    for bm25_doc in top_bm25_docs:
        doc_id, details = bm25_doc
        score = details["score"] * weight_bm25
        if doc_id in combined_scores:
            combined_scores[doc_id]["score"] += score
        else:
            combined_scores[doc_id] = {"score": score, "source_queries": details.get("source_queries", [])}

    for faiss_doc in top_faiss_docs:
        doc_id, (distance, _) = faiss_doc
        score = (1 / (1 + distance)) * weight_faiss
        if doc_id in combined_scores:
            combined_scores[doc_id]["score"] += score
        else:
            combined_scores[doc_id] = {"score": score, "source_queries": []}

    return combined_scores


@timer
def process_files_in_directory(data_dir,ncbi_json_dir):
    """Process all .gmt files in the given directory that start with 'wiki'."""
    for file in os.listdir(data_dir):
        print(file)
        full_file_path = os.path.join(data_dir, file)
        if os.path.isfile(full_file_path) and file.endswith(
                '.gmt.gz'):  # and file.startswith('wiki'):
            print(f"Processing file: {file}")

            converted_lines, unknown_genes = convert_gene_id_to_symbols(file,
                                                                        data_dir, ncbi_json_dir)
            print(
                f"Unknown genes saved to 'unknown_genes.txt'. Total unknown genes: {len(unknown_genes)}")


@timer
def initialize_faiss_index(embedding_dim, index_path):
    """Load or recreate a FAISS index."""
    try:
        return load_faiss_index(embedding_dim, index_path=index_path)
    except ValueError:
        print(
            "Deleting existing FAISS index and creating a new one with IndexIDMap.")
        if os.path.exists(index_path):
            os.remove(index_path)
            print(f"Deleted FAISS index at {index_path}")
        return load_faiss_index(embedding_dim, index_path=index_path)


@timer
def embed_documents_and_save(index, conn, tokenizer, model, data_dir,
                             batch_size, log_path, index_path):
    """Embed documents, save to FAISS index, and close connection."""
    embed_documents(conn, index, tokenizer, model, data_dir=data_dir,
                    batch_size=batch_size, log_path=log_path)
    save_faiss_index(index, index_path=index_path)
    conn.close()


@timer
def run_query_expansion_and_retrieval(query, index, tokenizer, model, top_k,
                                      bm25, bm25_chunk_ids, bm25_chunk_texts):
    """Expand query and retrieve top documents using FAISS and BM25."""
    expanded_queries = query_expansion(query, number=number_of_expansions)[1:]
    expanded_queries.append(query)

    for idx, eq in enumerate(expanded_queries, start=1):
        print(f"Expanded Query {idx}: {eq}")

    top_faiss_docs = create_top_faiss_docs(expanded_queries, index, tokenizer,
                                           model, top_k)
    top_bm25_docs = create_top_bm25_docs(expanded_queries, bm25,
                                         bm25_chunk_ids, bm25_chunk_texts,
                                         top_k)

    bm25_detailed_scores = top_bm25_docs

    return top_faiss_docs, top_bm25_docs, bm25_detailed_scores


@timer
def rank_and_retrieve_documents(rrf_scores, conn, top_faiss_docs, top_bm25_docs, amount_docs):
    """Rank documents and fetch top chunks from the database."""
    sorted_rrf_scores = sorted(rrf_scores.items(),
                               key=lambda item: item[1]['score'],
                               reverse=True)[:amount_docs]

    combined_docs = {}
    for rank, (doc_id, data) in enumerate(sorted_rrf_scores, start=1):
        score = data['score']
        source_queries = data.get('source_queries', [])

        if doc_id not in combined_docs:
            combined_docs[doc_id] = {'retriever': [], 'rank': rank, 'score': score, 'source_queries': source_queries}

        if any(faiss_doc[0] == doc_id for faiss_doc in top_faiss_docs):
            combined_docs[doc_id]['retriever'].append('FAISS')
        if any(bm25_doc[0] == doc_id for bm25_doc in top_bm25_docs):
            combined_docs[doc_id]['retriever'].append('BM25')

    combined_top_ids = list(combined_docs.keys())
    retrieved_chunks = fetch_chunks_by_ids(conn, combined_top_ids) if combined_top_ids else []
    doc_id_to_chunk = dict(zip(sorted(combined_top_ids), retrieved_chunks))

    return [doc_id_to_chunk[doc_id] for doc_id in combined_top_ids], combined_docs


@timer
def generate_response_and_save(query, labeled_documents, conn):
    """Generate GPT-4 response and save the result."""
    print(
        f"Retrieved {len(labeled_documents)} unique chunks from the database.")
    response = generate_gpt4_turbo_response_with_instructions(query,
                                                              labeled_documents)

    if response:
        combined_documents = "\n\n".join(labeled_documents)
        prompt = f"Based on the following documents, answer the question: {query}\n\nDocuments:\n{combined_documents}\n"
        save_answer_to_file(prompt, response, labeled_documents)
    else:
        print("Failed to generate a response from GPT-4.")
    conn.close()


@timer
def export_scores_to_excel(rrf_scores, bm25_scores, faiss_scores, file_name="scores.xlsx"):
    """
    Export combined RRF, BM25, and FAISS scores to an Excel file.

    Args:
        rrf_scores (dict): Combined RRF scores with doc_id as keys.
        bm25_scores (dict): BM25 scores with doc_id as keys.
        faiss_scores (dict): FAISS scores with doc_id as keys.
        file_name (str): Name of the Excel file to create.
    """
    all_doc_ids = set(rrf_scores.keys()).union(bm25_scores.keys()).union(faiss_scores.keys())

    data = []
    for doc_id in all_doc_ids:
        bm25_detail = bm25_scores.get(doc_id, {})
        bm25_score = bm25_detail.get('score', 0) if isinstance(bm25_detail, dict) else bm25_detail
        faiss_score = faiss_scores.get(doc_id, 0)
        rrf_score = rrf_scores.get(doc_id, 0) if isinstance(rrf_scores.get(doc_id, 0),
                                                            (int, float)) else rrf_scores.get(doc_id, {}).get('score',
                                                                                                              0)

        data.append({
            "doc_id": doc_id,
            "BM25 Score": bm25_score,
            "FAISS Distance": faiss_score,
            "RRF Score": rrf_score
        })

    df = pd.DataFrame(data)
    df = df.sort_values(by="RRF Score", ascending=False)
    df.to_excel(file_name, index=False)
    print(f"Scores saved to {file_name}")


@timer
def save_scores_to_file(scores, file_name):
    """
    Save scores to a text file, including tokens that contributed to the score.

    Args:
        scores (dict): Dictionary where each key is a doc_id and each value is either:
                       - For BM25: a dictionary with 'score', 'tokens', and optionally 'source_queries'.
                       - For FAISS: a simple score (e.g., distance).
        file_name (str): Name of the file to save the scores.
    """
    with open(file_name, "w", encoding='utf-8') as f:
        for doc_id, details in scores.items():
            if isinstance(details, dict):
                overall_score = details.get('score', 0)
                tokens = details.get('tokens', [])
                f.write(f"Chunk ID: {doc_id}, Score: {overall_score}\n")
                if tokens:
                    # Format tokens and their scores
                    tokens_formatted = "\n".join(tokens)
                    f.write(f"Tokens that were relevant:\n{tokens_formatted}\n")
                else:
                    f.write("Tokens that were relevant: None\n")
                f.write("\n")
            else:
                f.write(f"Chunk ID: {doc_id}, Score: {details}\n\n")
    print(f"Scores saved to {file_name}")


@timer
def main():
    #TODO remove all the entries with len < 3 as they dont seem to contain anything except ensg id
    data_dir = './Data/biomart'
    log_dir = './file_log'
    log_path = os.path.join(log_dir, 'file_log.json')
    index_path = 'faiss_index.bin'
    db_path = 'chunks_embeddings.db'
    ncbi_json_dir='./Data/JSON/'
    process_files_in_directory(data_dir, ncbi_json_dir)

    conn = initialize_database(db_path=db_path)
    tokenizer, model = load_model_and_tokenizer()
    embedding_dim = model.config.hidden_size

    index = initialize_faiss_index(embedding_dim, index_path)

    embed_documents_and_save(index, conn, tokenizer, model, data_dir,
                             batch_size=batch_size, log_path=log_path,
                             index_path=index_path)

    index = load_faiss_index(embedding_dim, index_path=index_path)
    conn = sqlite3.connect(db_path)

    bm25_index, bm25_chunk_ids, bm25_chunk_texts = build_bm25_index(conn)
    print(f"Embedding dimensions: {embedding_dim} {index.d} {model.config.hidden_size}")

    top_faiss_docs, top_bm25_docs, bm25_detailed_scores = run_query_expansion_and_retrieval(
        query, index, tokenizer, model, top_k=100000, bm25=bm25_index,
        bm25_chunk_ids=bm25_chunk_ids, bm25_chunk_texts=bm25_chunk_texts)

    rrf_scores = weighted_rrf(top_bm25_docs, top_faiss_docs, weight_faiss, weight_bm25)
    bm25_scores = dict(bm25_detailed_scores)
    faiss_scores = {doc_id: distance for doc_id, (distance, eq) in top_faiss_docs}

    save_scores_to_file(bm25_scores, "bm25_score.txt")
    save_scores_to_file(faiss_scores, "faiss_score.txt")

    export_scores_to_excel(rrf_scores, bm25_scores, faiss_scores, file_name="scores.xlsx")

    retrieved_chunks_ordered, combined_docs = rank_and_retrieve_documents(
        rrf_scores, conn, top_faiss_docs, top_bm25_docs, amount_docs=amount_docs)

    labeled_documents = [
        (
            f"Reference {combined_docs[doc_id]['rank']} ({' and '.join(combined_docs[doc_id]['retriever'])}) with RRF Score:"
            f" {combined_docs[doc_id]['score']:.4f}:\n{chunk_text}"
        )
        for doc_id, chunk_text in
        zip(combined_docs.keys(), retrieved_chunks_ordered)
    ]

    generate_response_and_save(query, labeled_documents, conn)


if __name__ == "__main__":
    main()
