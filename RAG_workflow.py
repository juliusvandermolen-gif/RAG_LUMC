# Standard library imports
import os
import sys
import time
import re
import json
import gzip
import csv
import hashlib
import sqlite3
import argparse
import warnings
import atexit
import functools
import logging
import contextlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Third-party imports
import pandas as pd
import numpy as np
import requests
from tqdm import tqdm
from dotenv import load_dotenv

import nltk
from nltk import sent_tokenize
from nltk.corpus import stopwords
from nltk.data import find

from PyPDF2 import PdfReader
from transformers import AutoModel, AutoTokenizer

import faiss
from rank_bm25 import BM25Okapi, BM25Plus

from openai import OpenAI
import google.generativeai as genai
from anthropic import Anthropic
import torch
from openai import OpenAI as DeepSeekClient

# Environment & API clients setup
load_dotenv()

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["OMP_NUM_THREADS"] = "8"

client_open_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
client_claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
client_deepseek = DeepSeekClient(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
client_grok = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key=os.getenv("XAI_API_KEY"),
)

# NLTK: silent downloader
logging.getLogger('nltk').setLevel(logging.ERROR)


def ensure_nltk_resource(pkg_name: str, resource_path: str):
    """
    Check for an NLTK resource; if missing, download quietly
    with no stdout/stderr noise.
    """
    try:
        find(resource_path)
    except LookupError:
        with contextlib.redirect_stdout(open(os.devnull, 'w')), \
             contextlib.redirect_stderr(open(os.devnull, 'w')):
            nltk.download(pkg_name, quiet=True)


ensure_nltk_resource('punkt',
                     'tokenizers/punkt')
ensure_nltk_resource('stopwords', 'corpora/stopwords')


class ConfigError(Exception):
    pass


def load_config(path: str) -> Dict[str, Any]:
    """
    Load JSON config from `path`, enforce required keys,
    fill in defaults, print each key (excluding secrets) with its source,
    then return the merged dict.
    """
    default_cfg: Dict[str, Any] = {
        "number_of_expansions":    5,
        "batch_size":              64,
        "embeddings_model_name":   "mghuibregtse/biolinkbert-large-simcse-rat",
        "generation_model":        "o4-mini",
        "amount_docs":             50,
        "weight_faiss":            50,
        "weight_bm25":             50,
        "max_genes":               [250],
        "fdr_threshold":           0.05,
        "query_range": 1,
    }

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")

    def escape_newlines(m: re.Match) -> str:
        inner = m.group(0)[1:-1].replace("\n", "\\n")
        return f"\"{inner}\""
    processed = re.sub(r'"(?:\\.|[^"\\])*"', escape_newlines, raw, flags=re.DOTALL)

    try:
        user_cfg = json.loads(processed)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path!r}: {e}")

    # Required keys
    for key in ("system_instruction_response", "query"):
        if key not in user_cfg:
            raise ConfigError(f"Missing required config key: {key}")

    merged = default_cfg.copy()
    merged.update(user_cfg)

    missing_opt = set(default_cfg) - set(user_cfg)
    if missing_opt:
        logging.warning("Using default for: %s", ", ".join(sorted(missing_opt)))

    print(f"Using config: {Path(path).name} with these parameters:")
    for k, v in merged.items():
        if k in ("query", "system_instruction_response"):
            continue
        src = "user" if k in user_cfg else "default"
        print(f"{k}: {v} (from {src} config)")

    return merged


# BM25 stop-words setup
stop_words = set(stopwords.words('english'))
stop_words |= {
    "list", "genes", "identify", "mechanisms", "pathways",
    "related", "based", "following", "recent", "study"
}


os.makedirs("./logs", exist_ok=True)


def compute_file_hash(file_path, block_size=65536):
    """
    Computes the SHA-256 has of a file's content.

    Args:
        file_path: The path to the file to hash.
        block_size: The size (in bytes) of each block to hash.

    Returns:
        A hexadecimal string representing the SHA-256 hash of the file.

    """
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as hash_file:
        for block in iter(lambda: hash_file.read(block_size), b''):
            hasher.update(block)
    return hasher.hexdigest()


def initialize_gene_list(max_genes, fdr_threshold, excel_file_path=r".\data\GSEA\genes_of_interest\PMP22_VS_WT.xlsx",
                         de_filter_option="combined"):
    """
    Creates a list of genes from the specified Excel file based on filtering criteria.

    Args:
        excel_file_path: The path to the Excel file.
        de_filter_option: Specifies whether to combine or separate up- and down-regulated genes.
        max_genes: The maximum number of genes to process.
        fdr_threshold: The threshold for the false discovery rate (FDR).

    Returns:
        A tuple containing:
            - gene_list: A comma-separated string of gene names.
            - regulation: A string indicating the regulation type ('upregulated', 'downregulated', or 'combined').
            - num_genes: The number of genes processed.
    """
    results = process_excel_data(excel_file_path, de_filter_option, max_genes, fdr_threshold)
    if results:
        gene_list_string, regulation, num_genes = results[0]
    else:
        gene_list_string = ""
        regulation = ""
        num_genes = 0
    return gene_list_string, regulation, num_genes


def process_excel_data(
    path: str,
    mode: str,
    max_genes: int,
    fdr: float
) -> List[Tuple[str, str, int]]:
    """
    Processes an Excel file to filter and extract gene data based on differential expression and FDR threshold.

    Args:

        path: The file path to the Excel file containing gene data.
        mode: The differential expression filter option ('combined' or 'separate').
        max_genes: number of genes to process.
        fdr: False discovery rate threshold for filtering.

    Returns:
        A list of tuples. Each tuple contains:
            - A comma-separated string of gene names.
            - A regulation label ('upregulated', 'downregulated', or 'combined').
            - The number of genes included in that subset.
    """
    df = pd.read_excel(path).head(max_genes)
    out: List[Tuple[str, str, int]] = []

    def collect(sub_df: pd.DataFrame, regulation_label: str):
        genes = sub_df['X'].tolist()
        out.append((', '.join(genes), regulation_label, len(genes)))

    if mode == "combined":
        df = df[df.DE != 0]
        if not df.empty and df.FDR.max() <= fdr:
            unique = set(df.DE)
            regulation = "upregulated" if unique == {1} else "downregulated" if unique == {-1} else "combined"
            collect(df, regulation)

    elif mode == "separate":
        for val, label in ((1, "upregulated"), (-1, "downregulated")):
            sub = df[df.DE == val]
            if not sub.empty and sub.FDR.max() <= fdr:
                collect(sub, label)

    else:
        raise ValueError(f"Unknown mode {mode!r}; expected 'combined' or 'separate'")

    return out


