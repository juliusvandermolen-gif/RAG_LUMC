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


def validate(gpt_answer, ground_truth, instruction):
    prompt = (
        "Based on my found pathways, confirm whether they are there by validating them using the ground truth. "
        "Furthermore, check whether the pathways might be novel or not interesting. The pathways provided by the "
        "user:\n"
        f"{gpt_answer}\n\n"
        "The ground truth based on IPA and gProfiler:\n"
        f"{ground_truth}"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": prompt}
    ]
    print(messages)
    answer = query_open_ai(messages, instruction, prompt)

    return answer


def main():
    gpt_answer_file = "./output/text_files/answer.txt"
    ground_truth_file = "./output/text_files/ground_truth_pathways.txt"
    system_instruction_file = "./output/text_files/system_instruction.txt"
    gpt_answer, ground_truth, instruction = open_file(gpt_answer_file, ground_truth_file, system_instruction_file)
    validation_answer = validate(gpt_answer, ground_truth, instruction)
    #print(validation_answer)


if __name__ == "__main__":
    main()
