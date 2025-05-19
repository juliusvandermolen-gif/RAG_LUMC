import os
import glob
import re
import time
import json
import markdown
from RAG_workflow import query_open_ai
from pymed import PubMed
import pymed
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI as DeepSeekClient

deepseek_client = DeepSeekClient(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                 base_url="https://api.deepseek.com")


def read_latest_llm_output(answer_dir="./output/test_files"):
    answer_files = glob.glob(os.path.join(answer_dir, '*.txt'))
    if not answer_files:
        raise FileNotFoundError("No answer files found in the directory.")
    latest_file = max(answer_files, key=os.path.getmtime)
    with open(latest_file, 'r', encoding="utf8") as f:
        content = f.read().strip()
    return content, latest_file


def extract_pathways(answer_text):
    pathways = []
    pathway_dict = {}
    lines = [line.strip() for line in answer_text.splitlines() if line.strip()]
    it = iter(lines)
    for line in it:
        if line.endswith(":"):
            pathway = line[:-1].strip()
            pathways.append(pathway)
            genes_line = next(it, "")
            genes = [gene.strip() for gene in genes_line.split(",") if
                     gene.strip()]
            pathway_dict[pathway] = genes
    return pathways, pathway_dict


def validate_pathways(gpt_answer, ground_truth, instruction, validation_model):
    _, pathway_dict = extract_pathways(gpt_answer)
    prompt = (
        "Based on the identified pathways, confirm whether they match the ground truth pathways. "
        "Additionally, indicate if there are any novel pathways not present in the ground truth. "
        "User provided pathways:\n"
        f"{', '.join(pathway_dict.keys())}\n\n"
        "Ground truth pathways:\n"
        f"{ground_truth}"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": prompt}
    ]
    answer = query_open_ai(messages, instruction, prompt, save=False, model=validation_model, range_query=1)
    return answer


def academic_validation(pathways, pathway_dict, academic_instruction,
                        validation_model):
    academic_results = []
    for pathway in pathways:
        print(f"Validating pathway: {pathway}")
        genes = pathway_dict[pathway]
        prompt = (
            f"For the biological pathway '{pathway}', validate the involvement of the genes: {', '.join(genes)}. "
            "Check academic databases such as PubMed, Google Scholar, and GeneCards."
        )
        messages = [
            {"role": "system", "content": academic_instruction},
            {"role": "user", "content": prompt}
        ]
        response = query_open_ai(messages, academic_instruction, prompt,
                                 save=False, model=validation_model, range_query=1,
                                 )
        if response is None:
            response = "No academic validation response returned."
        academic_results.append((pathway, genes, response.strip()))
    return academic_results


# Initialize PubMed (used in the replace_entry function)
pubmed = PubMed(tool="MyTool", email="my@email.address")

# Global counters for statistics.
total_matches = 0
credible_matches = 0

# Updated regex pattern: accepts titles enclosed in either asterisks or double quotes,
# and allows an optional colon after the bibliographic entry.
pattern = re.compile(
    r'(\d{4}),\s+([^,]+),\s+\\?"(.*?)"\\?,\s+([^<]+)(<br>.*)',
    re.MULTILINE
)


def replace_entry(match):
    global total_matches, credible_matches
    total_matches += 1

    # Extract the bibliographic fields.
    year = match.group(1).strip()
    authors = match.group(2).strip()
    original_title = match.group(3).strip()
    journal = match.group(4).strip()
    orig_summary = match.group(5).strip()
    citation = f"({year}, {authors}, \"{original_title}\", {journal})"
    print(f"Querying PubMed for title: {original_title}")
    try:
        results = pubmed.query(original_title, max_results=5)
    except Exception as e:
        print("PubMed query error:", e)
        results = []
    new_title = None

    for article in results:
        if isinstance(article, pymed.article.PubMedArticle) and article.title:
            new_title = article.title.strip()
            break
    if new_title is None:
        print("Could not find title for article.")
        citation = ("The AI hallucinated and didnt manage to find a credible source. Here is the original title: " +
                    citation)
    else:
        credible_matches += 1
    time.sleep(1)
    # Reconstruct the bibliographic entry with the updated title while preserving year, authors, and journal.
    print(credible_matches)
    return f'{citation}{orig_summary}"'


