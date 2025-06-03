#!/usr/bin/env python3
import re
from pathlib import Path
from typing import Optional, Set, Tuple
import os
import pandas as pd
import plotly.express as px
from scipy.stats import pearsonr
import shutil

# plotting defaults
figure_width: int = 1000
figure_height: int = 600
figure_kwargs = {'width': figure_width, 'height': figure_height}


def normalize_gene(gene: str) -> str:
    """
    Normalize a gene name by stripping whitespace, converting to lowercase,
    removing any parenthetical content, and removing non-alphanumeric characters.

    Args:
        gene: A raw gene name string.

    Returns:
        A normalized gene name string containing only lowercase letters and digits.
    """
    g = gene.strip().lower()
    g = re.sub(r"\s*\(.*\)$", "", g)
    g = re.sub(r"[^a-z0-9]", "", g)
    return g


def load_input_gene_set(amount: int, input_gene_dir: Path) -> Set[str]:
    """
    Load a pre‐generated input gene set from a text file named "all_genes_<amount>.txt".

    Each line in the file corresponds to one gene. Lines that are empty are skipped.
    All gene names are normalized via normalize_gene() before adding to the set.

    Args:
        amount: The number of genes (used to name the file "all_genes_<amount>.txt").
        input_gene_dir: Path to the directory containing the input gene files.

    Returns:
        A set of normalized gene names.

    Raises:
        FileNotFoundError: If the file "all_genes_<amount>.txt" does not exist in input_gene_dir.
    """
    fn: Path = input_gene_dir / f"all_genes_{amount}.txt"
    if not fn.exists():
        raise FileNotFoundError(f"{fn} not found – please generate it first.")

    with fn.open(encoding="utf8") as f:
        return {normalize_gene(line) for line in f if line.strip()}


def clean_gene_name(raw: str) -> Optional[str]:
    """
    Attempt to clean a raw gene name fragment. Strips whitespace and trailing commas,
    removes any parenthetical content, and discards entries that are empty, start with
    "gene ", or contain more than one whitespace‐separated token.

    Args:
        raw: A raw string fragment that may represent a gene name.

    Returns:
        The cleaned gene name if valid, or None if the fragment should be discarded.
    """
    g = raw.strip().rstrip(', ')
    g = re.sub(r"\s*\(.*\)$", "", g)
    if not g or g.startswith("gene ") or len(g.split()) > 1:
        return None
    return g


def parse_genes_from_file(fp: Path) -> Set[str]:
    """
    Parse unique gene names from a text file containing comma‐separated gene lists
    and possible metadata lines. Lines beginning with "Pathway Name:" or enclosed
    in double asterisks are ignored. Lines that contain ":" but not ", " are skipped.
    For lines with comma‐separated tokens, each token is cleaned via clean_gene_name()
    and added to the result set if valid.

    Args:
        fp: Path to the .txt file to parse.

    Returns:
        A set of cleaned gene names extracted from the file.
    """
    genes = set()
    raw_text = fp.read_text(encoding="utf8")
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Pathway Name:") or (line.startswith("**") and line.endswith("**")):
            continue
        if ': ' in line and ', ' not in line:
            continue
        if ', ' in line:
            for part in line.split(', '):
                cg = clean_gene_name(part)
                if cg:
                    genes.add(cg)
    return genes


def parse_filename(f: Path) -> Tuple[Optional[int], Optional[float]]:
    """
    Extract the input gene count and runtime (in seconds) from a filename.
    Filenames are expected to follow a pattern where the third‐to‐last segment
    (when splitting on '-') is an integer (gene count), and the last segment is a float
    (runtime in seconds). If parsing fails, returns (None, None).

    Args:
        f: Path object representing a .txt file. The stem of f must contain at least
           three '-'‐separated parts, where the third‐to‐last is an integer and the last
           is a float.

    Returns:
        A tuple (gene_amount, time) where:
            - gene_amount is an int if parsing succeeds, or None otherwise.
            - time is a float if parsing succeeds, or None otherwise.
    """
    parts = f.stem.split('-')
    if len(parts) < 3:
        return None, None
    try:
        gene_amount = int(parts[-3])
        time_val = float(parts[-1])
    except ValueError:
        return None, None
    return gene_amount, time_val


