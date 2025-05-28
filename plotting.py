#!/usr/bin/env python3
import re
from pathlib import Path
from typing import Optional, Set, Tuple
import os
import pandas as pd
import plotly.express as px
from scipy.stats import pearsonr

# plotting defaults
figure_width = 1000
figure_height = 600
figure_kwargs = {'width': figure_width, 'height': figure_height}

# where your input-gene lists live:
INPUT_GENE_DIR = Path(r"supporting scripts/calculate_overlap")


def normalize_gene(gene: str) -> str:
    g = gene.strip().lower()
    g = re.sub(r"\s*\(.*\)$", "", g)
    g = re.sub(r"[^a-z0-9]", "", g)
    return g


def load_input_gene_set(amount: int) -> Set[str]:
    fn = INPUT_GENE_DIR / f"all_genes_{amount}.txt"
    if not fn.exists():
        raise FileNotFoundError(f"{fn} not found – please generate it first.")

    with fn.open(encoding="utf8") as f:
        return {normalize_gene(line) for line in f if line.strip()}
    return {normalize_gene(line) for line in f if line.strip()}


def clean_gene_name(raw: str) -> Optional[str]:
    g = raw.strip().rstrip(', ')
    g = re.sub(r"\s*\(.*\)$", "", g)
    if not g or g.startswith("gene ") or len(g.split()) > 1:
        return None
    return g


def parse_genes_from_file(fp: Path) -> Set[str]:
    genes = set()
    for raw in fp.read_text(encoding="utf8").splitlines():
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
    parts = f.stem.split('-')
    if len(parts) < 3:
        return None, None
    try:
        g = int(parts[-3])
        t = float(parts[-1])
    except ValueError:
        return None, None
    return g, t