def load_gene_id_cache(file_path):
    """
    Loads the gene ID cache from a JSON file.

    Args:
        file_path: The path to the JSON file containing the gene ID cache.

    Returns:
        A dictionary with the gene ID cache, or an empty dictionary if the file does not exist or cannot be decoded.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as gene_id_file:
            try:
                return json.load(gene_id_file)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON file: {e}")
                return {}
    else:
        return {}


def save_gene_id_cache(cache, file_path):
    """
    Saves the gene ID cache to a JSON file.

    Args:
        cache: The gene ID cache dictionary to save.
        file_path: The path to the file where the cache should be saved.
    """
    print(f"\n\n\nSaving cache to {file_path}\n\n")
    with open(file_path, 'w', encoding='utf-8') as gene_id_file:
        json.dump(cache, gene_id_file, indent=4)


def search_genes(unknown_genes, gene_cache, cache_file):
    """
    Searches for gene symbols for a set of unknown genes using the MyGene.info API.
    Updates the gene cache with resolved symbols and saves the updated cache.

    Args:
        unknown_genes: A collection of gene IDs that need symbol resolution.
        gene_cache: A dictionary containing already resolved gene IDs.
        cache_file: The path to the cache file.

    Returns:
        A dictionary mapping the original gene IDs to their resolved gene symbols.
    """
    url = "https://mygene.info/v3/gene/"
    resolved_genes = {}

    for gene_id in unknown_genes:
        sanitized_gene_id = gene_id.strip("[]")
        print(f"Searching for gene symbol for {sanitized_gene_id}...")
        try:
            response = requests.get(url + sanitized_gene_id)
            response.raise_for_status()
            data = response.json()

            if 'symbol' in data:
                gene_symbol = data['symbol']
                resolved_genes[sanitized_gene_id] = gene_symbol
                print(f"Found gene symbol for {sanitized_gene_id}: {gene_symbol}")
            else:
                resolved_genes[sanitized_gene_id] = "Unknown"
        except requests.exceptions.RequestException as e:
            resolved_genes[sanitized_gene_id] = "Error"
            print(f"Error with gene ID {sanitized_gene_id}: {e}")

    gene_cache.update(resolved_genes)
    save_gene_id_cache(gene_cache, cache_file)

    return resolved_genes


def convert_gene_id_to_symbols(file, data_dir, ncbi_json_dir):
    """
    Converts gene IDs in a file to gene symbols using a cached mapping and by searching unknown genes via an API.
    If the file is compressed, it is decompressed before processing.

    Args:
        file: The filename to process.
        data_dir: The directory containing the file.
        ncbi_json_dir: The directory where the gene ID cache is stored.

    Returns:
        A tuple containing:
            - A list of processed lines with gene symbols.
            - A list of gene IDs that remain unknown.
    """
    cache_file = os.path.join(ncbi_json_dir, 'ncbi_id_to_symbol.json')
    gene_cache = load_gene_id_cache(cache_file)
    output_lines = []
    unknown_genes = set()
    sanitized_gene_id_map = {}

    if file.endswith('.gz'):
        decompressed_file = file[:-3]  # Remove '.gz' extension
        with gzip.open(os.path.join(data_dir, file), 'rt') as gzfile:
            with open(os.path.join(data_dir, decompressed_file), 'w') as outfile:
                for line in gzfile:
                    outfile.write(line)
        file = decompressed_file

    with open(os.path.join(data_dir, file), 'r') as infile:
        for line_index, line in enumerate(infile):
            parts = line.strip().split('\t')
            pathway_info = parts[:2]
            gene_ids = parts[2:]
            gene_symbols = []

            for i, gene_id in enumerate(gene_ids):
                sanitized_gene_id = gene_id.strip("[]")
                if sanitized_gene_id in gene_cache:
                    gene_symbols.append(gene_cache[sanitized_gene_id])
                else:
                    if sanitized_gene_id.isdigit():  # Makes sure that they aren't already converted
                        gene_symbols.append("Unknown")
                        unknown_genes.add(sanitized_gene_id)

                        if line_index not in sanitized_gene_id_map:
                            sanitized_gene_id_map[line_index] = []
                        sanitized_gene_id_map[line_index].append((i, sanitized_gene_id))

            new_line = '\t'.join(pathway_info + gene_symbols)
            output_lines.append(new_line)

    if unknown_genes:
        print(f"There are unknown genes: {unknown_genes}")
        resolved_genes = search_genes(unknown_genes, gene_cache, cache_file)

        for line_index, unknown_gene_positions in sanitized_gene_id_map.items():
            line_parts = output_lines[line_index].split('\t')
            pathway_info = line_parts[:2]
            gene_symbols = line_parts[2:]

            for position, sanitized_gene_id in unknown_gene_positions:
                if sanitized_gene_id in resolved_genes:
                    gene_symbols[position] = resolved_genes[sanitized_gene_id]

            output_lines[line_index] = '\t'.join(pathway_info + gene_symbols)

        final_unknown_genes = {sanitized_gene_id for sanitized_gene_id, symbol in
                               resolved_genes.items() if symbol == "Unknown"}
    else:
        final_unknown_genes = set()

    if final_unknown_genes:
        unknown_genes_file = os.path.join('./output/results/unknown_genes.txt')
        with open(unknown_genes_file, 'w') as unknown_file:
            unknown_file.write('\n'.join(final_unknown_genes))

        print(f"These genes are still unknown: {final_unknown_genes}")

    return output_lines, list(final_unknown_genes)


# Database Functions
def initialize_database(db_path='./database/reference_chunks.db'):
    """
    Initializes the SQLite database and creates the 'chunks' table if it does not exist.

    Args:
        db_path: The path to the SQLite database file.

    Returns:
        A connection object to the SQLite database.
    """
    if not os.path.exists('./database'):
        os.makedirs('./database')
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
    """
    Inserts a text chunk into the database.

    Args:
        conn: The SQLite database connection.
        file_name: The name of the file from which the chunk was extracted.
        chunk_index: The index of the chunk within the file.
        text: The text content of the chunk.

    Returns:
        The row ID of the inserted chunk.
    """
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chunks (file_name, chunk_index, text)
        VALUES (?, ?, ?)
    ''', (file_name, chunk_index, text))
    conn.commit()
    return cursor.lastrowid


def fetch_chunks(conn):
    """
    Retrieves all chunks from the database.

    Args:
        conn: The SQLite database connection.

    Returns:
        A list of tuples containing the chunk ID and text.
    """
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    return cursor.fetchall()


def fetch_chunks_by_ids(conn, ids):
    """
    Retrieves chunks from the database that match the specified IDs.

    Args:
        conn: The SQLite database connection.
        ids: A list of chunk IDs to fetch.

    Returns:
        A list of text contents corresponding to the provided chunk IDs.
    """

    if not ids:
        return {}
    placeholder = ','.join('?' for _ in ids)
    query = f"SELECT id, text FROM chunks WHERE id IN ({placeholder})"
    cursor = conn.cursor()
    cursor.execute(query, ids)
    rows = cursor.fetchall()
    return {row[0]: row[1] for row in rows}


# FAISS Functions
def save_faiss_index(index, index_path='./database/faiss_index.bin'):
    """
    Saves the FAISS index to a file.

    Args:
        index: The FAISS index to save.
        index_path: The file path where the FAISS index will be saved.
    """
    faiss.write_index(index, index_path)


def load_faiss_index(embedding_dim, index_path='./database/faiss_index.bin'):
    """
    Loads a FAISS index from a file. If the file does not exist, creates a new IndexIDMap based on an IndexFlatIP.

    Args:
        embedding_dim: The dimension of the embeddings.
        index_path: The file path from which to load the FAISS index.

    Returns:
        A FAISS index with ID mapping.
    """
    if os.path.exists(index_path):
        index = faiss.read_index(index_path)
        if not isinstance(index, faiss.IndexIDMap):
            raise ValueError(
                f"Loaded FAISS index is of type {type(index)}, but IndexIDMap is required.")
    else:
        faiss_index = faiss.IndexFlatIP(embedding_dim)
        index = faiss.IndexIDMap(faiss_index)
        print("New FAISS IndexIDMap created.")
    return index


def initialize_faiss_index(embedding_dim, index_path):
    """
    Load or recreate a FAISS index using the specified embedding dimension and index path.
    If the existing index is invalid, it is deleted and a new one is created.

    Args:
        embedding_dim: The dimension of the embeddings.
        index_path: The file path for the FAISS index.

    Returns:
        A valid FAISS index.
    """
    try:
        return load_faiss_index(embedding_dim, index_path=index_path)
    except ValueError:
        print(
            "Deleting existing FAISS index and creating a new one with IndexIDMap.")
        if os.path.exists(index_path):
            os.remove(index_path)
            print(f"Deleted FAISS index at {index_path}")
        return load_faiss_index(embedding_dim, index_path=index_path)


# Chunking Functions
def chunk_documents(documents):
    """
    Splits documents into chunks by breaking them into non-empty lines.

    Args:
        documents: A list of document strings.

    Returns:
        A list of text chunks derived from the input documents.
    """
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


def chunk_pdfs(single_document, gene_list=None, target_length=1000):
    """
    Splits the text content of a PDF document into chunks based on sentence tokenization and target length.
    Optionally filters chunks based on the presence of any gene from a provided gene list.

    Args:
        single_document: A tuple containing the file name, content, and file hash.
        gene_list: An optional list of gene names to filter chunks.
        target_length: The target minimum length of each chunk in characters.

    Returns:
        A list of text chunks extracted from the document.
    """
    file_name, content, file_hash = single_document[0]
    sentences = sent_tokenize(content)
    if not sentences:
        return []

    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        current_chunk.append(sentence)
        current_length += len(sentence)

        if current_length >= target_length:
            chunk_text = " ".join(current_chunk)
            if not gene_list or any(gene in chunk_text for gene in gene_list):
                chunks.append(chunk_text)
            current_chunk = []
            current_length = 0

    if current_chunk:
        chunk_text = " ".join(current_chunk)
        if not gene_list or any(gene in chunk_text for gene in gene_list):
            chunks.append(chunk_text)

    return chunks


# Embedding & Loading Functions
def load_file_log(log_path='./logs/file_log.json'):
    """
    Loads a file log from a JSON file, creating the log directory and file if necessary.

    Args:
        log_path: The path to the JSON file that stores the file log.

    Returns:
        A dictionary representing the file log.
    """
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
            with open(log_path, 'r', encoding='utf-8') as file_logs:
                file_log = json.load(file_logs)
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


def save_file_log(file_log, log_path='./logs/file_log.json'):
    """
    Saves the file log to a JSON file, ensuring the log directory exists.

    Args:
        file_log: The file log dictionary to save.
        log_path: The path to the JSON file where the log will be saved.
    """
    log_dir = os.path.dirname(log_path)
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            print(f"Created log directory at '{log_dir}'.")
        except Exception as e:
            print(f"Failed to create log directory '{log_dir}': {e}")
            return

    try:
        with open(log_path, 'w', encoding='utf-8') as file_logs:
            json.dump(file_log, file_logs, indent=4)
    except Exception as e:
        print(f"An error occurred while saving the file log: {e}")


def load_embeddings_model_and_tokenizer(force_download=False):
    """
    Loads and returns a tokenizer and model from pretrained sources using the Transformers library.

    Args:
        force_download: If True, forces re-downloading of the model and tokenizer.

    Returns:
        A tuple containing the tokenizer and model.
    """
    tokenizer = AutoTokenizer.from_pretrained(embeddings_model_name, force_download=force_download)
    embeddings_model = AutoModel.from_pretrained(embeddings_model_name, force_download=force_download)
    return tokenizer, embeddings_model


def load_gz_files(data_dir='./data/GSEA/external_gene_data'):
    """
    Loads documents from files in the specified directory that are either gzipped or in .gmt format.
    Computes a file hash for each document and returns a list of documents with their metadata.

    Args:
        data_dir: The directory containing the gene data files.

    Returns:
        A list of tuples, each containing the file name, document content, and file hash.
    """
    files = [
        gz_files for gz_files in os.listdir(data_dir)
        if
        gz_files.endswith('.gz') or (gz_files.startswith('converted') and gz_files.endswith('.gmt'))
    ]

    documents = []
    if len(files) > 0:
        print(f"Found {len(files)} files in '{data_dir}'.")

    for file in files:
        file_path = os.path.join(data_dir, file)
        file_hash = compute_file_hash(file_path)

        if file.endswith('.gz'):
            with gzip.open(file_path, 'rt', encoding='utf-8') as gz_files:
                content = gz_files.read().strip()
        else:
            with open(file_path, 'r', encoding='utf-8') as gz_files:
                content = gz_files.read().strip()

        if content:
            lines = content.splitlines()
            document = "\n".join(lines)
            documents.append((file, document, file_hash))
            print(
                f"Loaded '{file}' with {len(lines)} lines. Document length: {len(document)} characters.")
        else:
            print(f"Skipped empty file '{file}'.")

    return documents


def load_pdf_files(pdf_dir='./data/PDF', file_log=None):
    """
    Loads PDF documents from the specified directory, skipping those already recorded in the file log.

    Args:
        pdf_dir: The directory containing PDF files.
        file_log: An optional dictionary of previously processed files.

    Returns:
        A list of tuples, each containing the PDF file name, extracted content, and file hash.
    """
    pdf_documents = []
    if not os.path.exists(pdf_dir):
        print(f"No PDF directory found at '{pdf_dir}'. Skipping PDF import.")
        return pdf_documents

    file_log = file_log or {}

    for file in os.listdir(pdf_dir):
        if file.lower().endswith('.pdf'):
            if file in file_log:
                continue

            file_path = os.path.join(pdf_dir, file)
            try:
                reader = PdfReader(file_path)
                pages_text = []
                for page in reader.pages:
                    pages_text.append(page.extract_text() or "")
                content = "\n".join(pages_text).strip()
                if content:
                    file_hash = compute_file_hash(file_path)
                    pdf_documents.append((file, content, file_hash))
                    print(
                        f"Loaded PDF '{file}' with {len(pages_text)} pages. Document length: {len(content)}"
                        f" characters.")
                else:
                    print(f"Skipped empty PDF '{file}'.")
            except Exception as e:
                print(f"Failed to read PDF '{file}': {e}")

    return pdf_documents


def embed_documents(conn, index, tokenizer, embeddings_model, data_dir,
                    batch_size, log_path='./logs/file_log.json',
                    pdf_dir='./data/PDF'):
    """
    Embeds text from documents (both gzipped and PDF files) using a transformer model,
    updates the FAISS index with the embeddings, and records file metadata in a log.

    Args:
        conn: The SQLite database connection.
        index: The FAISS index to update.
        tokenizer: The tokenizer for preparing text inputs.
        embeddings_model: The transformer model for generating embeddings.
        data_dir: Directory containing gzipped documents.
        batch_size: The batch size for embedding computation.
        log_path: The path to the file log.
        pdf_dir: Directory containing PDF files.
    """
    file_log = load_file_log(log_path=log_path)

    files_documents = load_gz_files(data_dir=data_dir)
    pdf_documents = load_pdf_files(pdf_dir=pdf_dir, file_log=file_log)
    if len(files_documents) > 0:
        print(f"Loaded {len(files_documents)} documents from '{data_dir}'.")
        print(f"Loaded {len(pdf_documents)} new PDF documents from '{pdf_dir}'.")

    all_documents = files_documents + pdf_documents

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if device.type != 'cuda':
        print(f"No GPU available. Using {device} for embedding.")
    embeddings_model.to(device)
    embeddings_model.eval()

    for file, document, file_hash in all_documents:
        if file in file_log and file_log[file]['hash'] == file_hash:
            continue
        else:
            print(f"Processing file: {file}")

        lines = document.splitlines()
        if not lines:
            print(f"No lines found in file '{file}'. Skipping embedding.")
            continue

        if file.lower().endswith('.pdf'):
            chunks = chunk_pdfs([(file, document, file_hash)], gene_list=None)
        else:
            document_content = "\n".join(lines[1:]) if file.endswith('txt.gz') else "\n".join(lines)
            chunks = chunk_documents([document_content])

        if not chunks:
            print(f"No chunks created for file '{file}'. Skipping embedding.")
            continue

        print(f"Embedding chunks of '{file}' on device: {device}")
        embeddings = []

        with torch.no_grad():
            for i in tqdm(range(0, len(chunks), batch_size),
                          desc=f"Processing {file} in batches"):
                batch = chunks[i:i + batch_size]
                inputs = tokenizer(batch, return_tensors="pt", truncation=True,
                                   padding=True, max_length=512).to(device)
                outputs = embeddings_model(**inputs)
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
        faiss.normalize_L2(embeddings_np)

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

    save_faiss_index(index, index_path='./database/faiss_index.bin')
    save_file_log(file_log, log_path=log_path)


# BM25 and Query Functions
def tokenize(text):
    """
    Tokenizes the input text into lowercase words, excluding common stopwords.

    Args:
        text: The text string to tokenize.

    Returns:
        A list of tokens.
    """
    return [word for word in re.findall(r'\b[\w-]+\b', text.lower()) if word not in stop_words]


def build_bm25_index(conn):
    """
    Builds a BM25 index for the text chunks stored in the database.

    Args:
        conn: The SQLite database connection.

    Returns:
        A tuple containing the BM25 index object, a list of chunk IDs, and a list of chunk texts.
    """
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM chunks')
    data = cursor.fetchall()
    chunk_ids = []
    chunk_texts = []
    for row in data:
        chunk_ids.append(row[0])
        chunk_texts.append(row[1])
    tokenized_corpus = [tokenize(doc) for doc in chunk_texts]
    bm25 = BM25Okapi(tokenized_corpus)
    return bm25, chunk_ids, chunk_texts


def query_bm25_index(
        query: str,
        index: BM25Okapi,
        chunk_ids: List[int],
        top_k: int = 1_000
) -> Tuple[List[int], List[float], Dict[int, Any]]:
    """
        Queries the BM25 index using the provided query text and returns the top scoring documents along with details.

        Args:
            query: The text query.
            index: The BM25 index object.
            chunk_ids: List of chunk IDs corresponding to the BM25 index.
            top_k: The number of top results to return.

        Returns:
            A tuple containing:
                - A list of top chunk IDs.
                - A list of their corresponding scores.
                - A dictionary with detailed token contributions for each chunk.
        """
    tokens = tokenize(query)
    scores = index.get_scores(tokens)
    order = np.argsort(scores)[::-1][:top_k]

    top_ids = [chunk_ids[i] for i in order]
    top_scores = [float(scores[i]) for i in order]

    details: Dict[int, Any] = {}
    for i, s in zip(order, top_scores):
        cid = chunk_ids[i]
        tok_details = []
        for t in tokens:
            tf = index.doc_freqs[i].get(t, 0)
            if tf:
                idf, b, avgdl, k1 = index.idf[t], index.b, index.avgdl, index.k1
                numerator = (k1+1)*tf
                denominator = k1*(1-b + b*(index.doc_len[i]/avgdl)) + tf
                tok_details.append(f"{t} score={idf*(numerator/denominator):.4f}")
        details[cid] = {"score": round(s, 4), "tokens": tok_details or None}

    return top_ids, top_scores, details


def query_faiss_index(query_text, index, tokenizer, embeddings_model, top_k, force_download=False):
    """
    Computes the embedding for a query and searches the FAISS index to retrieve the closest document chunks.

    Args:
        query_text: The query string.
        index: The FAISS index.
        tokenizer: The tokenizer for preparing the query.
        embeddings_model: The transformer model for generating the query embedding.
        top_k: The number of top results to retrieve.
        force_download: If True, forces re-downloading of model/tokenizer if needed.

    Returns:
        A tuple containing the list of top document IDs and their corresponding distances.
    """
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if device.type != 'cuda':
        print(f"No GPU available. Using {device} for embedding.")
    loaded_model = AutoModel.from_pretrained(embeddings_model_name, force_download=force_download)
    loaded_tokenizer = AutoTokenizer.from_pretrained(embeddings_model_name, force_download=force_download)
    if embeddings_model.config != loaded_model.config:
        print("The models have different configurations.")
        embeddings_model = AutoModel.from_pretrained(embeddings_model_name, force_download=force_download)
    if str(tokenizer) != str(loaded_tokenizer):
        print("The tokenizers have different configurations.")
        tokenizer = AutoTokenizer.from_pretrained(embeddings_model_name, force_download=force_download)
    embeddings_model.to(device)
    embeddings_model.eval()
    with torch.no_grad():
        inputs = tokenizer([query_text], return_tensors="pt", truncation=True,
                           padding=True, max_length=512).to(device)
        outputs = embeddings_model(**inputs)
        query_embedding = outputs.last_hidden_state.mean(dim=1).cpu().numpy()
        faiss.normalize_L2(query_embedding)
    distances, indices = index.search(query_embedding, top_k)
    top_ids = [int(id_) for id_ in indices[0] if id_ != -1]
    top_distances = [float(dist) for dist in distances[0] if dist != -1]
    return top_ids, top_distances


# Document Ranking and Combination
def create_top_faiss_docs(expanded_queries, index, tokenizer, embeddings_model, top_k):
    """
    Retrieves top documents from the FAISS index for each expanded query and computes average distances.

    Args:
        expanded_queries: A list of expanded query strings.
        index: The FAISS index.
        tokenizer: The tokenizer for processing the queries.
        embeddings_model: The transformer model for generating embeddings.
        top_k: The number of top documents to retrieve per query.

    Returns:
        A list of tuples containing document IDs and their average distances.
    """
    doc_distances = {}

    for eq in expanded_queries:
        top_ids, distances = query_faiss_index(eq, index, tokenizer, embeddings_model, top_k=top_k)
        for doc_id, distance in zip(top_ids, distances):
            if doc_id not in doc_distances:
                doc_distances[doc_id] = []
            doc_distances[doc_id].append(distance)

    faiss_doc_distance_dict = {}
    for doc_id, dist_list in doc_distances.items():
        avg_dist = sum(dist_list) / len(dist_list)
        faiss_doc_distance_dict[doc_id] = (avg_dist, "avg")

    sorted_faiss_docs = sorted(faiss_doc_distance_dict.items(), key=lambda item: item[1][0])
    top_faiss_docs = sorted_faiss_docs[:top_k]

    return top_faiss_docs


def create_top_bm25_docs(expanded_queries, bm25_index, chunk_ids, top_k):
    """
    Retrieves top documents from the BM25 index for each expanded query and aggregates their scores.

    Args:
        expanded_queries: A list of expanded query strings.
        bm25_index: The BM25 index object.
        chunk_ids: List of chunk IDs corresponding to the BM25 index.
        top_k: The number of top documents to retrieve per query.

    Returns:
        A list of tuples where each tuple contains a document ID and its aggregated BM25 score and details.
    """
    bm25_doc_scores = {}

    for eq in expanded_queries:
        top_ids, top_scores, detailed_scores = query_bm25_index(eq, bm25_index, chunk_ids, top_k=top_k)
        for doc_id, details in detailed_scores.items():
            if doc_id not in bm25_doc_scores:
                bm25_doc_scores[doc_id] = {
                    'scores': [],
                    'tokens': "",
                    'best_tokens_score': float('-inf'),
                    'source_queries': []
                }

            bm25_doc_scores[doc_id]['scores'].append(details['score'])

            bm25_doc_scores[doc_id]['source_queries'].append(eq)

            if details['score'] > bm25_doc_scores[doc_id]['best_tokens_score']:
                bm25_doc_scores[doc_id]['best_tokens_score'] = details['score']
                bm25_doc_scores[doc_id]['tokens'] = (
                    ', '.join(details['tokens']) if isinstance(details['tokens'], list) else details['tokens']
                )

    for doc_id, info in bm25_doc_scores.items():
        avg_score = sum(info['scores']) / len(info['scores'])
        info['score'] = avg_score

    sorted_bm25_docs = sorted(bm25_doc_scores.items(),
                              key=lambda item: item[1]['score'],
                              reverse=True)
    top_bm25_docs = sorted_bm25_docs[:top_k]

    return top_bm25_docs


def weighted_rrf(top_bm25_docs, top_faiss_docs, weight_faiss, weight_bm25):
    """
    Combine BM25 and FAISS scores using specified weights.

    Args:
        top_bm25_docs: List of top documents from BM25.
        top_faiss_docs: List of top documents from FAISS.
        weight_faiss: Weight for FAISS scores.
        weight_bm25: Weight for BM25 scores.

    Returns:
        A dictionary mapping document IDs to their combined weighted scores.
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


