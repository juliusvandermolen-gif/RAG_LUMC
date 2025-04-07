import os
import glob
import re
import time
import json
import markdown
from RAG_workflow import query_open_ai

# Load environment variables (e.g., DEEPSEEK_API_KEY)
from dotenv import load_dotenv

load_dotenv()

# Set up the DeepSeek client using the OpenAI library interface.
# Note: The DeepSeek reasoning model is "deepseek-reasoner".
from openai import OpenAI as DeepSeekClient

deepseek_client = DeepSeekClient(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")


def query_deepseek(messages, model):
    """
    Helper function to query the DeepSeek API using the specified model.
    It takes only the messages and model parameters.
    """
    response = deepseek_client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False
    )
    return response.choices[0].message.content


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
    Expects the text to have a pathway line ending with ':' followed by a line of comma-separated genes.

    Returns:
        - pathways: list of pathway names in the order they appear.
        - pathway_dict: dictionary mapping each pathway name to its list of genes.
    """
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


def validate_pathways(gpt_answer, ground_truth, instruction):
    """
    Validates the GPT answer by comparing the extracted pathway names against
    the ground truth. It builds a prompt that includes both pathway sets and queries
    the model via query_open_ai.
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
    answer = query_open_ai(messages, instruction, prompt, save=False, range_query=2)
    return answer


def academic_validation(pathways, pathway_dict, academic_instruction):
    """
    For each pathway, queries academic literature via the DeepSeek API (using the deepseek-reasoner model)
    to validate whether the involvement of the listed genes is supported by evidence from academic databases.

    Returns:
        A list of tuples (pathway, genes, academic_summary)
    """
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
        response = query_deepseek(messages, model="deepseek-reasoner")
        if response is None:
            response = "No academic validation response returned."
        academic_results.append((pathway, genes, response.strip()))
    return academic_results


def main():
    # Paths for necessary files and directories
    answer_dir = "./output/test_files"
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./configs_system_instruction/system_instruction_comparison_pathways.txt"
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
        print("Academic instruction file not found. Exiting program")
        sys.exit(1)
    try:
        llm_output, latest_file = read_latest_llm_output(answer_dir)
    except FileNotFoundError as e:
        print(e)
        return

    # Step 2: g:Profiler Comparison Validation using OpenAI
    comparison_summary = validate_pathways(llm_output, ground_truth, comparison_instruction)

    # Step 3: Extract pathways and perform Academic Validation using DeepSeek
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
            # Remove markdown fences
            cleaned_summary = re.sub(r'```markdown\s*|\s*```', '', summary.strip())
            md.write(f"{cleaned_summary}\n\n")

    print(f"Markdown validation report created: {md_filename}")


if __name__ == "__main__":
    main()
