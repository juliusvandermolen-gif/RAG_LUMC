#!/usr/bin/env python3
import os
import glob
import plotly.express as px
import pandas as pd
from scipy.stats import pearsonr


def parse_genes_from_file(filepath):
    """
    Parse a file (opened with UTF-8 encoding) to extract distinct gene names.

    Expected file format:
      - Section headers (ending with a colon) followed by one or more lines
        of comma-separated gene symbols.
      - Also supports lines that contain a header and genes on the same line.

    Returns:
      A set of unique gene symbols found in the file.
    """
    genes_set = set()
    # Open with UTF-8 encoding.
    with open(filepath, 'r', encoding="utf8") as f:
        lines = f.readlines()

    expecting_genes = False
    for line in lines:
        line = line.strip()
        if not line:
            expecting_genes = False
            continue

        if line.endswith(':'):
            expecting_genes = True
            continue

        if ':' in line:
            parts = line.split(':', 1)
            gene_list_part = parts[1]
        elif expecting_genes:
            gene_list_part = line
        else:
            continue

        # Split by commas and add each cleaned gene name.
        for gene in gene_list_part.split(','):
            gene_clean = gene.strip().strip(',')
            if gene_clean:
                genes_set.add(gene_clean)
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
    directory = "./output/test_files/run"
    txt_files = glob.glob(os.path.join(directory, "*.txt"))

    # Prepare lists for plotting and analysis.
    input_genes_list = []   # X-axis: Amount of input genes (from filename)
    unique_gene_counts = []  # Y-axis: Unique gene count parsed from file content
    time_list = []          # Z-axis: Runtime (in seconds from filename)

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
        print(f"File: {filename}")
        print(f"  Amount of Input Genes: {input_genes}")
        print(f"  Unique Genes Used: {count}")
        print(f"  Time (sec): {time_val}\n")

    if not input_genes_list:
        print("No valid files found in the directory with the expected naming pattern.")
        return

    # Build a DataFrame for analysis.
    df = pd.DataFrame({
        "input_genes": input_genes_list,
        "unique_genes": unique_gene_counts,
        "time_sec": time_list
    })
    # Compute percentage of genes used relative to the input genes.
    df["percentage_used"] = (df["unique_genes"] / df["input_genes"]) * 100

    # --- 2D Box Plot using Plotly ---
    # X-axis: Amount of Input Genes; Y-axis: Total Unique Genes Used.
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

    # --- Correlation Calculations ---
    # Overall correlation between percentage used and runtime.
    overall_corr, overall_p = pearsonr(df["percentage_used"], df["time_sec"])
    print("\nOverall correlation between percentage used and time:")
    print(f"  Pearson r = {overall_corr:.3f} (p-value = {overall_p:.3e})")

    # Correlation per input_genes group.
    print("\nCorrelation per Amount of Input Genes:")
    grouped = df.groupby("input_genes")
    for input_val, group in grouped:
        if len(group) < 2:
            print(f"  Amount of Input Genes {input_val}: Not enough data to compute correlation (n = {len(group)})")
        else:
            r, p = pearsonr(group["percentage_used"], group["time_sec"])
            print(f"  Amount of Input Genes {input_val}: Pearson r = {r:.3f} (p-value = {p:.3e})")


if __name__ == "__main__":
    main()
