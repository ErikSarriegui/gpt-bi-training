from transformers import GPT2TokenizerFast, GPT2Config, GPT2LMHeadModel, Trainer, TrainingArguments, DataCollatorForLanguageModeling

import huggingface_hub

import torch
import os

## -- PROJECT IMPORTS
from callback import PushToHubCallback
from dataset import loadLatxa

def main():
    """
    ==========================
    SET UP ENVIROMENT
    ==========================
    """
    huggingface_hub.login(token = "<token>")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.backends.cudnn.benchmark = True
    
    """
    ==========================
    HYPERPARAMETERS
    ==========================
    """        
    ## -- Model & Hyperparams
    block_size = 1024
    batch_size = 64
    learning_rate = 6e-4
    weight_decay = 0.1
    max_grad_norm = 1.0
    lr_scheduler_type = "cosine"
    warmup_steps = 2000
    ddp_find_unused_parameters = False
    gradient_checkpointing = False
    num_workers = os.cpu_count()

    ## -- Tokenizer
    tokenizer_id = "AuriLab/gpt-bi"
    tokenizer = GPT2TokenizerFast.from_pretrained(tokenizer_id)
    
    """
    ==========================
    MODEL
    ==========================
    """
    config = GPT2Config(
        vocab_size = tokenizer.vocab_size,
        n_positions = block_size,
        n_ctx = block_size,
        n_embd = 768,
        n_layer = 12,
        n_head = 12,
    )

    model = GPT2LMHeadModel(config)
    model.to(device)

    """
    ==========================
    DATASET
    ==========================
    """
    def clean_text(example):
        example["clean_text"] = example["text"]
        return example
    
    def tokenize(example, tokenizer=tokenizer, block_size=block_size):
        tokenized = tokenizer(
            example["clean_text"],
            padding="max_length",
            max_length=block_size,
            truncation=True
        )
        
        tokenized["labels"] = tokenized["input_ids"].copy()
        return tokenized

    train_dataset = loadLatxa(split = "train")
    test_dataset = loadLatxa(split = "test")

    clean_train_dataset = train_dataset.map(clean_text, batched = True, num_proc = num_workers, remove_columns = ["text"])
    clean_test_dataset = test_dataset.map(clean_text, batched = True, num_proc = num_workers, remove_columns = ["text"])
    
    tokenized_train_dataset = clean_train_dataset.map(tokenize, batched = True, num_proc = num_workers, remove_columns = ["clean_text"])
    tokenized_test_dataset = clean_test_dataset.map(tokenize, batched = True, num_proc = num_workers, remove_columns = ["clean_text"])
    
    """
    ==========================
    TRAINING
    ==========================
    """
    ## -- Calculate steps
    model_n_params = sum(p.numel() for p in model.parameters())
    ideal_n_tokens = model_n_params * 20
    max_steps = (ideal_n_tokens // (batch_size * 1024)) * 1.07

    world_size = int(os.environ.get("WORLD_SIZE", 1))
    assert batch_size % world_size == 0, f"Batch size {batch_size} no es divisible por el número de GPUs {world_size}"

    training_args = TrainingArguments(
        output_dir = "./gpt2-pretrained",
        overwrite_output_dir = True,
                
        # Parámetros de entrenamiento principales
        max_steps = int(round(max_steps)),
        per_device_train_batch_size = int(batch_size / world_size),
                
        # Optimizador y learning rate
        learning_rate = learning_rate,
        weight_decay = weight_decay,
        max_grad_norm = max_grad_norm,
        lr_scheduler_type = lr_scheduler_type,
        warmup_steps = warmup_steps,
                
        # Precisión y optimización
        fp16 = True, # bf16 en A100?
                
        # Checkpoints y logging
        save_strategy = "steps",
        save_steps = int(round(max_steps / 20)),
        logging_strategy = "steps",
        logging_steps = int(round(max_steps / 20)),
        eval_strategy="steps",
                
        # Otros parámetros
        ddp_find_unused_parameters = ddp_find_unused_parameters,
        dataloader_num_workers = num_workers,
        report_to = "none",
                
        # Optimizaciones de memoria
        gradient_checkpointing = gradient_checkpointing,
        optim = "adamw_torch"
    )
            
            
    data_collator = DataCollatorForLanguageModeling(
        tokenizer = tokenizer,
        mlm = False,
        pad_to_multiple_of = 8
    )
            
    trainer = Trainer(
        model = model,
        args = training_args,
        train_dataset = tokenized_train_dataset,
        eval_dataset = tokenized_test_dataset,
        data_collator = data_collator,
        callbacks = [PushToHubCallback()]
    )
        
    """
    ==========================
    TRAIN
    ==========================
    """
    print(f"[INFO] Iniciando el entrenamiento en {device}")
    if device == "cuda":
        print(f"[INFO] El entrenamiento se realizará con {world_size} GPUs")
        
    trainer.train()
    model.push_to_hub("gpt-bi-erik", organization="AuriLab")

if __name__ == "__main__":
    main()