def collect_data_for_model(model_dir: Path, input_gene_dir: Path) -> pd.DataFrame:
    """
    Traverse a model directory looking for .txt output files. For each valid file:
      1. Parse input gene count and runtime from its filename via parse_filename().
      2. Extract the set of output genes via parse_genes_from_file(), normalize them,
         and count how many match the pre‐loaded input gene set (loaded via load_input_gene_set()).
      3. Compute the total number of unique output genes and the percentage of hallucinated genes.
      4. Append a record with filename, input_genes, time_sec, total_output_genes, matched_genes,
         and hallucination_perc.

    Args:
        model_dir: Path to a directory containing .txt output files produced by the model.
        input_gene_dir: Path to the directory containing "all_genes_<amount>.txt" files.

    Returns:
        A pandas DataFrame with columns:
            - 'filename': Name of the .txt file.
            - 'input_genes': Number of input genes (parsed from filename).
            - 'time_sec': Runtime in seconds (parsed from filename).
            - 'total_output_genes': Count of unique normalized output genes.
            - 'matched_genes': Count of output genes that are present in the input set.
            - 'hallucination_perc': Percentage of hallucinated genes (100 * (total - matched)/total).
        If no valid files are found, returns an empty DataFrame.
    """
    rec = []
    for f in model_dir.glob("*.txt"):
        ig, tm = parse_filename(f)
        if ig is None:
            continue

        raw_set: Set[str] = parse_genes_from_file(f)
        raw_norm: Set[str] = {normalize_gene(g) for g in raw_set}

        try:
            inp_set: Set[str] = load_input_gene_set(ig, input_gene_dir)
        except FileNotFoundError:
            inp_set = set()

        matched: int = sum(1 for g in raw_norm if g in inp_set)
        total: int = len(raw_norm)
        hallucination_perc: float = ((total - matched) / total * 100) if total else 0.0

        rec.append({
            'filename':             f.name,
            'input_genes':          ig,
            'time_sec':             tm,
            'total_output_genes':   total,
            'matched_genes':        matched,
            'hallucination_perc':   hallucination_perc,
        })

    return pd.DataFrame(rec)