def collect_data_for_model(model_dir: Path) -> pd.DataFrame:
    rec = []
    for f in model_dir.glob("*.txt"):
        ig, tm = parse_filename(f)
        if ig is None:
            continue

        raw = parse_genes_from_file(f)
        raw_norm = {normalize_gene(g) for g in raw}

        try:
            inp_set = load_input_gene_set(ig)
        except FileNotFoundError:
            inp_set = set()

        matched = sum(1 for g in raw_norm if g in inp_set)
        total = len(raw_norm)
        hallucination_perc = (total - matched) / total * 100 if total else 0.0

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
    out_dir.mkdir(parents=True, exist_ok=True)

    # per‐input‐size boxplots
    for col, label, suf in [
        ('total_output_genes', 'total output genes', ''),
        ('matched_genes',      'non-hallucinated genes', '_filtered'),
    ]:
        fig = px.box(
            df,
            x='input_genes', y=col,
            points='all',
            hover_data=['filename', 'time_sec'],
            labels={'input_genes': 'Input genes', col: label},
            title=f"{model}: {label} per input count",
            width=1920, height=1080
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

    # per‐input‐size lineplots (means)
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
            x='input_genes', y=col,
            markers=True,
            hover_data=['time_sec'],
            labels={'input_genes': 'Input genes', col: label},
            title=f"{model}: {label} per input count",
            width=1920, height=1080
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
    # coverage: fraction of output that isn't hallucinated
    coverage = df['total_output_genes'] / df['input_genes']

    # 1) time vs coverage
    r_tc, p_tc = pearsonr(coverage, df['time_sec'])

    # 2) input size vs total (unique) output
    r_iu, p_iu = pearsonr(df['input_genes'], df['total_output_genes'])

    results = pd.DataFrame([
        {
            'Metric': 'time vs coverage (%)',
            'r': f"{r_tc:.3f}",
            'p': f"{p_tc:.3e}"
        },
        {
            'Metric': 'input vs output size',
            'r': f"{r_iu:.3f}",
            'p': f"{p_iu:.3e}"
        }
    ])

    print(results.to_string(index=False))


def main():
    base_dir = Path("./output/test_files")
    out_dir = Path("output/results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_full = []
    all_filt = []
    hall_data = []

    # process each model directory except configurations ran
    for model_dir in sorted(base_dir.iterdir()):
        if not model_dir.is_dir() or model_dir.name == "configurations ran":
            continue
        model = model_dir.name
        df = collect_data_for_model(model_dir)
        if df.empty:
            print(f"no data for {model}, skipping.")
            continue

        # plot_and_save(df, model, out_dir)
        print(model)
        compute_correlations(df)
        sum_full = (
            df.groupby('input_genes', as_index=False).agg(
                mean_total=('total_output_genes', 'mean'),
                mean_time_sec=('time_sec', 'mean')
            )
            .assign(model=model)
            .sort_values('input_genes')
        )
        sum_filt = (
            df
            .groupby('input_genes', as_index=False).agg(
                mean_matched=('matched_genes', 'mean'),
                mean_time_sec=('time_sec', 'mean')
            )
            .assign(model=model)
            .sort_values('input_genes')
        )

        all_full.append(sum_full)
        all_filt.append(sum_filt)
        hall_data.append(df[['filename', 'hallucination_perc']].assign(model=model))

    # combined unfiltered line plot
    if all_full:
        combined = pd.concat(all_full, ignore_index=True)
        fig = px.line(
            combined,
            x='input_genes', y='mean_total',
            color='model', markers=True,
            hover_data=['mean_time_sec'],
            labels={
                'input_genes': 'Number of input genes',
                'mean_total':  'Mean number of output genes',
                'model':       'Model'
            },
            title="Mean number of output genes vs. input gene count across all models",
            width=1920, height=1080
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
            showgrid=True, gridcolor='lightgrey', gridwidth=1
        )
        fig.update_yaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True, gridcolor='lightgrey', gridwidth=1
        )
        fig.write_html(out_dir / "combined_models_line.html")

    # combined filtered line plot
    if all_filt:
        combined2 = pd.concat(all_filt, ignore_index=True)
        csv_dir = "./output/results/csv"
        if not os.path.exists(csv_dir):
            os.makedirs(csv_dir)
        combined2.to_csv(os.path.join(csv_dir, "combined_models_line_filtered.csv"), index=False)
        fig = px.line(
            combined2,
            x='input_genes', y='mean_matched',
            color='model', markers=True,
            hover_data=['mean_time_sec'],
            labels={
                'input_genes': 'Number of input genes',
                'mean_matched': 'Mean number of non-hallucinated genes',
                'model':       'Model'
            },
            title="Mean number of non-hallucinated genes vs. input gene count across all models",
            width=1920, height=1080
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
            showgrid=True, gridcolor='lightgrey', gridwidth=1
        )
        fig.update_yaxes(
            title_font=dict(size=25),
            tickfont=dict(size=22),
            showgrid=True, gridcolor='lightgrey', gridwidth=1
        )
        fig.write_html(out_dir / "combined_models_line_filtered.html")

    # single boxplot of hallucination % per model
    if hall_data:
        hall_df = pd.concat(hall_data, ignore_index=True)
        fig = px.box(
            hall_df,
            x='model', y='hallucination_perc',
            points='all', color='model',
            custom_data=['filename'],
            labels={'model': 'Model', 'hallucination_perc': 'Percentage of hallucinated genes (%)'},
            title="Distribution of hallucinated gene percentages across models",
            template="plotly_white",
            width=1920, height=1080
        )
        fig.update_traces(
            pointpos=0, jitter=0.3,
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
            showgrid=True, gridcolor='lightgrey', gridwidth=1
        )
        fig.write_html(out_dir / "hallucination_boxplot_per_model.html")

    # configurations_ran: only subdirs starting with "with" and ending ".txt"
    cfg_base = Path(r"output/test_files/configurations ran")
    if cfg_base.exists():
        records = []
        for cfg in sorted(cfg_base.iterdir()):
            if not cfg.is_dir():
                continue
            name = cfg.name
            if not (name.startswith("With")):
                continue
            for f in cfg.glob("*.txt"):
                ug = len(parse_genes_from_file(f))
                records.append({
                    'configuration': name,
                    'unique_genes': ug,
                    'filename': f.name
                })
        if records:
            df_cfg = pd.DataFrame(records)
            order = (
                df_cfg.groupby('configuration')['unique_genes'].median().sort_values(ascending=True).index.tolist()
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
                width=1920, height=1080
            )
            fig.update_traces(
                pointpos=0, jitter=0.3,
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
                showgrid=True, gridcolor='lightgrey', gridwidth=1
            )
            fig.write_html(out_dir / "configurations_ran_boxplot.html")


if __name__ == '__main__':
    main()
