import os
import glob
import re
from pathlib import Path
from typing import List, Set, Tuple, Optional

import pandas as pd
import plotly.express as px
from scipy.stats import pearsonr

figure_width = 1000
figure_height = 600
figure_kwargs = {
    'width': figure_width,
    'height': figure_height, }


def clean_gene_name(raw: str) -> Optional[str]:
    """
    Strip whitespace, trailing commas/annotations, and ignore placeholders or multi-word phrases.
    """
    gene = raw.strip().rstrip(',')
    gene = re.sub(r"\s*\(.*\)$", "", gene)
    if not gene or gene.startswith("Gene ") or len(gene.split()) > 1:
        return None
    return gene


def parse_genes_from_file(filepath: Path) -> Set[str]:
    """
    Extract unique gene symbols from a text file, using simple heuristics to
    skip headers and non-gene lines.
    """
    genes: Set[str] = set()
    expecting_list = False

    for raw in filepath.read_text(encoding="utf8").splitlines():
        line = raw.strip()
        if not line:
            expecting_list = False
            continue

        if line.startswith("Pathway Name:") or (
                line.startswith("**") and line.endswith("**")):
            expecting_list = True
            continue
        if expecting_list or (',' not in line and len(line.split()) > 1):
            expecting_list = False
            continue

        if ':' in line and ',' not in line.split(':', 1)[1]:
            continue

        for raw_gene in line.split(','):
            gene = clean_gene_name(raw_gene)
            if gene:
                genes.add(gene)

    return genes


def parse_filename(filename: Path) -> Tuple[Optional[int], Optional[float]]:
    """
    Expect filenames ending in: ...-<genes>-<index>-<time>.txt
    Returns (input_genes, time_sec) or (None, None) on failure.
    """
    parts = filename.stem.split('-')
    if len(parts) < 3:
        return None, None
    try:
        genes = int(parts[-3])
        time_sec = float(parts[-1])
    except ValueError:
        return None, None
    return genes, time_sec


def collect_data_for_model(model_dir: Path) -> pd.DataFrame:
    """
    Walks the directory for .txt files, parses filenames and contents,
    and returns a DataFrame with columns: input_genes, unique_genes, time_sec.
    """
    records = []
    for filepath in model_dir.glob("*.txt"):
        genes_count, runtime = parse_filename(filepath)
        if genes_count is None or runtime is None:
            continue
        unique_genes = len(parse_genes_from_file(filepath))
        records.append({
            'input_genes': genes_count,
            'unique_genes': unique_genes,
            'time_sec': runtime
        })
    return pd.DataFrame(records)


def plot_and_save(df: pd.DataFrame, model: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nPlotting {model} data...\n")
    fig_box = px.box(
        df, x='input_genes', y='unique_genes', points='all',
        hover_data=['time_sec'],
        labels={'input_genes': 'Input Genes', 'unique_genes': 'Unique Genes'},
        title=f"{model}: Unique Genes per Input Count",
        **figure_kwargs
    )
    fig_box.update_traces(pointpos=0, jitter=0.3)
    fig_box.write_html(str(out_dir / f"box_{model}.html"))

    # Line plot of means (single model)
    summary = (
        df
        .groupby('input_genes', as_index=False)
        .mean(numeric_only=True)
        .sort_values('input_genes')
    )

    fig_line = px.line(
        summary, x='input_genes', y='unique_genes', markers=True,
        hover_data=['time_sec'],
        labels={'input_genes': 'Input Genes',
                'unique_genes': 'Mean Unique Genes'},
        title=f"{model}: Mean Unique Genes per Input Count",
        **figure_kwargs
    )

    fig_line.write_html(str(out_dir / f"line_{model}.html"))


def compute_correlations(df: pd.DataFrame) -> None:
    r_tc, p_tc = pearsonr(df['unique_genes'] / df['input_genes'],
                          df['time_sec'])
    r_iu, p_iu = pearsonr(df['input_genes'], df['unique_genes'])

    results = pd.DataFrame([
        {
            'Metric': 'time vs coverage (%)',
            'r': f"{r_tc:.3f}",
            'p': f"{p_tc:.3e}"
        },
        {
            'Metric': 'input vs unique output',
            'r': f"{r_iu:.3f}",
            'p': f"{p_iu:.3e}"
        }
    ])

    print(results.to_string(index=False))


def main():
    base_dir = Path("./output/test_files")
    out_dir = Path("output/text_files/PNG_HTML")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_summaries = []
    models = [d.name for d in base_dir.iterdir() if d.is_dir()]
    for model in models:
        df = collect_data_for_model(base_dir / model)
        if df.empty:
            print(f"No data for {model}")
            continue

        plot_and_save(df, model, out_dir)
        compute_correlations(df)

        summary = (
            df
            .groupby('input_genes', as_index=False)
            .agg(
                mean_unique_genes=('unique_genes', 'mean'),
                mean_time_sec=('time_sec', 'mean'),
            )
            .assign(model=model)
            .sort_values('input_genes')
        )
        all_summaries.append(summary)

    if all_summaries:
        combined = pd.concat(all_summaries, ignore_index=True)
        fig = px.line(
            combined,
            x='input_genes',
            y='mean_unique_genes',
            color='model',
            markers=True,
            hover_data=['mean_time_sec'],
            labels={
                'input_genes': 'Input Genes',
                'mean_unique_genes': 'Mean Unique Genes',
                'model': 'Model'
            },
            title="All Models: Mean Unique Genes per Input Count",
            **figure_kwargs

        )
        # … after px.line(...)
        # … after creating your figure …

        fig.update_layout(
            # make background fully transparent
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',

            # (optional) restyle legend/font here too
            legend_title_font_size=16,
            legend_font_size=14,
        )

        # ensure grid is on and visible
        fig.update_xaxes(
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1,
        )
        fig.update_yaxes(
            showgrid=True,
            gridcolor='lightgrey',
            gridwidth=1,
        )

        combined_path = out_dir / "combined_models_line.html"
        fig.write_html(str(combined_path))
        print(f"Combined line plot for all models saved: {combined_path}")


if __name__ == '__main__':
    main()