def rank_and_retrieve_documents(rrf_scores, conn, top_faiss_docs, top_bm25_docs, amount_docs):
    """
    Ranks documents based on combined RRF scores, retrieves the corresponding text chunks from the database,
    and returns them in ranked order.

    Args:
        rrf_scores: A dictionary of combined scores for documents.
        conn: The SQLite database connection.
        top_faiss_docs: List of top documents from FAISS.
        top_bm25_docs: List of top documents from BM25.
        amount_docs: The number of documents to retrieve.

    Returns:
        A tuple containing:
            - A list of ordered document chunks.
            - A dictionary with combined document details and their rankings.
    """
    sorted_rrf_scores = sorted(rrf_scores.items(),
                               key=lambda item: item[1]['score'],
                               reverse=True)[:amount_docs]

    unique_ids = {doc_id for doc_id, _ in sorted_rrf_scores}

    combined_docs = {}
    for rank, (doc_id, data) in enumerate(sorted_rrf_scores, start=1):
        score = data['score']
        source_queries = data.get('source_queries', [])
        combined_docs[doc_id] = {
            'retriever': [],
            'rank': rank,
            'score': score,
            'source_queries': source_queries
        }
        if any(faiss_doc[0] == doc_id for faiss_doc in top_faiss_docs):
            combined_docs[doc_id]['retriever'].append('FAISS')
        if any(bm25_doc[0] == doc_id for bm25_doc in top_bm25_docs):
            combined_docs[doc_id]['retriever'].append('BM25')

    # build the ID→text map in one go
    doc_id_to_chunk = fetch_chunks_by_ids(conn, list(unique_ids))

    # then simply iterate in rank order
    ordered_chunks = [
        doc_id_to_chunk[doc_id]
        for doc_id, _ in sorted_rrf_scores
        if doc_id in doc_id_to_chunk
    ]

    return ordered_chunks, combined_docs