def main():
    answer_dir = "./output/test_files/"
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./configs_system_instruction/system_instruction_comparison_pathways.txt"
    academic_instruction_file = "./configs_system_instruction/system_instruction_academic_validation_test.txt"
    output_directory = "./output/text_files/automated_comparison"
    os.makedirs(output_directory, exist_ok=True)

    with open(ground_truth_file, 'r', encoding="utf8") as f:
        ground_truth = f.read().strip()
    with open(system_instruction_file, 'r', encoding="utf8") as f:
        comparison_instruction = f.read().strip()

    if os.path.exists(academic_instruction_file):
        with open(academic_instruction_file, 'r', encoding="utf8") as f:
            academic_instruction = f.read().strip()
    else:
        print("Academic instruction file not found. Exiting program")
        import sys
        sys.exit(1)
    try:
        llm_output, latest_file = read_latest_llm_output(answer_dir)
    except FileNotFoundError as e:
        print(e)
        return
    model = "o3"
    validation_model = model


    # 1) do your comparisons and extractions
    print("Validating pathways... using g:Profiler")
    comparison_summary = validate_pathways(llm_output, ground_truth,
                                           comparison_instruction, validation_model=validation_model)
    pathways, pathway_dict = extract_pathways(llm_output)
    print("Validating pathways... using literature support")
    academic_results = academic_validation(
        pathways, pathway_dict,
        academic_instruction,
        validation_model=validation_model
    )

    # 2) build a base_name and then append the run index
    base_name = os.path.splitext(os.path.basename(latest_file))[0]
    md_filename = os.path.join(
        output_directory,
        f"validation_{base_name}_{validation_model}.md"
    )

    # 3) preprocess summaries
    processed_results = []
    for pathway, genes, summary in academic_results:
        new_summary = pattern.sub(replace_entry, summary)
        processed_results.append((pathway, genes, new_summary))

    # 4) write out the report
    with open(md_filename, 'w', encoding="utf8") as md:
        # Header
        md.write(f"# Pathway Validation Report for {base_name}\n\n")

        # Table of Contents
        toc = [
            ("Credible sources found", "#credible-sources-found"),
            ("Original genes / pathways", "#original-genes--pathways"),
            ("Automated validation of pathways", "#automated-validation-of-pathways"),
            ("g:Profiler comparison summary", "#gprofiler-comparison-summary")
        ]
        for title, anchor in toc:
            md.write(f"- [{title}]({anchor})\n")
        md.write("\n")

        # 1) Credible Sources
        if total_matches > 0:
            percent_credible = (credible_matches / total_matches) * 100
        else:
            percent_credible = 0.0
        md.write("## Credible sources found\n")
        md.write(
            f"**{percent_credible:.1f}% credible matches "
            f"({credible_matches} out of {total_matches})**\n\n"
        )

        # 2) Original genes / pathways
        md.write("## Original genes / pathways\n")
        for pathway, genes in pathway_dict.items():
            md.write(f"- **{pathway}**: {', '.join(genes)}\n")
        md.write("\n")

        # 3) Automated validation of pathways
        md.write("## Automated validation of pathways\n")
        for pathway, genes, new_summary in processed_results:
            md.write(f"### {pathway}\n")
            md.write(f"**Genes involved:** {', '.join(genes)}\n\n")
            md.write(f"{new_summary}\n\n")

        # 4) g:Profiler comparison summary
        md.write("## g:Profiler comparison summary\n")
        md.write(f"{comparison_summary}\n\n")

    print(f"Markdown validation report created: {md_filename}")




if __name__ == "__main__":
    main()
