import os
import glob
import re
import time
import json
import markdown

from RAG_workflow import query_open_ai


def read_latest_llm_output(answer_dir="./output/test_files"):
    """
    Reads the most recent .txt file from the answer directory.
    """
    answer_files = glob.glob(os.path.join(answer_dir, '*.txt'))
    if not answer_files:
        raise FileNotFoundError("No answer files found in the directory.")
    latest_file = max(answer_files, key=os.path.getmtime)
    with open(latest_file, 'r', encoding="utf8") as f:
        content = f.read().strip()
    return content, latest_file


def extract_pathways(answer_text):
    """
    Extracts pathway names and their associated gene lists from the answer text.
    Expects each pathway line to end with a colon and a following line starting
    with 'Genes involved:' to list the genes.

    Returns:
        - gpt_pathways: list of pathway names in the order they appear.
        - pathway_dict: dictionary mapping each pathway name to a list of genes.
    """
    gpt_pathways = []
    pathway_dict = {}
    lines = answer_text.splitlines()
    current_pathway = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("answer"):
            continue
        if line.lower().startswith("genes involved"):
            if current_pathway is not None:
                gene_part = line[len("Genes involved:"):].strip()
                genes = [g.strip() for g in gene_part.split(",") if g.strip()]
                pathway_dict[current_pathway] = genes
            continue
        if line.endswith(":"):
            pathway_name = line[:-1].strip()
            current_pathway = pathway_name
            if pathway_name not in gpt_pathways:
                gpt_pathways.append(pathway_name)
                pathway_dict[pathway_name] = []
    return gpt_pathways, pathway_dict


def validate_pathways(gpt_answer, ground_truth, instruction):
    """
    Validates the GPT answer by comparing the extracted pathway names against
    the ground truth. It builds a prompt that includes both pathway sets and queries
    the model via query_open_ai. The full list of pathways with genes is appended to
    the response.
    """
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
    save = False
    # Use a range_query of 2 so one attempt is made.
    answer = query_open_ai(messages, instruction, prompt, save, range_query=2)
    final_answer = answer
    return final_answer


def academic_validation(pathways, pathway_dict, academic_instruction):
    """
    For each pathway, queries academic literature via query_open_ai (using ChatGPT's web search)
    to validate whether the involvement of the listed genes is supported by evidence from
    academic databases (e.g., PubMed, Google Scholar, GeneCards). The prompt requests
    summaries per gene with citations (DOIs or URLs).

    Returns:
        A list of tuples (pathway, genes, academic_summary)
    """
    academic_results = []
    for pathway in pathways:
        genes = pathway_dict[pathway]
        prompt = (
            f"For the biological pathway '{pathway}', validate the involvement of the genes: {', '.join(genes)}. "
            "Check academic databases such as PubMed, Google Scholar, and GeneCards. "
            "Summarize evidence per gene with citations (DOIs or URLs). Explicitly state if no evidence is found for a gene."
        )
        messages = [
            {"role": "system", "content": academic_instruction},
            {"role": "user", "content": prompt}
        ]
        # Use the 4o-mini-search-preview model with web search options (e.g., medium context)
        response = query_open_ai(
            messages,
            academic_instruction,
            prompt,
            save=False,
            range_query=2,
            model="gpt-4o-mini-search-preview",
            web_search_options={"search_context_size": "medium"}
        )
        if response is None:
            response = "No academic validation response returned."
        academic_results.append((pathway, genes, response.strip()))
    return academic_results


def main():
    # Paths for necessary files and directories
    answer_dir = "./output/test_files"
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./configs_system_instruction/system_instruction_comparison_pathways.txt"
    # New academic instruction file (if available)
    academic_instruction_file = "./configs_system_instruction/system_instruction_academic_validation.txt"
    output_directory = "./output/text_files/automated_comparison"
    os.makedirs(output_directory, exist_ok=True)

    # Load ground truth and system instruction texts
    with open(ground_truth_file, 'r', encoding="utf8") as f:
        ground_truth = f.read().strip()
    with open(system_instruction_file, 'r', encoding="utf8") as f:
        comparison_instruction = f.read().strip()

    # Load academic instruction if file exists, else use a default instruction.
    if os.path.exists(academic_instruction_file):
        with open(academic_instruction_file, 'r', encoding="utf8") as f:
            academic_instruction = f.read().strip()
    else:
        academic_instruction = (
            "You are an academic literature expert. Evaluate gene involvement in biological pathways "
            "by consulting academic databases and literature. Provide evidence with citations (DOIs or URLs) "
            "for each gene. Be precise and clear in your explanation."
        )

    # Step 1: Read the latest LLM output
    try:
        llm_output, latest_file = read_latest_llm_output(answer_dir)
    except FileNotFoundError as e:
        print(e)
        return

    # Step 2: g:Profiler Comparison Validation
    comparison_summary = validate_pathways(llm_output, ground_truth, comparison_instruction)

    # Step 3: Extract pathways and perform Academic Validation
    pathways, pathway_dict = extract_pathways(llm_output)
    academic_results = academic_validation(pathways, pathway_dict, academic_instruction)

    # Step 4: Combine into a Markdown report
    base_name = os.path.splitext(os.path.basename(latest_file))[0]
    md_filename = os.path.join(output_directory, f"validation_{base_name}.md")

    with open(md_filename, 'w', encoding="utf8") as md:
        md.write(f"# Pathway Validation Report for {base_name}\n\n")
        md.write("## g:Profiler Comparison Summary\n")
        md.write(f"{comparison_summary}\n\n")
        md.write("## Academic Validation of Pathways\n")
        for pathway, genes, summary in academic_results:
            md.write(f"### {pathway}\n")
            md.write(f"**Genes involved:** {', '.join(genes)}\n\n")
            md.write(f"{summary}\n\n")

    print(f"Markdown validation report created: {md_filename}")


if __name__ == "__main__":
    main()
