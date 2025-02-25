import gzip
import matplotlib.pyplot as plt
import re
import os
import sqlite3

def length_word(line):
    """
    Counts words in a given line using a regex that matches alphanumeric
    or hyphenated tokens. E.g., 'foo-bar' is one token.
    """
    words = re.findall(r'\b[\w-]+\b', line.lower())
    return len(words)

def get_length(file_path):
    """
    Reads a gzipped text file line by line, counts the words in each line
    (via regex), and returns a list of word counts.

    Also prints lines (and their word_count) if word_count < 3.
    """
    lengths = []
    if not os.path.exists(file_path):
        return lengths

    with gzip.open(file_path, 'rt', encoding='utf-8') as f:
        for line in f:
            word_count = length_word(line)
            lengths.append(word_count)
            if word_count < 3:
                print(f"{word_count} {line}")
    return lengths

def get_lengths_from_db(db_path, table_name, text_column):
    """
    Connects to the SQLite database `db_path`, queries `table_name` for `text_column`,
    and uses the LENGTH(...) - LENGTH(REPLACE(...)) + 1 trick to count words
    (assuming single-space separation).
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = f"""
    WITH counts AS (
        SELECT 
            (
                LENGTH(TRIM({text_column})) 
                - LENGTH(REPLACE(TRIM({text_column}), ' ', ''))
                + 1
            ) AS word_count
        FROM {table_name}
        WHERE TRIM({text_column}) <> ''
          AND {text_column} IS NOT NULL
    )
    SELECT word_count
    FROM counts
    """

    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()

    # Convert to a list of integers
    lengths = [row[0] for row in results]
    return lengths

def main():
    # Gzipped files to read
    file1 = './Data/biomart/without_biomart_wik/wikipathways_synonyms_Rattus_norvegicus.gmt.gz'
    file2 = './Data/biomart/without_biomart_wik/rat_genes_consolidated.txt.gz'

    lengths_file1 = get_length(file1)
    lengths_file2 = get_length(file2)
    combined_lengths = sorted(lengths_file1 + lengths_file2)
    db_path = 'chunks_embeddings.db'
    table_name = 'chunks'
    text_column = 'text'
    lengths_db = get_lengths_from_db(db_path, table_name, text_column)
    #combined_lengths = sorted(lengths_file1 + lengths_file2 + lengths_db)

    cutoff = 30

    total_count = len(combined_lengths)
    below_cutoff_count = sum(1 for l in combined_lengths if l <= cutoff)
    percentage_below_cutoff = (below_cutoff_count / total_count) * 100 if total_count > 0 else 0

    print(f"Total lines: {total_count}")
    print(f"Percentage of lines with length ≤ {cutoff}: {percentage_below_cutoff:.2f}%")

    focused_lengths = [l for l in combined_lengths if l <= cutoff]

    plt.figure(figsize=(12, 6))
    num_bins = 50
    plt.hist(focused_lengths, bins=num_bins, alpha=0.75, edgecolor='black', density=True)

    plt.title(f"Distribution of Line Lengths (Focused on ≤ {cutoff} Words)")
    plt.xlabel("Line Length (in Words)")
    plt.ylabel("Density")


    plt.xticks(range(0, cutoff + 1, 25))

    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.show()

if __name__ == "__main__":
    main()
