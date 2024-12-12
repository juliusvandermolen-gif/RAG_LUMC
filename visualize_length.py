import gzip
import matplotlib.pyplot as plt
import re
import os


def length_word(line):
    words = re.findall(r'\b[\w-]+\b', line.lower())
    return len(words)


def get_length(file_path):
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


def main():
    file1 = './Data/biomart/hold/genes_consolidated.txt.gz'
    file2 = './Data/biomart/hold/wikipathways_synonyms_Homo_sapiens.gmt.gz'

    lengths_file1 = get_length(file1)
    lengths_file2 = get_length(file2)

    combined_lengths = sorted(lengths_file1 + lengths_file2)

    cutoff = 30
    below_cutoff_count = sum(1 for l in combined_lengths if l <= cutoff)
    total_count = len(combined_lengths)
    percentage_below_cutoff = (below_cutoff_count / total_count) * 100 if total_count > 0 else 0

    print(f"Percentage of lines with length ≤ {cutoff}: {percentage_below_cutoff:.2f}%")

    focus_range_cutoff = cutoff
    focused_lengths = [l for l in combined_lengths if l <= focus_range_cutoff]

    plt.figure(figsize=(12, 6))
    bins = list(range(0, focus_range_cutoff + 2))
    plt.hist(focused_lengths, bins=bins, alpha=0.75, edgecolor='black', density=True)
    plt.title(f"Distribution of Line Lengths (Focused on ≤ {focus_range_cutoff} Words)")
    plt.xlabel("Line Length (in Words)")
    plt.ylabel("Density")
    plt.xticks(bins)
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.show()


if __name__ == "__main__":
    main()
