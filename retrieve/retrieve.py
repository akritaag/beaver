import argparse
from transformers import set_seed
import os
from tqdm import tqdm, trange
import torch
import numpy as np
from typing import Literal
from pathlib import Path

from rerank import Reranker, Pair
from utils import format_table, read_json, write_json, cosine_sim

def embed(texts: list[str], fn, model: str, provider: Literal['local', 'openrouter']):
    if fn is not None and os.path.isfile(fn):
        print(f'embedding computed for {fn}')
        return

    if provider == 'local':
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model, trust_remote_code=True).cuda()
        embeds = model.encode(texts, show_progress_bar=True, batch_size=16)
    elif provider == 'openrouter':
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv()
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
        )

        batch_size = 256

        embeds = []
        for batch_idx in trange(0, len(texts), batch_size, desc="Batches"):
            batch_texts = texts[batch_idx : batch_idx + batch_size]
            response = client.embeddings.create(model=model, input=batch_texts)
            embeddings = [e.embedding for e in response.data]
            embeds += embeddings
        
        embeds = np.array(embeds)
    
    assert len(embeds) == len(texts)

    if fn is not None:
        np.save(fn, embeds)

def retrieve_tables(dataset: str, model, k, provider):
    dataset_dir = Path(f"./data/{dataset}")
    retrieval_dir = Path(f"./data/{dataset}/retrieval")
    retrieval_dir.mkdir(exist_ok=True)

    # embed questions
    qs = read_json(dataset_dir / 'dev_sampled.json')
    qs = [x['question'] for x in qs]
    q_embeds = embed(qs, dataset_dir / 'q_embeds.npy', model, provider)
    
    # embed tables
    corpus_tables = read_json(dataset_dir / 'dev_tables.json')
    trim = dataset in ['sp', 'nova']
    print(f'trim tables: {trim}')
    ts = [format_table(t, corpus_tables, use_instance=False, trim=trim) for t in corpus_tables]
    t_embeds = embed(ts,  retrieval_dir / 't_embeds.npy', model, provider)

    sim_scores = cosine_sim(q_embeds, t_embeds)
    print(sim_scores.shape)

    # TODO: write to JSON (because someone might not want to do reranking)
    # rerank based on the top-50 tables (dict from q_id --> tables, same format as reranked_tables.json)
    top_k_indices = torch.topk(sim_scores, k=k, dim=1).indices
    top_k_tables = [[corpus_tables[i] for i in q_indices] for q_indices in top_k_indices]
    write_json(top_k_tables, retrieval_dir / 'retrieved_tables.json')

def rerank_tables(dataset: str, model, k):
    qs = read_json(f"./data/beaver/{dataset}/dev_sampled.json")
    corpus_tables = read_json(f'./data/beaver/{dataset}/dev_tables.json')
    trim = dataset in ['sp', 'nova']
    print(f'trim tables: {trim}')
    formatted_tables = {t: format_table(t, corpus_tables, use_instance=False, trim=trim) for t in corpus_tables}
    
    reranker = Reranker(model)

    retrieval_dir = Path(f"./data/{dataset}/retrieval")
    retrieval_dir.mkdir(exist_ok=True)
    
    retrieved_tables = read_json(retrieval_dir / 'retrieved_tables.json')
    reranked_tables = {}

    save_fn = Path(retrieval_dir) / f"reranked_tables.json"
    if os.path.isfile(save_fn):
        reranked_tables = read_json(save_fn)

    for q in tqdm(qs):
        q_id = q['id']
        if q_id in reranked_tables:
            continue

        pairs = []

        for pred_table in retrieved_tables[q_id]:
            pairs.append(Pair(q['question'], formatted_tables[pred_table]))

        rerank_scores = []
        batch_size = 16
        for batch_idx in trange(0, len(pairs), batch_size, desc="Reranking"):
            batch_pairs = pairs[batch_idx : batch_idx + batch_size]
            rerank_scores.append(reranker.rerank(batch_pairs))
        rerank_scores = torch.cat(rerank_scores)

        # re-rank scores
        top_table_idxs = torch.sort(rerank_scores, descending=True).indices
        top_tables = [retrieved_tables[i] for i in top_table_idxs]
        reranked_tables[q_id] = top_tables[:k]
    
        write_json(reranked_tables, save_fn)

def main():
    set_seed(1234)

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str)
    parser.add_argument("--embed_model", type=str, default="Qwen/Qwen3-Embedding-8B")
    parser.add_argument("--embed_k", type=int, default=50)
    parser.add_argument("--provider", choices=['local', 'openrouter'])
    parser.add_argument("--rerank_model", type=str, default="Qwen/Qwen3-Reranker-8B")
    parser.add_argument("--rerank_k", type=int, default=15)
    args = parser.parse_args()

    print(f'Retrieving {args.embed_k} tables using {args.rerank_model}')
    retrieve_tables(args.dataset, args.embed_model, args.embed_k, args.provider)

    if args.rerank_model:
        print(f'Reranking to {args.rerank_k} tables using {args.rerank_model}')
        rerank_tables(args.dataset, args.rerank_model, args.rerank_k)

if __name__ == "__main__":
    main()