# Query Expansion and Response Generation
def query_expansion(query_text, number):
    """
    Expands a given query into a specified number of alternative queries by generating synonyms and related phrases.

    Args:
        query_text: The original user query.
        number: The number of expanded queries to generate.

    Returns:
        A list of expanded query strings.
    """
    system_instruction = f"""You are an expert in query expansion for biomedical literature searches. Given a user's 
query, generate exactly {number} alternative queries that capture related concepts, synonyms, and relevant expansions. 
Each alternative query should be concise and directly related to the original query.

Example of a query expansion:
User query: "GGT5 in glutathione metabolism"

Possible expanded queries:
1. "Gamma-glutamyltransferase 5 role in glutathione metabolism"
2. "GGT5 enzyme function in glutathione pathway"
3. "GGT5 and its involvement in glutathione homeostasis"
4. "Impact of GGT5 on glutathione biosynthesis and metabolism"

Also, consider the context of the user query and ensure that the expanded queries are relevant to the topic.
    """
    expanded_queries = []
    if number > 0:
        prompt = (
            f"Based on the user's query, provide exactly {number} expanded queries that include related terms,"
            f" synonyms, and relevant expansions.\n\nUser query: \"{query_text}\"")

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ]
        save = False
        answer = query_llm(messages, prompt, system_instruction, save, generation_model=generation_model,
                           query_range=query_range)
        expanded_queries = answer.strip().split('\n')
        expanded_queries = [q.lstrip("-•*").lstrip("0123456789. ").strip() for q in expanded_queries if q.strip()]

        if len(expanded_queries) > number:
            expanded_queries = expanded_queries[:number]
        elif len(expanded_queries) < number:
            print(f"Warning: Expected {number} queries but received {len(expanded_queries)}.")

    return expanded_queries


