import os
import glob
import re
import time
import json
import markdown
from RAG_workflow import query_llm
from pymed import PubMed
import pymed
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI as DeepSeekClient

deepseek_client = DeepSeekClient(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                 base_url="https://api.deepseek.com")

log_dir = './logs'
log_file = os.path.join(log_dir, 'validation_logs.json')
os.makedirs(log_dir, exist_ok=True)
try:
    with open(log_file, 'r', encoding='utf8') as f:
        validation_logs = json.load(f)
    if isinstance(validation_logs, list):
        validation_logs = dict(validation_logs)

except FileNotFoundError:
    validation_logs = {}


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
            genes = [gene.strip() for gene in genes_line.split(",") if gene.strip()]
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
                                 save=False, model=validation_model, range_query=1)
        if response is None:
            response = "No academic validation response returned."
        academic_results.append((pathway, genes, response.strip()))
    return academic_results

pubmed = PubMed(tool="MyTool", email="my@email.address")

total_matches = 0
credible_matches = 0

pattern = re.compile(
    r'(\d{4}),\s+([^,]+),\s+\\?"(.*?)"\\?,\s+([^<]+)(<br>.*)',
    re.MULTILINE
)


def replace_entry(match):
    global total_matches, credible_matches, validation_logs
    total_matches += 1

    year = match.group(1).strip()
    authors = match.group(2).strip()
    original_title = match.group(3).strip()
    journal = match.group(4).strip()
    orig_summary = match.group(5).strip()
    citation_str = f"({year}, {authors}, \"{original_title}\", {journal})"

    key = original_title

    if key in validation_logs:
        entry = validation_logs[key]
        entry['count'] += 1
        is_real = entry['is_real']
        citation_str = entry.get('citation', citation_str)
        print(f"Using cached status for: {original_title} -> is_real={is_real}")
    else:
        print(f"Querying PubMed for title: {original_title}")
        try:
            results = pubmed.query(original_title, max_results=5)
        except Exception as e:
            print("PubMed query error:", e)
            results = []

        is_real = any(isinstance(article, pymed.article.PubMedArticle) and article.title for article in results)
        if not is_real:
            print("The citation doesn't match any pubmed articles.")
        validation_logs[key] = {'citation': citation_str, 'count': 1, 'is_real': is_real}

    sorted_entries = sorted(validation_logs.items(), key=lambda item: item[1]['count'], reverse=True)
    with open(log_file, 'w', encoding='utf8') as logf:
        json.dump(sorted_entries, logf, indent=2)

    if is_real:
        styled = f'<span style="color:green;">{citation_str}</span>'
        credible_matches += 1
    else:
        styled = f'<span style="color:red;">{citation_str}</span>'

    time.sleep(1)
    return f'{styled}{orig_summary}'


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

    model = "grok-3-latest"
    validation_model = model
    for i in range(2):
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

        base_name = os.path.splitext(os.path.basename(latest_file))[0]
        md_filename = os.path.join(
            output_directory,
            f"validation_{base_name}_{validation_model}_{i}.md"
        )

        processed_results = []
        for pathway, genes, summary in academic_results:
            new_summary = pattern.sub(replace_entry, summary)
            processed_results.append((pathway, genes, new_summary))

        with open(md_filename, 'w', encoding="utf8") as md:
            md.write(f"# Pathway Validation Report for {base_name}\n\n")
            toc = [
                ("Credible sources found", "#credible-sources-found"),
                ("Original genes / pathways", "#original-genes--pathways"),
                ("Automated validation of pathways", "#automated-validation-of-pathways"),
                ("g:Profiler comparison summary", "#gprofiler-comparison-summary")
            ]
            for title, anchor in toc:
                md.write(f"- [{title}]({anchor})\n")
            md.write("\n")

            percent_credible = (credible_matches / total_matches * 100) if total_matches else 0.0
            md.write("## Credible sources found\n")
            md.write(
                f"**{percent_credible:.1f}% credible matches ({credible_matches} out of {total_matches})**\n\n"
            )

            md.write("## Original genes / pathways\n")
            for pathway, genes in pathway_dict.items():
                md.write(f"- **{pathway}**: {', '.join(genes)}\n")
            md.write("\n")

            md.write("## Automated validation of pathways\n")
            for pathway, genes, new_summary in processed_results:
                md.write(f"### {pathway}\n")
                md.write(f"**Genes involved:** {', '.join(genes)}\n\n")
                md.write(f"{new_summary}\n\n")

            md.write("## g:Profiler comparison summary\n")
            md.write(f"{comparison_summary}\n\n")

        print(f"Markdown validation report created: {md_filename}")


if __name__ == "__main__":
    main()