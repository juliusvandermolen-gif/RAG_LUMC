from openai import OpenAI
import pandas as pd
import gzip
import csv
import os
import time
import json
from RAG_workflow import query_open_ai

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
    Extracts pathway names from the given answer text.
    Assumes that pathway names are on lines ending with ':' (excluding header lines
    like "Answer:" and lines starting with "Genes involved").
    """
    pathways = []
    for line in answer_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip header and gene listing lines.
        if line.lower().startswith("answer"):
            continue
        if line.lower().startswith("genes involved"):
            continue
        if line.endswith(":"):
            # Remove the trailing colon.
            pathways.append(line[:-1].strip())
    return pathways


def validate(gpt_answer, ground_truth, instruction):
    # Extract only the pathway names from the GPT answer.
    gpt_pathways = extract_pathways(gpt_answer)

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
    answer = query_open_ai(messages, instruction, prompt, save)
    return answer


def main():
    answer_dir = r".\supporting scripts\validation\test_answers"  # Directory containing GPT answer files
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./output/text_files/system_instruction.txt"

    # Read ground truth and system instruction once since they're reused for each answer file.
    with open(ground_truth_file, 'r', encoding="utf8") as f:
        ground_truth = f.read().strip()
    with open(system_instruction_file, 'r', encoding="utf8") as f:
        instruction = f.read().strip()

    output_directory = "./output/text_files/automated_comparison"
    os.makedirs(output_directory, exist_ok=True)

    # Process each GPT answer file in the answer_dir
    for filename in os.listdir(answer_dir):
        if not filename.endswith('.txt'):
            continue
        gpt_answer_file = os.path.join(answer_dir, filename)
        with open(gpt_answer_file, 'r', encoding="utf8") as f:
            gpt_answer = f.read().strip()
        validation_answer = validate(gpt_answer, ground_truth, instruction)
        # Save the validation answer with the new file name: validation_{original_file_name}
        validation_file = os.path.join(output_directory, f"validation_{filename}")
        with open(validation_file, 'w', encoding="utf8") as f:
            f.write(validation_answer)


if __name__ == "__main__":
    main()
