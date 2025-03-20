from openai import OpenAI
import pandas as pd
import gzip
import csv
import os
import time
import json
from RAG_workflow import query_open_ai
import re
import subprocess
import markdown
from xhtml2pdf import pisa
import pdfkit
client_open_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def open_file(gpt_answer_file, ground_truth_file, messages_file):
    with open(gpt_answer_file, 'r') as f:
        gpt_answer = f.read().strip()
    with open(ground_truth_file, 'r') as f:
        ground_truth = f.read().strip()
    with open(messages_file, 'r') as f:
        instruction = f.read().strip()
    return gpt_answer, ground_truth, instruction

def extract_pathways(answer_text):
    """
    Extracts pathway names and their associated gene lists from the given answer text.

    This function assumes that each pathway is represented by a line ending with a colon,
    and that immediately following that line (or later in the block) is a line starting with
    "Genes involved:" listing the genes (comma-separated). It skips header lines such as those
    starting with "Answer".

    Returns:
        A tuple:
          - gpt_pathways: a list of pathway names (as they appear).
          - pathway_dict: a dictionary mapping each pathway name to a list of genes.
    """
    gpt_pathways = []
    pathway_dict = {}
    lines = answer_text.splitlines()
    current_pathway = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip header lines.
        if line.lower().startswith("answer"):
            continue
        # Check if this line is a "Genes involved:" line.
        if line.lower().startswith("genes involved"):
            if current_pathway is not None:
                gene_part = line[len("Genes involved:"):].strip()
                genes = [g.strip() for g in gene_part.split(",") if g.strip()]
                pathway_dict[current_pathway] = genes
            continue
        # If the line ends with a colon, consider it as a pathway name.
        if line.endswith(":"):
            pathway_name = line[:-1].strip()
            current_pathway = pathway_name
            # Avoid duplicates.
            if pathway_name not in gpt_pathways:
                gpt_pathways.append(pathway_name)
                pathway_dict[pathway_name] = []
    return gpt_pathways, pathway_dict

def validate(gpt_answer, ground_truth, instruction):
    """
    Validates the GPT answer by comparing only the pathway names (gpt_pathways)
    against the ground truth. Then, at the end of the response, appends the full list
    of pathways with their genes (from the GPT output). (The complete ground truth is not appended.)
    """
    # Extract both the pathway names and the full pathway-to-genes mapping.
    gpt_pathways, pathway_dict = extract_pathways(gpt_answer)

    # Build the prompt using only the pathway names.
    prompt = (
        "Based on the identified pathways, confirm whether they match the ground truth pathways. "
        "Additionally, indicate if there are any novel pathways not present in the ground truth. "
        "User provided pathways:\n"
        f"{', '.join(gpt_pathways)}\n\n"
        "Ground truth pathways:\n"
        f"{ground_truth}"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": prompt}
    ]
    save = False
    answer = query_open_ai(messages, instruction, prompt, save, range_query=2)

    # Format the full pathway details (pathway with genes) for appending.
    detailed_lines = ["\n\nFull pathway list with genes:"]
    for pathway, genes in pathway_dict.items():
        genes_str = ", ".join(genes) if genes else "No genes listed"
        detailed_lines.append(f"{pathway}: {genes_str}")
    detailed_info = "\n".join(detailed_lines)

    final_answer = answer + "\n" + detailed_info
    return final_answer



def main():
    answer_dir = r"./supporting scripts/validation/test_answers"
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./configs_system_instruction/system_instruction_comparison_pathways.txt"

    with open(ground_truth_file, 'r', encoding="utf8") as f:
        ground_truth = f.read().strip()
    with open(system_instruction_file, 'r', encoding="utf8") as f:
        instruction = f.read().strip()

    output_directory = "./output/text_files/automated_comparison"
    os.makedirs(output_directory, exist_ok=True)

    answer_files = [
        os.path.join(answer_dir, f) for f in os.listdir(answer_dir) if f.endswith('.txt')
    ]
    if not answer_files:
        print("No answer files found.")
        return

    last_file = max(answer_files, key=os.path.getmtime)
    with open(last_file, 'r', encoding="utf8") as f:
        gpt_answer = f.read().strip()

    validation_answer = validate(gpt_answer, ground_truth, instruction)

    base_name = os.path.basename(last_file)
    md_filename = os.path.join(output_directory, f"validation_{base_name}".replace(".txt", ".md"))
    with open(md_filename, 'w', encoding="utf8") as f:
        f.write(validation_answer)

    print(f"Markdown file generated: {md_filename}")

if __name__ == "__main__":
    main()