def plot_and_save(df: pd.DataFrame, model: str, out_dir: Path) -> None:
    """
    Generate and save boxplots and line plots for a given model's DataFrame.

    - Boxplots (one for 'total_output_genes', one for 'matched_genes') grouped by 'input_genes'.
    - Line plots showing mean total output and mean non‐hallucinated output per input size.

    Each plot is saved as an HTML file under out_dir, named:
      - box_<model>.html
      - box_<model>_filtered.html  (for non‐hallucinated counts)
      - line_<model>.html
      - line_<model>_filtered.html

    Args:
        df: Pandas DataFrame containing at least these columns:
            - 'input_genes': integer input size.
            - 'total_output_genes': integer total unique output.
            - 'matched_genes': integer non‐hallucinated output.
            - 'filename': source filename (used in hover tooltips).
            - 'time_sec': runtime in seconds (used in hover tooltips).
        model: A string label for the model (used in plot titles and filenames).
        out_dir: Path to an existing or new directory where HTML files will be written.

    Returns:
        None. Files are written directly to out_dir.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Per‐input‐size boxplots
    for col, label, suf in [
        ('total_output_genes', 'total output genes', ''),
        ('matched_genes',      'non-hallucinated genes', '_filtered'),
    ]:
        fig = px.box(
            df,
            x='input_genes',
            y=col,
            points='all',
            hover_data=['filename', 'time_sec'],
            labels={'input_genes': 'Input genes', col: label},
            title=f"{model}: {label} per input count",
            width=1920,
            height=1080
        )
        fig.update_traces(pointpos=0, jitter=0.3)
        fig.update_layout(
            title_font_size=28,
            legend_title_font_size=20,
            legend_font_size=22
        )
        fig.update_xaxes(title_font=dict(size=25), tickfont=dict(size=22))
        fig.update_yaxes(title_font=dict(size=25), tickfont=dict(size=22))
        fig.write_html(out_dir / f"box_{model}{suf}.html")

    # Per‐input‐size line plots (means)
    summary = (
        df
        .groupby('input_genes', as_index=False)
        .mean(numeric_only=True)
        .sort_values('input_genes')
    )
    for col, label, suf in [
        ('total_output_genes', 'M output genes', ''),
        ('matched_genes',      'Mean non-hallucinated genes', '_filtered'),
    ]:
        fig = px.line(
            summary,
            x='input_genes',
            y=col,
            markers=True,
            hover_data=['mean_time_sec'],
            labels={'input_genes': 'Input genes', col: label},
            title=f"{model}: {label} per input count",
            width=1920,
            height=1080
        )
        fig.update_layout(
            title_font_size=28,
            legend_title_font_size=20,
            legend_font_size=22
        )
        fig.update_xaxes(title_font=dict(size=25), tickfont=dict(size=22))
        fig.update_yaxes(title_font=dict(size=25), tickfont=dict(size=22))
        fig.write_html(out_dir / f"line_{model}{suf}.html")


def compute_correlations(df: pd.DataFrame) -> None:
    """
    Compute and print Pearson correlations for:
      1) time_sec vs. coverage (coverage = total_output_genes / input_genes)
      2) input_genes vs. total_output_genes

    If either variable in a pair is constant (no variation), skip that correlation
    and print a message indicating insufficient variation.

    Args:
        df: Pandas DataFrame containing columns:
            - 'input_genes': integer input size.
            - 'time_sec': float runtime in seconds.
            - 'total_output_genes': integer count of unique output genes.

    Returns:
        None. Prints results or skip messages to stdout.
    """
    coverage = df['total_output_genes'] / df['input_genes']

    # 1) time vs coverage
    if coverage.nunique() > 1 and df['time_sec'].nunique() > 1:
        r_tc, p_tc = pearsonr(coverage, df['time_sec'])
        print(f"  time vs coverage (%):    r = {r_tc:.3f}, p = {p_tc:.3e}")
    else:
        print("  Skipping time vs coverage: not enough variation to compute correlation.")

    # 2) input size vs total (unique) output
    if df['input_genes'].nunique() > 1 and df['total_output_genes'].nunique() > 1:
        r_iu, p_iu = pearsonr(df['input_genes'], df['total_output_genes'])
        print(f"  input vs output size:    r = {r_iu:.3f}, p = {p_iu:.3e}")
    else:
        print("  Skipping input vs output size: not enough variation to compute correlation.")


def create_input_dir(input_gene_dir: Path, max_genes: Optional[Set[int]] = None) -> None:
    """
    Create a directory of input gene files named "all_genes_<size>.txt" for various sizes.
    Reads the list of genes from "./data/GSEA/genes_of_interest/PMP22_VS_WT.xlsx" (column 'X').
    For each size in the default range 100, 150, ..., 1000, plus any sizes in max_genes,
    writes the first <size> genes (one per line) into a new text file. Existing files/folders
    with the same name are removed/replaced.

    Args:
        input_gene_dir: Path to the directory where "all_genes_<size>.txt" files will be created.
        max_genes: Optional set of additional integer sizes to include beyond the default 100–1000.

    Returns:
        None. The function creates or updates files under input_gene_dir.
    """
    input_gene_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel("./data/GSEA/genes_of_interest/PMP22_VS_WT.xlsx")
    genes = df['X'].dropna().astype(str).tolist()

    sizes = list(range(100, 1001, 50))
    if max_genes:
        for sz in max_genes:
            if sz not in sizes:
                sizes.append(sz)

    for size in sorted(sizes):
        file_path = input_gene_dir / f"all_genes_{size}.txt"

        if file_path.exists() and file_path.is_dir():
            shutil.rmtree(file_path)
        elif file_path.exists():
            file_path.unlink()

        with file_path.open('w', encoding='utf8') as f:
            f.write("\n".join(genes[:size]))


def main() -> None:
    """
    Main entry point for the script:
      1. Create input gene files under "output/all_genes".
      2. Iterate over each model subdirectory in "./output/test_files".
      3. For each model, collect data, compute correlations, and build summary DataFrames.
      4. Generate combined line plots and boxplots, writing HTML to "output/results/plots".

    Returns:
        None. Writes output files (HTML plots, CSVs) to disk.
    """
    input_gene_dir = Path("output/all_genes")
    create_input_dir(input_gene_dir)

    base_dir = Path("./output/test_files")
    out_dir = Path("output/results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_full = []
    all_filt = []
    hall_data = []

    # Process each model directory except "configurations ran"
    for model_dir in sorted(base_dir.iterdir()):
        if not model_dir.is_dir() or model_dir.name == "configurations ran":
            continue
        model = model_dir.name
        df = collect_data_for_model(model_dir, input_gene_dir)
        if df.empty:
            print(f"no data for {model}, skipping.")
            continue

        print(model)
        compute_correlations(df)

        sum_full = (
            df.groupby('input_genes', as_index=False)
              .agg(mean_total=('total_output_genes', 'mean'),
                   mean_time_sec=('time_sec', 'mean'))
              .assign(model=model)
              .sort_values('input_genes')
        )
        sum_filt = (
            df.groupby('input_genes', as_index=False)
              .agg(mean_matched=('matched_genes', 'mean'),
                   mean_time_sec=('time_sec', 'mean'))
              .assign(model=model)
              .sort_values('input_genes')
        )

        all_full.append(sum_full)
        all_filt.append(sum_filt)
        hall_data.append(df[['filename', 'hallucination_perc']].assign(model=model))

    if all_full:
        combined = pd.concat(all_full, ignore_index=True)
        fig = px.line(
            combined,
            x='input_genes',
            y='mean_total',
            color='model',
            markers=True,
            hover_data=['mean_time_sec'],
            labels={
                'input_genes': 'Number of input genes',
                'mean_total':  'Mean number of output genes',
                'model':       'Model'
            },
            title="Mean number of output genes vs. input gene count across all models",
            width=1920,
            height=1080
        )
        fig.update_layout(
            title_font_size=28,
            legend_title_font_size=20,
            legend_font_size=22,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)'
        )
        fig.update_xaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1
        )
        fig.update_yaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1
        )
        fig.write_html(out_dir / "combined_models_line.html")

    if all_filt:
        combined2 = pd.concat(all_filt, ignore_index=True)
        csv_dir = "./output/results/csv"
        if not os.path.exists(csv_dir):
            os.makedirs(csv_dir)
        combined2.to_csv(os.path.join(csv_dir, "combined_models_line_filtered.csv"), index=False)
        fig = px.line(
            combined2,
            x='input_genes',
            y='mean_matched',
            color='model',
            markers=True,
            hover_data=['mean_time_sec'],
            labels={
                'input_genes': 'Number of input genes',
                'mean_matched': 'Mean number of non-hallucinated genes',
                'model':       'Model'
            },
            title="Mean number of non-hallucinated genes vs. input gene count across all models",
            width=1920,
            height=1080
        )
        fig.update_layout(
            title_font_size=28,
            legend_title_font_size=20,
            legend_font_size=22,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)'
        )
        fig.update_xaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1
        )
        fig.update_yaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1
        )
        fig.write_html(out_dir / "combined_models_line_filtered.html")

    if hall_data:
        hall_df = pd.concat(hall_data, ignore_index=True)
        fig = px.box(
            hall_df,
            x='model',
            y='hallucination_perc',
            points='all',
            color='model',
            custom_data=['filename'],
            labels={'model': 'Model', 'hallucination_perc': 'Percentage of hallucinated genes (%)'},
            title="Distribution of hallucinated gene percentages across models",
            template="plotly_white",
            width=1920,
            height=1080
        )
        fig.update_traces(
            pointpos=0,
            jitter=0.3,
            hovertemplate="<b>%{customdata[0]}</b><br>Hallucination: %{y:.2f}%<extra></extra>",
            showlegend=True
        )
        fig.update_layout(
            title_font_size=28,
            legend_title_font_size=20,
            legend_font_size=22,
            title={'x': 0.5, 'xanchor': 'center'}
        )
        fig.update_xaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=False
        )
        fig.update_yaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1
        )
        fig.write_html(out_dir / "hallucination_boxplot_per_model.html")

    cfg_base = Path(r"output/test_files/configurations ran")
    if cfg_base.exists():
        records = []
        for cfg in sorted(cfg_base.iterdir()):
            if not cfg.is_dir():
                continue
            name = cfg.name
            if not name.startswith("With"):
                continue
            for f in cfg.glob("*.txt"):
                ug: int = len(parse_genes_from_file(f))
                records.append({
                    'configuration': name,
                    'unique_genes': ug,
                    'filename': f.name
                })
        if records:
            df_cfg = pd.DataFrame(records)
            order = (
                df_cfg
                .groupby('configuration')['unique_genes']
                .median()
                .sort_values(ascending=True)
                .index
                .tolist()
            )
            fig = px.box(
                df_cfg,
                x='configuration',
                y='unique_genes',
                points='all',
                color='configuration',
                custom_data=['filename'],
                category_orders={'configuration': order},
                labels={'configuration': 'Configuration', 'unique_genes': 'Number of unique genes'},
                title="Unique gene counts per configuration (ordered by median) for OpenAI o3",
                template="plotly_white",
                width=1920,
                height=1080
            )
            fig.update_traces(
                pointpos=0,
                jitter=0.3,
                hovertemplate="<b>%{customdata[0]}</b><br>Unique genes: %{y}<extra></extra>"
            )
            fig.update_layout(
                title_font_size=32,
                legend_title_font_size=30,
                legend_font_size=25,
                title={'x': 0.5, 'xanchor': 'center'}
            )
            fig.update_xaxes(
                title_font=dict(size=30),
                tickfont=dict(size=25),
                showgrid=False
            )
            fig.update_yaxes(
                title_font=dict(size=30),
                tickfont=dict(size=25),
                showgrid=True,
                gridcolor='lightgrey',
                gridwidth=1
            )
            fig.write_html(out_dir / "configurations_ran_boxplot.html")


if __name__ == '__main__':
    main()