def build_search_parameters(
    mode: str = "on",
    from_date: str = None,
    to_date: str = None,
    max_results: int = 20,
    academic: bool = False,
    academic_rss_links: list[str] = None,
) -> dict:
    sources = [
        {"type": "web"},
        {"type": "x"},
        {"type": "news"},
    ]
    if academic:
        default_arxiv = "https://export.arxiv.org/rss/cs"
        rss_links = academic_rss_links or [default_arxiv]
        for link in rss_links:
            sources.append({"type": "rss", "links": [link]})

    params: dict = {
        "mode": mode,
        "return_citations": True,
        "max_search_results": max_results,
        "sources": sources,
    }
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date

    return params


def get_generation_model_dir(model: str) -> str:
    if model.startswith("grok"):
        return "grok"
    if model == "gpt-4o-mini-search-preview" or model.startswith("gpt-4"):
        return "openai"
    if model.startswith("o"):
        if "o4-mini" in model:
            return "open_ai_o4_mini"
        if "o3-mini" in model:
            return "open_ai_o3_mini"
        if "o4" in model:
            return "open_ai_o4"
        return "open_ai_o3"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith("claude"):
        return "claude"
    if model.lower().startswith("gemini"):
        return "gemini"
    return "openai"


def query_llm(
    messages: list[dict],
    system_instruction_for_response: str,
    prompt: str,
    save: bool,
    generation_model: str,
    query_range: int,
    **kwargs
) -> str:
    """
    Unified LLM query function supporting Grok, OpenAI, DeepSeek, Claude, and Gemini.
    Retries `query_range` times, times each call, and optionally saves outputs to files.
    """

    answers: list[str] = []
    for i in range(1, query_range + 1):
        try:
            start_time = time.perf_counter()
            model_dir = get_generation_model_dir(generation_model)

            # ── Grok via raw HTTP ───────────────────────────────────────────────
            if model_dir == "grok":
                api_key = os.getenv("XAI_API_KEY")
                endpoint = "https://api.x.ai/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                # Grok-specific search params
                academic = kwargs.pop('academic', False)
                academic_rss = kwargs.pop('academic_rss_links', None)
                from_date = kwargs.pop('from_date', None)
                to_date = kwargs.pop('to_date', None)
                max_results = kwargs.pop('max_results', 20)
                mode = kwargs.pop('mode', 'on')
                temperature_grok = kwargs.pop('temperature', 0)

                payload = {
                    "model": generation_model,
                    "messages": messages,
                    "search_parameters": build_search_parameters(
                        mode=mode,
                        from_date=from_date,
                        to_date=to_date,
                        max_results=max_results,
                        academic=academic,
                        academic_rss_links=academic_rss
                    ),
                    "temperature": temperature_grok
                }
                resp = requests.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                answer = data["choices"][0]["message"]["content"]

            elif model_dir == "openai":
                response = client_open_ai.chat.completions.create(  # type: ignore
                    model=generation_model,
                    messages=messages,
                    max_tokens=kwargs.get("max_tokens", 16384),
                    temperature=kwargs.get("temperature", 0),
                    **kwargs
                )
                answer = response.choices[0].message.content

            elif model_dir.startswith("open_ai_o"):
                adjusted = system_instruction_for_response + prompt
                response = client_open_ai.chat.completions.create(  # type: ignore
                    model=generation_model,
                    messages=[{"role": "user", "content": adjusted}],
                    max_completion_tokens=32768,
                    reasoning_effort="high",
                    **kwargs
                )
                answer = response.choices[0].message.content

            elif model_dir == "deepseek":
                response = client_deepseek.chat.completions.create(  # type: ignore
                    model=generation_model,
                    messages=messages,
                    stream=False,
                    temperature=0,
                    **kwargs
                )
                answer = response.choices[0].message.content

            elif model_dir == "claude":
                claude_msgs = []
                if system_instruction_for_response:
                    claude_msgs.append({
                        "role": "system",
                        "content": system_instruction_for_response
                    })
                claude_msgs.extend(messages)

                response = client_claude.messages.create(
                    model=generation_model,
                    max_tokens=kwargs.get("max_tokens", 8192),
                    messages=claude_msgs
                )
                answer = "".join([part.text for part in response.content])

            elif model_dir == "gemini":
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction_for_response,
                    max_output_tokens=kwargs.get("max_output_tokens", 1024),
                    temperature=kwargs.get("temperature", 0.0)
                )
                resp = client_gemini.models.generate_content(
                    model=generation_model,
                    contents=prompt,
                    config=config
                )
                answer = resp.text

            else:
                # Fallback to OpenAI
                response = client_open_ai.chat.completions.create(  # type: ignore
                    model=generation_model,
                    messages=messages,
                    max_tokens=16384,
                    temperature=0,
                    **kwargs
                )
                answer = response.choices[0].message.content

            duration = round(time.perf_counter() - start_time, 2)

        except Exception as e:
            print(f"[{generation_model}][iter {i}] Error: {e}")
            continue

        # Save to disk if requested
        if save:
            out_dir = os.path.join("./output/test_files", model_dir)
            os.makedirs(out_dir, exist_ok=True)
            fname = f"{generation_model}-{i}-{duration}.txt"
            path = os.path.join(out_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(answer)
            print(f"Saved iter {i} answer to {path}")

        answers.append(answer)

    return answers[-1] if answers else None


def generate_llm_response(query_text,  gene_list_string,
                          conn, index, tokenizer, embeddings_model,
                          bm25_index, bm25_chunk_ids,
                          weight_faiss, weight_bm25,
                          system_instruction_for_response,
                          api_type='openai'):
    """
    Generates a response from a language model by performing the following steps:
    - Expanding the input query.
    - Retrieving relevant documents using FAISS and BM25.
    - Combining scores using weighted reciprocal rank fusion (RRF).
    - Constructing a prompt with the retrieved documents.
    - Querying the specified LLM API (OpenAI, Claude, or Gemini).

    Args:
        query_text: The original user query.
        gene_list_string: A comma-separated string of gene names.
        conn: The SQLite database connection.
        index: The FAISS index.
        tokenizer: The tokenizer for the transformer model.
        embeddings_model: The transformer model used for embeddings.
        bm25_index: The BM25 index object.
        bm25_chunk_ids: List of chunk IDs corresponding to the BM25 index.
        weight_faiss: Weight for FAISS scores.
        weight_bm25: Weight for BM25 scores.
        system_instruction_for_response: The system instruction to guide the LLM response.
        api_type: The API type to use ('openai', 'claude', or 'gemini').

    Returns:
        A tuple containing:
            - The generated answer.
            - A list of document references.
            - The combined RRF scores.
            - BM25 scores.
            - FAISS scores.
    """
    # Expand the retrieval query
    expanded_queries = query_expansion(query_text, number=number_of_expansions)
    if not expanded_queries:
        expanded_queries = [query_text]
    else:
        expanded_queries.append(query_text)
    query_expanded_queries = [f"{eq} {gene_list_string}" for eq in expanded_queries]
    query_text = query_text + gene_list_string

    # Retrieve documents using FAISS and BM25
    top_faiss_docs = create_top_faiss_docs(query_expanded_queries, index, tokenizer, embeddings_model, top_k=50)
    top_bm25_docs = create_top_bm25_docs(query_expanded_queries, bm25_index, bm25_chunk_ids, top_k=50)

    # Combine scores using weighted RRF
    rrf_scores = weighted_rrf(top_bm25_docs, top_faiss_docs, weight_faiss, weight_bm25)
    print(f"Retrieving top {amount_docs} documents based on RRF scores.")
    retrieved_chunks_ordered, combined_docs = rank_and_retrieve_documents(
        rrf_scores, conn, top_faiss_docs, top_bm25_docs, amount_docs=amount_docs
    )

    if len(retrieved_chunks_ordered) < amount_docs:
        print(f"Warning: Retrieved only {len(retrieved_chunks_ordered)} documents but expected {amount_docs}.")

    document_references = []
    for doc_id in combined_docs.keys():
        chunk_text = retrieved_chunks_ordered.pop(0) if retrieved_chunks_ordered else "No text available"
        document_references.append(
            f"|Reference {combined_docs[doc_id]['rank']} ({' and '.join(combined_docs[doc_id]['retriever'])}) with "
            f"RRF Score: {combined_docs[doc_id]['score']:.4f}\n{chunk_text}"
        )
    combined_documents = "\n\n".join(document_references)

    prompt = (
        f"Based on the following documents, answer the question using both your knowledge and the provided "
        f"documents: {query_text}\n\nDocuments:\n{combined_documents}\n"
    )

    messages = [
        {"role": "system", "content": system_instruction_for_response},
        {"role": "user", "content": prompt}
    ]
    save_message = f"(role: system, content: {system_instruction_for_response}\nrole: user, content: {prompt})"
    os.makedirs("./output/results", exist_ok=True)
    with open("./output/results/messages.txt", "w", encoding="utf-8") as file:
        file.write(save_message)
    print(f"Using LLM model: {generation_model} for response generation.")

    if api_type.lower() == 'openai':
        save = True
        answer = query_llm(messages, system_instruction_for_response, prompt, save,
                           generation_model=generation_model, query_range=query_range)
    elif api_type.lower() == 'claude':
        answer = query_claude(messages)
    elif api_type.lower() == 'gemini':
        answer = query_gemini(system_instruction_for_response, prompt)
    else:
        raise ValueError("Unsupported API type. Choose from 'openai', 'claude', 'gemini'.")

    bm25_scores = dict([(doc_id, details['score']) for doc_id, details in top_bm25_docs])
    faiss_scores = {doc_id: distance for doc_id, (distance, eq) in top_faiss_docs}

    return answer, document_references, rrf_scores, bm25_scores, faiss_scores


def generate_response_and_save(query,
                               gene_list_string,
                               conn, index, tokenizer, embeddings_model,
                               bm25_index, bm25_chunk_ids,
                               weight_faiss, weight_bm25,
                               system_instruction_for_response):
    """
    Generates a response from an LLM using the provided query and gene information,
    saves the answer and associated scores to files, and closes the database connection.

    Args:
        query: The original user query.
        gene_list_string: A comma-separated string of gene names.
        conn: The SQLite database connection.
        index: The FAISS index.
        tokenizer: The tokenizer for the transformer model.
        embeddings_model: The transformer model used for embeddings.
        bm25_index: The BM25 index object.
        bm25_chunk_ids: List of chunk IDs corresponding to the BM25 index.
        weight_faiss: Weight for FAISS scores.
        weight_bm25: Weight for BM25 scores.
        system_instruction_for_response: The system instruction for generating the LLM response.
    """
    answer, document_references, rrf_scores, bm25_scores, faiss_scores = generate_llm_response(
        query,  gene_list_string,
        conn, index, tokenizer, embeddings_model,
        bm25_index, bm25_chunk_ids,
        weight_faiss, weight_bm25,
        system_instruction_for_response,
    )

    if answer and answer != "Processing complete.":
        save_answer_to_file(answer, document_references)

        export_scores_to_excel(rrf_scores, bm25_scores, faiss_scores, file_name="./output/scores.xlsx")
    else:
        print("Failed to generate a response from the LLM.")
    conn.close()


# File Saving and Processing Helpers
def save_answer_to_file(answer, document_references, file_name="./output/results/answer.txt"):
    """
    Saves the generated answer and associated document references to text files.

    Args:
        answer: The generated answer string.
        document_references: A list of document reference strings.
        file_name: The file path where the answer will be saved.
    """
    with open(file_name, "w", encoding='utf-8') as answer_file:
        answer_file.write(f"Answer:\n{answer}\n\n")
    print(f"Answer saved to {file_name}")
    with open("./output/results/documents.txt", "w", encoding='utf-8') as answer_file:
        for idx, doc in enumerate(document_references, start=1):
            answer_file.write(f"{doc} \n\n")


def process_files_in_directory(data_dir, ncbi_json_dir):
    """
    Processes all .gmt.gz files in the given directory by converting gene IDs to symbols and logging unknown genes.

    Args:
        data_dir: The directory containing the files to process.
        ncbi_json_dir: The directory to store JSON cache files for gene IDs.
    """
    """Process all .gmt.gz files in the given directory."""
    for file in os.listdir(data_dir):
        full_file_path = os.path.join(data_dir, file)
        if os.path.isfile(full_file_path) and file.endswith('.gmt.gz'):
            print(f"Processing file: {file}")

            converted_lines, unknown_genes = convert_gene_id_to_symbols(file,
                                                                        data_dir, ncbi_json_dir)
            print(
                f"Unknown genes saved to 'unknown_genes.txt'. Total unknown genes: {len(unknown_genes)}")


def embed_documents_and_save(index, conn, tokenizer, embeddings_model, data_dir,
                             batch_size, log_path, index_path):
    """
    Embeds documents from the specified directory, updates the FAISS index with the embeddings,
    and then saves the updated index and closes the database connection.

    Args:
        index: The FAISS index.
        conn: The SQLite database connection.
        tokenizer: The tokenizer for processing text.
        embeddings_model: The transformer model for embedding computation.
        data_dir: The directory containing the documents.
        batch_size: The batch size for processing documents.
        log_path: The file path for the file log.
        index_path: The file path to save the FAISS index.
    """
    embed_documents(conn, index, tokenizer, embeddings_model, data_dir=data_dir,
                    batch_size=batch_size, log_path=log_path)
    save_faiss_index(index, index_path=index_path)
    conn.close()


def export_scores_to_excel(rrf_scores, bm25_scores, faiss_scores, file_name="./output/scores.xlsx"):
    """
    Exports combined RRF, BM25, and FAISS scores to an Excel file.

    Args:
        rrf_scores: A dictionary of RRF scores.
        bm25_scores: A dictionary of BM25 scores.
        faiss_scores: A dictionary of FAISS scores.
        file_name: The file path where the Excel file will be saved.
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


def save_scores_to_file(scores, file_name):
    """
    Saves scores to a text file, including details about tokens that contributed to each score.

    Args:
        scores: A dictionary containing scores and token details for each document/chunk.
        file_name: The file path where the scores will be saved.
    """
    with open(file_name, "w", encoding='utf-8') as scores_file:
        for doc_id, details in scores.items():
            if isinstance(details, dict):
                overall_score = details.get('score', 0)
                tokens = details.get('tokens', [])
                scores_file.write(f"Chunk ID: {doc_id}, Score: {overall_score}\n")
                if tokens:
                    tokens_formatted = "\n".join(tokens)
                    scores_file.write(f"Tokens that were relevant:\n{tokens_formatted}\n")
                else:
                    scores_file.write("Tokens that were relevant: None\n")
                scores_file.write("\n")
            else:
                f.write(f"Chunk ID: {doc_id}, Score: {details}\n\n")
    print(f"Scores saved to {file_name}")


def main():
    parser = argparse.ArgumentParser(description="Run the RAG workflow tests for varying gene counts.")
    parser.add_argument(
        "--config",
        type=str,
        default="./configs_system_instruction/GSEA.json",
        help="Path to the configuration JSON file"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    config_name = os.path.splitext(os.path.basename(args.config))[0]
    globals()['config_name'] = config_name
    globals().update(config)

    for max_genes_value in max_genes:
        print(f"=== Running test for max_genes = {max_genes_value} ===")
        current_max_genes = max_genes_value
        gene_list_string, regulation, num_genes = initialize_gene_list(
            max_genes=current_max_genes,
            fdr_threshold=fdr_threshold,
            excel_file_path=r".\data\GSEA\genes_of_interest\PMP22_VS_WT.xlsx",
            de_filter_option="combined",
        )

        data_dir = './data/GSEA/external_gene_data'
        log_dir = './logs'
        log_path = os.path.join(log_dir, 'file_log.json')
        index_path = './database/faiss_index.bin'
        db_path = './database/reference_chunks.db'
        ncbi_json_dir = './data/JSON/'

        process_files_in_directory(data_dir, ncbi_json_dir)

        conn = initialize_database(db_path=db_path)
        tokenizer, embeddings_model = load_embeddings_model_and_tokenizer()
        embedding_dim = embeddings_model.config.hidden_size

        index = initialize_faiss_index(embedding_dim, index_path)
        embed_documents_and_save(index, conn, tokenizer, embeddings_model, data_dir,
                                 batch_size=batch_size, log_path=log_path,
                                 index_path=index_path)

        index = load_faiss_index(embedding_dim, index_path=index_path)
        conn = sqlite3.connect(db_path)
        bm25_index, bm25_chunk_ids, bm25_chunk_texts = build_bm25_index(conn)

        # Single call per gene count since query_llm already performs 5 attempts.
        generate_response_and_save(
            query,
            gene_list_string,
            conn, index, tokenizer, embeddings_model,
            bm25_index, bm25_chunk_ids,
            weight_faiss, weight_bm25,
            system_instruction_response
        )


if __name__ == "__main__":
    main()
