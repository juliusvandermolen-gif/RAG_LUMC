import os
import glob
import plotly.express as px
import pandas as pd
from scipy.stats import pearsonr
import re

import re


def parse_genes_from_file(filepath):
    """
    Parse a file (opened with UTF-8 encoding) to extract distinct gene names.

    Handles multiple formats:
      - Files where pathway headers and gene lists are on separate lines.
      - Files where headers and gene lists appear on the same line.
      - Files with Markdown-style headers.

    Heuristics used:
      • Lines starting with "Pathway Name:" or wrapped in "**" are considered headers.
      • Lines without a comma and containing multiple words are assumed to be headers.
      • If a line contains a colon (e.g. an inline header), the part after the colon is used only if it has commas.
      • Gene names with trailing annotations in parentheses are cleaned.
      • Placeholder tokens (e.g., "Gene A") and multiword phrases are ignored.

    Returns:
      A set of unique gene symbols found in the file.
    """
    genes_set = set()
    gene_dict_counter = {}
    with open(filepath, 'r', encoding="utf8") as f:
        lines = f.readlines()

    # Flag to signal that the next non-empty line is expected to be a gene list.
    expecting_genes = False

    for line in lines:
        line = line.strip()
        if not line:
            expecting_genes = False
            continue

        if line.startswith("Pathway Name:") or (line.startswith("**") and line.endswith("**")):
            expecting_genes = True
            continue

        if ',' not in line and len(line.split()) > 1:
            expecting_genes = True
            continue

        if ':' in line and not expecting_genes:
            parts = line.split(':', 1)
            candidate = parts[1].strip()
            if ',' in candidate:
                line = candidate

        expecting_genes = False

        # Split the line by commas and process each gene.
        for gene in line.split(','):
            gene_clean = gene.strip().strip(',')
            # Remove any trailing annotation in parentheses,
            # e.g., "Mpz (implicit via pathway context)" → "Mpz"
            gene_clean = re.sub(r'\s*\(.*\)$', '', gene_clean)
            if gene_clean.startswith("Gene "):
                continue
            # If the cleaned entry contains multiple words, assume it isn’t a proper gene.
            if len(gene_clean.split()) > 1:
                continue
            if gene_clean:
                genes_set.add(gene_clean)
                if gene_clean not in gene_dict_counter:
                    gene_dict_counter[gene_clean] = 1
                else:
                    gene_dict_counter[gene_clean] += 1

    return genes_set


