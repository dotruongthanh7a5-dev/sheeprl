from dataclasses import asdict
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tqdm import tqdm
from transformers import AutoTokenizer, PreTrainedTokenizer
from sheeprl.algos.rlhf.args import OPT, TextDataArgs

from sheeprl.utils.parser import HfArgumentParser
import json

import torch
from datasets import load_dataset

# support running without installing as a package
wd = Path(__file__).parent.parent.resolve()
sys.path.append(str(wd))


def prepare(
    destination_dir: str,
    tokenizer_name: str,
    stage: str = "sft",
    max_length: int = 512,
    max_prompt_length: int = 512,
    mask_prompt: bool = True,
    num_samples: Optional[int] = None,
    ignore_index: int = -1,
    remove_same_responses: bool = True,
    remove_same_prompts: bool = True,
    minimum_response_length: int = 5,
) -> None:
    destination_dir = Path(destination_dir)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
    if not hasattr(tokenizer, "pad_token") or tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    os.makedirs(destination_dir, exist_ok=True)
    cache_dir = destination_dir / "cache"
    for split in ["train", "test"]:
        print(f"Processing {split} split ...")
        dataset = load_dataset("Dahoas/static-hh", split=split, cache_dir=cache_dir)
        if stage == "finetune":
            # first half of the dataset
            dataset = dataset.select(range(len(dataset) // 2))
        elif stage == "preference":
            # second half of the dataset
            dataset = dataset.select(range(len(dataset) // 2, len(dataset)))
        else:
            raise ValueError(f"stage must be one of 'finetune', 'preference', but got {stage}")

        if num_samples is not None:
            dataset = dataset.select(range(num_samples))
        samples = []
        hashes = []
        skipped = 0
        for sample in tqdm(dataset):
            output = {}
            prompt = sample["prompt"]
            prompt_hash = hash(prompt)
            chosen = sample["chosen"]
            rejected = sample["rejected"]
            if remove_same_prompts and prompt_hash in hashes:
                skipped += 1
                continue
            encoded_prompt = tokenizer(
                prompt,
                padding=False,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            num_prompt_input_ids = len(encoded_prompt["input_ids"].squeeze())
            output["prompt_len"] = num_prompt_input_ids
            if num_prompt_input_ids > max_prompt_length:
                skipped += 1
                continue
            if stage == "finetune":
                # we use prompt and choosen as data
                if len(chosen) < minimum_response_length:
                    skipped += 1
                    continue
                prompt_response = prompt + chosen + tokenizer.eos_token
                encoded_prompt_response = tokenizer(
                    prompt_response,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                )
                input_ids = encoded_prompt_response["input_ids"].squeeze()
                targets = input_ids.clone()
                output["input_ids"] = input_ids
                if mask_prompt:
                    targets[:num_prompt_input_ids] = ignore_index
                output["targets"] = targets
            elif stage == "preference":
                # we need pairs of prompt and choosen and prompt and rejected
                chosen = sample["chosen"]
                rejected = sample["rejected"]
                if len(chosen) < minimum_response_length or len(rejected) < minimum_response_length:
                    skipped += 1
                    continue
                prompt_chosen = sample["prompt"] + sample["chosen"] + tokenizer.eos_token
                encoded_prompt_chosen = tokenizer(
                    prompt_chosen,
                    max_length=max_length,
                    truncation=True,
                    return_tensors="pt",
                )

                prompt_rejected = sample["prompt"] + sample["rejected"] + tokenizer.eos_token
                encoded_prompt_rejected = tokenizer(
                    prompt_rejected,
                    max_length=max_length,
                    truncation=True,
                    return_tensors="pt",
                )
                if remove_same_responses and prompt_chosen == prompt_rejected:
                    skipped += 1
                    continue
                output["chosen_input_ids"] = encoded_prompt_chosen["input_ids"].squeeze()
                output["rejected_input_ids"] = encoded_prompt_rejected["input_ids"].squeeze()
            else:
                raise ValueError(f"stage must be one of 'finetune', 'preference', but got {stage}")
            samples.append(output)
            hashes.append(prompt_hash)
        print(f"Processed {len(samples)} samples, skipped {skipped} samples")
        torch.save(samples, destination_dir / f"{stage}_{split}.pt")

    example_prompt_path = destination_dir / "example_prompt.pt"
    example_prompt = create_example_prompt(tokenizer, max_length=max_length)
    torch.save(example_prompt, example_prompt_path)


def wrap_prompt(prompt: str) -> str:
    return "\n\nHuman: " + prompt + "\n\nAssistant: "


def create_example_prompt(tokenizer: PreTrainedTokenizer, max_length: int ) -> Dict[str, Any]:
    prompt = "How does the computer work?"
    wrapped_prompt = wrap_prompt(prompt)
    encoded_prompt = tokenizer(wrapped_prompt, max_length=max_length, truncation=True, return_tensors="pt")
    output = {
        "prompt": wrapped_prompt,
        "input_ids": encoded_prompt["input_ids"],
        "attention_mask": encoded_prompt["attention_mask"],
    }
    return output


if __name__ == "__main__":
    if len(sys.argv) > 1:
        parser = HfArgumentParser([TextDataArgs])
        dataclasses = parser.parse_args_into_dataclasses()
        data_args: TextDataArgs = dataclasses[0]
    else:
        data_args = TextDataArgs(
            destination_dir="data/Dahoas/static-hh",
            tokenizer_name=OPT().model_name,
            stage="preference",
            max_length=512,
            max_prompt_length=512,
        )
    prepare(**asdict(data_args))
    with open(Path(data_args.destination_dir) / "args.json", "w") as f:
        json.dump(asdict(data_args), f, indent=4)
