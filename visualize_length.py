import gzip
import matplotlib.pyplot as plt


def get_length(file_path):
    """Compute line lengths for a given gzipped file."""
    with gzip.open(file_path, 'rt') as f:
        return [len(line.strip()) for line in f]


# Paths to the gzipped files
file1 = './Data/biomart/hold/genes_consolidated.txt.gz'
file2 = './Data/biomart/hold/wikipathways_synonyms_Homo_sapiens.gmt.gz'

combined_lengths = sorted(get_length(file1) + get_length(file2))

cutoff = 300
filtered_lengths = [l for l in combined_lengths if l <= cutoff]
outliers = [l for l in combined_lengths if l > cutoff]

plt.figure(figsize=(10, 6))
plt.hist(filtered_lengths, bins=range(min(filtered_lengths), max(filtered_lengths) + 10, 10),
         alpha=0.75, edgecolor='black', density=True)
plt.title("Distribution of Line Lengths (Filtered)")
plt.xlabel(f"Line Length (≤ {cutoff})")
plt.ylabel("Density")
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.show()