def parse_filename(filename):
    """
    Parse a filename assumed to be of the form:
      o3-mini-GSEA-{input_genes}-{number}-{time}.txt

    Returns:
      input_genes (int): The amount of input genes (from the filename).
      time_val (float): The runtime (in seconds, from the filename).

    If parsing fails, returns (None, None).
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    parts = base.split('-')
    # Expected structure:
    # parts[0] = "o3"
    # parts[1] = "mini"
    # parts[2] = "GSEA"
    # parts[3] = input_genes (the amount of input genes)
    # parts[4] = a number (index, etc.)
    # parts[5] = time (if time contains hyphens join the rest)
    if base.startswith('grok'):
        # Expected structure:
        # parts[0] = "grok"
        # parts[1] = "3"
        # parts[2] = "mini"
        # parts[3] = "beta
        # parts[4] = "GSEA
        # parts[5] = input_genes (the amount of input genes)
        # parts[6] = a number (index, etc.)
        # parts[7] = time (if time contains hyphens join the rest)
        if len(parts) != 8:
            print("len not 8!")
            return None, None
        else:
            try:
                input_genes = int(parts[5])
                time_str = '-'.join(parts[7:])
                time_val = float(time_str)
            except Exception as e:
                print(f"Error parsing filename {filename}: {e}")
                return None, None
            return input_genes, time_val
    if len(parts) < 6:
        return None, None
    try:
        input_genes = int(parts[3])
        time_str = '-'.join(parts[5:])
        time_val = float(time_str)
    except Exception as e:
        print(f"Error parsing filename {filename}: {e}")
        return None, None
    return input_genes, time_val


def main():
    # Define the directory with the .txt files.
    directory = "./output/test_files"
    txt_files = glob.glob(os.path.join(directory, "*.txt"))

    # Prepare lists for plotting and analysis.
    input_genes_list = []  # X-axis: Amount of input genes (from filename)
    unique_gene_counts = []  # Y-axis: Unique gene count parsed from file content
    time_list = []  # Z-axis: Runtime (in seconds from filename)

    for filename in txt_files:
        input_genes, time_val = parse_filename(filename)
        if input_genes is None or time_val is None:
            print(f"Skipping file with unexpected format: {filename}")
            continue

        genes = parse_genes_from_file(filename)
        count = len(genes)

        input_genes_list.append(input_genes)
        unique_gene_counts.append(count)
        time_list.append(time_val)

        # Debug output for each file.
        # print(f"File: {filename}")
        # print(f"  Amount of Input Genes: {input_genes}")
        # print(f"  Unique Genes Used: {count}")
        # print(f"  Time (sec): {time_val}\n")

    if not input_genes_list:
        print("No valid files found in the directory with the expected naming pattern.")
        return

    # Build a DataFrame for analysis.
    df = pd.DataFrame({
        "input_genes": input_genes_list,
        "unique_genes": unique_gene_counts,
        "time_sec": time_list
    })

    df["percentage_used"] = (df["unique_genes"] / df["input_genes"]) * 100

    df_mean = df.groupby("input_genes", as_index=False)[["unique_genes", "time_sec", "percentage_used"]].mean()

    df_mean = df_mean.sort_values("input_genes")
    fig_box = px.box(
        df,
        x="input_genes",
        y="unique_genes",
        points="all",  # show all data points
        hover_data={"time_sec": True, "percentage_used": True},
        labels={
            "input_genes": "Amount of Input Genes",
            "unique_genes": "Total Unique Genes Used"
        },
        title="Boxplot: Distribution of Unique Genes Used per Amount of Input Genes"
    )
    fig_box.show()
    # --- 2D Line Plot using Plotly ---
    # X-axis: Amount of Input Genes; Y-axis: Mean Unique Genes Used.
    fig_line = px.line(
        df_mean,
        x="input_genes",
        y="unique_genes",
        markers=True,  # display markers on the mean values
        hover_data={"time_sec": True, "percentage_used": True},
        labels={
            "input_genes": "Amount of Input Genes",
            "unique_genes": "Mean Unique Genes Used"
        },
        title="Line Plot: Mean Unique Genes Used per Amount of Input Genes"
    )

    fig_line.show()

    # --- 3D Scatter Plot using Plotly ---
    # X-axis: Amount of Input Genes; Y-axis: Unique Genes Used; Z-axis: Time.
    fig_3d = px.scatter_3d(
        df,
        x="input_genes",
        y="unique_genes",
        z="time_sec",
        hover_data={"percentage_used": True},
        labels={
            "input_genes": "Amount of Input Genes",
            "unique_genes": "Total Unique Genes Used",
            "time_sec": "Time (sec)"
        },
        title="3D Scatter: Input Genes vs Unique Genes vs Time"
    )
    fig_3d.show()
    print(df.columns.to_list())
    # --- Correlation Calculations ---
    # Overall correlation between percentage used and runtime.
    overall_corr, overall_p = pearsonr(df["percentage_used"], df["time_sec"])
    print(df[["input_genes", "unique_genes"]])
    correlation_genes, correlation_p = pearsonr(df["input_genes"], df["unique_genes"])
    print("\nOverall correlation between percentage used and time:")
    print(f"  Pearson r = {overall_corr:.3f} (p-value = {overall_p:.3e})\n")
    print(f"overall correlation between amount of input genes and unique genes used\n"
          f" Pearson r = {correlation_genes:.3f} (p-value = {correlation_p:.3e})")
    #
    # # Correlation per input_genes group.
    print("\nCorrelation per Amount of Input Genes:")
    grouped = df.groupby("input_genes")

    for input_val, group in grouped:
        if len(group) < 2:
            print(f"  Amount of Input Genes {input_val}: Not enough data to compute correlation (n = {len(group)})")
        else:
            r, p = pearsonr(group["percentage_used"], group["time_sec"])
            average_p, average_g = group["percentage_used"].mean(), group["unique_genes"].mean()
            print(
                f"  Amount of Input Genes {input_val}: Pearson r = {r:.3f} (p-value = {p:.3e}) {average_p}, {average_g} ")


if __name__ == "__main__":
    main()
