import os
import json
import hashlib
import requests

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
import gzip
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm
import faiss
import numpy as np
from transformers import AutoModel, AutoTokenizer
import torch
import warnings
from rank_bm25 import BM25Okapi

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Suppress Warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*symlinks.*")
warnings.filterwarnings("ignore", category=FutureWarning,
                        message=".*resume_download.*")
warnings.filterwarnings("ignore", category=FutureWarning,
                        message=".*torch.load.*")


def compute_file_hash(file_path, block_size=65536):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            hasher.update(block)
    return hasher.hexdigest()


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


def save_faiss_index(index, index_path='faiss_index.bin'):
    faiss.write_index(index, index_path)


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


def insert_chunk(conn, file_name, chunk_index, text):
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chunks (file_name, chunk_index, text)
        VALUES (?, ?, ?)
    ''', (file_name, chunk_index, text))
    conn.commit()
    return cursor.lastrowid


def fetch_chunks(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    return cursor.fetchall()


def load_model_and_tokenizer(model_name="michiyasunaga/BioLinkBERT-large",
                             force_download=False):

    tokenizer = AutoTokenizer.from_pretrained(model_name,
                                              force_download=force_download)
    model = AutoModel.from_pretrained(model_name,
                                      force_download=force_download)
    return tokenizer, model


def load_gz_files(data_dir='./Data/biomart'):
    files = [
        f for f in os.listdir(data_dir)
        if f.endswith('.gz') or (f.startswith('converted') and f.endswith('.gmt'))
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
            print(f"Loaded '{file}' with {len(lines)} lines. Document length: {len(document)} characters.")
        else:
            print(f"Skipped empty file '{file}'.")

    return documents


# def chunk_documents(documents, chunk_size=5000, chunk_overlap=300):
#     print("Chunking documents...")
#     if not documents:
#         print("No documents to chunk. Returning an empty list.")
#         return []
#
#     text_splitter = CharacterTextSplitter(
#         separator="\n",
#         chunk_size=chunk_size,
#         chunk_overlap=chunk_overlap,
#         length_function=len
#     )
#
#     chunked_docs = []
#
#     for idx, document in enumerate(documents):
#         print(f"Processing Document {idx + 1} with length {len(document)} characters.")
#         chunks = text_splitter.split_text(document)
#         print(f"Document {idx + 1} split into {len(chunks)} chunks.")
#         chunked_docs.extend(chunks)
#
#     print(f"Total number of chunks: {len(chunked_docs)}")
#     return chunked_docs

def chunk_documents(documents):
    print("Chunking documents by line...")
    if not documents:
        print("No documents to chunk. Returning an empty list.")
        return []

    chunked_docs = []
    for idx, document in enumerate(documents):
        print(f"Processing Document {idx + 1} with length {len(document)} characters.")

        lines = document.split("\n")
        chunked_docs.extend(line for line in lines if line.strip())  # Add non-empty lines as chunks

    print(f"Total number of chunks: {len(chunked_docs)} (should match line count)")
    return chunked_docs




def embed_documents(conn, index, tokenizer, model, data_dir='./Data/biomart',
                    batch_size=64, log_path='./file_log/file_log.json'):
    file_log = load_file_log(log_path=log_path)
    files_documents = load_gz_files(data_dir=data_dir)
    print(f"Loaded {len(files_documents)} documents from '{data_dir}'.")

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    model.to(device)
    model.eval()

    for file, document, file_hash in files_documents:
        if file in file_log and file_log[file]['hash'] == file_hash:
            print(f"Skipping unchanged file: {file}")
            continue
        else:
            print(f"Processing file: {file}")

        lines = document.splitlines()
        if not lines:
            print(f"No lines found in file '{file}'. Skipping embedding.")
            continue

        data_lines = lines[1:] if file.endswith('txt.gz') else lines

        if not data_lines:
                print(f"No data lines found in file '{file}' after skipping header. Skipping embedding.")
                continue

        document_content = "\n".join(data_lines)
        print(f"Loaded '{file}' with {len(data_lines)} data lines after skipping header.")

        chunks = chunk_documents([document_content])
        if not chunks:
            print(f"No chunks created for file '{file}'. Skipping embedding.")
            continue

        print(f"Embedding chunks of '{file}' on device: {device}")
        embeddings = []

        with torch.no_grad():
            for i in tqdm(range(0, len(chunks), batch_size),
                          desc=f"Embedding chunks of '{file}'"):
                batch = chunks[i:i + batch_size]
                inputs = tokenizer(batch, return_tensors="pt",
                                   truncation=True, padding=True,
                                   max_length=512).to(device)
                outputs = model(**inputs)
                batch_embeddings = outputs.last_hidden_state.mean(dim=1).cpu().numpy()
                embeddings.extend(batch_embeddings)

        if not embeddings:
            print(f"No embeddings generated for file '{file}'.")
            continue

        embeddings_np = np.vstack(embeddings).astype('float32')
        print(f"Generated {embeddings_np.shape[0]} embeddings for file '{file}'.")

        chunk_ids = []
        for idx, chunk_text in enumerate(chunks):
            chunk_id = insert_chunk(conn, file, idx, chunk_text)
            chunk_ids.append(chunk_id)

        faiss_ids = np.array(chunk_ids).astype('int64')
        index.add_with_ids(embeddings_np, faiss_ids)
        print(f"Added {embeddings_np.shape[0]} embeddings to FAISS index. Total embeddings: {index.ntotal}")

        file_log[file] = {
            'hash': file_hash,
            'num_embeddings': embeddings_np.shape[0]
        }

    save_faiss_index(index, index_path='faiss_index.bin')
    save_file_log(file_log, log_path=log_path)


def query_faiss_index(query_text, index, tokenizer, model, top_k=20):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
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


def fetch_chunks_by_ids(conn, ids):
    cursor = conn.cursor()
    placeholder = ','.join(['?'] * len(ids))
    query = f"SELECT text FROM chunks WHERE id IN ({placeholder})"
    cursor.execute(query, ids)
    results = cursor.fetchall()
    return [row[0] for row in results]

def build_bm25_index(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    data = cursor.fetchall()
    chunk_ids = []
    chunk_texts = []
    for row in data:
        chunk_ids.append(row[0])
        chunk_texts.append(row[1])
    # Tokenize the documents
    tokenized_corpus = [doc.lower().split() for doc in chunk_texts]
    bm25 = BM25Okapi(tokenized_corpus)
    return bm25, chunk_ids, chunk_texts

def query_bm25_index(query_text, bm25, chunk_ids, chunk_texts, top_k=5):
    query_tokens = query_text.lower().split()
    scores = bm25.get_scores(query_tokens)
    top_n = np.argsort(scores)[::-1][:top_k]
    top_ids = [chunk_ids[i] for i in top_n]
    top_scores = [scores[i] for i in top_n]
    return top_ids, top_scores


def generate_gpt4_turbo_response_with_instructions(query_text,
                                                   document_references):
    system_instruction = """
    You are an efficient and insightful assistant to a molecular biologist.
    Write a critical analysis of the biological processes performed by this system of interacting proteins.
    Be concise and specific; avoid overly general statements. Be factual and avoid opinions.
    
    For the documents that are formatted like this:
    Example:
    Glutathione metabolism%WikiPathways_20240910%WP100%Homo sapiens https://www.wikipathways.org/instance/WP100
    [GGT5, GGT-REL, GGTLA1] [GGT1, CD224, D22S672, D22S732, GGT]    [GPX1]  [GSR]
    
    Contain the pathway with its wikipathways link. The genes are in list.
    For every list there is the gene with its synonym. If there are multiple, there are multiple synonyms: GGT5 has the
    synonyms GGT-REL and GGTLA1. GPX1 however, does not have any Synonyms.
    """

    combined_documents = "\n\n".join(document_references)
    prompt = f"Based on the following documents, answer the question: {query_text}\n\nDocuments:\n{combined_documents}\n"

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.2
        )
    except Exception as e:
        print(f"An error occurred while generating the GPT-4 response: {e}")
        return None

    print("Answer incoming")
    return response.choices[0].message.content


def save_answer_to_file(prompt, answer, document_references,
                        file_name="answer.txt"):

    with open(file_name, "w", encoding='utf-8') as f:
        f.write(f"Prompt:\n{prompt}\n\n")
        f.write(f"Answer:\n{answer}\n\n")
        f.write(f"Document References:\n")
        for idx, doc in enumerate(document_references, start=1):
            f.write(f"Reference {idx}:\n{doc}\n\n")
    print(f"Answer saved to {file_name}")
    with open("documents.txt", "w", encoding='utf-8') as f:
        for idx, doc in enumerate(document_references, start=1):
            f.write(f"Reference {idx}:\n{doc}\n\n")


def query_expansion(query_text, number):
    system_instruction = """
    You are an expert in query expansion for biomedical literature search.
    Given a user's query, generate a list of alternative queries that capture related concepts, synonyms, and relevant expansions.
    The number of alternative queries should be appropriate to cover the topic comprehensively but remain focused. 
    However, there is a maximum of {number} alternative queries that are given.

    Example:
    User query: "GGT5 in glutathione metabolism"

    Possible expanded queries:
    - "Gamma-glutamyltransferase 5 role in glutathione metabolism"
    - "GGT5 enzyme function in glutathione pathway"
    - "GGT5 and its involvement in glutathione homeostasis"
    - "Impact of GGT5 on glutathione biosynthesis and metabolism"
    """.format(number=number)

    prompt = f"Based on the user's query, provide expanded queries that include related terms, synonyms, and relevant expansions.\n\nUser query: \"{query_text}\""


    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7,

        )

        reply = response.choices[0].message.content
        expanded_queries = reply.strip().split('\n')
        expanded_queries = [q.lstrip("-•*").lstrip("0123456789. ").strip() for q in expanded_queries if q.strip()]

        return expanded_queries

    except Exception as e:
        print(f"An error occurred while expanding the query: {e}")
        return [query_text]




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


def save_gene_id_cache(cache, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)


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


def convert_gene_id_to_symbols(file, data_dir='./Data/JSON/'):
    cache_file = os.path.join(data_dir, 'ncbi_id_to_symbol.json')
    gene_cache = load_gene_id_cache(cache_file)

    output_lines = []
    unknown_genes = set()
    gene_id_map = {}

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


def main():
    data_dir = './Data/biomart'
    log_dir = './file_log'
    log_path = os.path.join(log_dir, 'file_log.json')
    index_path = 'faiss_index.bin'
    db_path = 'chunks_embeddings.db'
    cache_file = os.path.join(data_dir, 'ncbi_id_to_symbol.json')

    for file in os.listdir(data_dir):
        full_file_path = os.path.join(data_dir, file)
        if os.path.isfile(full_file_path) and file.endswith('.gmt') and file.startswith('wiki'):
            print(f"Processing file: {file}")
            converted_lines, unknown_genes = convert_gene_id_to_symbols(file, data_dir)
            print(f"Unknown genes saved to 'unknown_genes.txt'. Total unknown genes: {len(unknown_genes)}")

    conn = initialize_database(db_path=db_path)
    tokenizer, model = load_model_and_tokenizer()
    embedding_dim = model.config.hidden_size

    try:
        index = load_faiss_index(embedding_dim, index_path=index_path)
    except ValueError as ve:
        print(ve)
        print("Deleting existing FAISS index and creating a new one with IndexIDMap.")
        if os.path.exists(index_path):
            os.remove(index_path)
            print(f"Deleted FAISS index at {index_path}")
        index = load_faiss_index(embedding_dim, index_path=index_path)

    embed_documents(conn, index, tokenizer, model, data_dir=data_dir, batch_size=64, log_path=log_path)
    save_faiss_index(index, index_path=index_path)
    conn.close()

    index = load_faiss_index(embedding_dim, index_path=index_path)
    conn = sqlite3.connect(db_path)

    bm25_index, bm25_chunk_ids, bm25_chunk_texts = build_bm25_index(conn)

    query = "What are the genes that are involved in Pentose phosphate metabolism"
    number_of_expansions = 5
    expanded_queries = query_expansion(query, number=number_of_expansions)[1:]  # Exclude first line if it's an example
    print(f"Using Expanded Queries: {expanded_queries}")
    expanded_queries.append(query)

    top_k = 5

    faiss_doc_distance_dict = {}
    for eq in expanded_queries:
        top_ids, distances = query_faiss_index(eq, index, tokenizer, model, top_k=top_k)
        for doc_id, distance in zip(top_ids, distances):
            if doc_id in faiss_doc_distance_dict:
                if distance < faiss_doc_distance_dict[doc_id][0]:
                    faiss_doc_distance_dict[doc_id] = (distance, eq)
            else:
                faiss_doc_distance_dict[doc_id] = (distance, eq)

    sorted_faiss_docs = sorted(faiss_doc_distance_dict.items(), key=lambda item: item[1][0])
    top_faiss_docs = sorted_faiss_docs[:top_k]
    print(f"\nTop {top_k} documents with the shortest distances (FAISS):")
    for rank, (doc_id, (distance, source_query)) in enumerate(top_faiss_docs, start=1):
        print(f"{rank}. Document ID: {doc_id-1}, Distance: {distance}, Source Query: {source_query}")

    bm25_doc_scores = {}
    for eq in expanded_queries:
        top_ids_bm25, scores = query_bm25_index(eq, bm25_index, bm25_chunk_ids, bm25_chunk_texts, top_k=top_k)
        for doc_id, score in zip(top_ids_bm25, scores):
            if doc_id in bm25_doc_scores:
                if score > bm25_doc_scores[doc_id][0]:  # Higher score is better in BM25
                    bm25_doc_scores[doc_id] = (score, eq)
            else:
                bm25_doc_scores[doc_id] = (score, eq)

    sorted_bm25_docs = sorted(bm25_doc_scores.items(), key=lambda item: item[1][0], reverse=True)
    top_bm25_docs = sorted_bm25_docs[:top_k]
    print(f"\nTop {top_k} documents from BM25:")
    for rank, (doc_id, (score, source_query)) in enumerate(top_bm25_docs, start=1):
        print(f"{rank}. Document ID: {doc_id-1}, Score: {score}, Source Query: {source_query}")

    combined_docs = {}
    for rank, (doc_id, (distance, source_query)) in enumerate(top_faiss_docs, start=1):
        if doc_id not in combined_docs:
            combined_docs[doc_id] = {'retriever': ['FAISS'], 'rank': rank}
        else:
            combined_docs[doc_id]['retriever'].append('FAISS')

    for rank, (doc_id, (score, source_query)) in enumerate(top_bm25_docs, start=1):
        if doc_id not in combined_docs:
            combined_docs[doc_id] = {'retriever': ['BM25'], 'rank': rank}
        else:
            combined_docs[doc_id]['retriever'].append('BM25')

    combined_top_ids = list(combined_docs.keys())
    if combined_top_ids:
        retrieved_chunks = fetch_chunks_by_ids(conn, combined_top_ids)

        doc_id_to_chunk_unordered = dict(zip(sorted(combined_top_ids), retrieved_chunks))

        retrieved_chunks_ordered = [doc_id_to_chunk_unordered[doc_id] for doc_id in combined_top_ids]

        labeled_documents = []
        for idx, doc_id in enumerate(combined_top_ids, start=1):
            retrievers = combined_docs[doc_id]['retriever']
            retriever_label = ' and '.join(retrievers)
            chunk_text = retrieved_chunks_ordered[idx - 1]
            labeled_document = f"Reference {idx} ({retriever_label}):\n{chunk_text}"
            labeled_documents.append(labeled_document)



        print(f"Retrieved {len(labeled_documents)} unique chunks from the database.")
        response = generate_gpt4_turbo_response_with_instructions(query, labeled_documents)
        if response:
            print(f"GPT-4 Response:\n{response}")
            combined_documents = "\n\n".join(labeled_documents)
            prompt = f"Based on the following documents, answer the question: {query}\n\nDocuments:\n{combined_documents}\n"
            save_answer_to_file(prompt, response, labeled_documents)

        else:
            print("Failed to generate a response from GPT-4.")
    else:
        print("No documents retrieved for the expanded queries. Skipping GPT-4 response generation.")

    conn.close()

if __name__ == "__main__":
    main()
